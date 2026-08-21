import base64
import io
import hmac
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from urllib.parse import urljoin

import pytesseract
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError

# ------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Configuration (all from environment)
# ------------------------------------------------------------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

TARGET_URL = os.getenv("TARGET_URL", "https://everify.bdris.gov.bd/")
API_KEY = os.getenv("API_KEY", "").strip()
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "10"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
ALLOW_QUERY_API_KEY = os.getenv("ALLOW_QUERY_API_KEY", "true").strip().lower() in {"1", "true", "yes", "on"}
MAX_OCR_IMAGE_BYTES = 512 * 1024  # increased for safety
MAX_OCR_OPERAND_DIGITS = 9
CAPTCHA_MAX_ATTEMPTS = max(1, min(5, int(os.getenv("CAPTCHA_MAX_ATTEMPTS", "3"))))
UPSTREAM_MIN_INTERVAL_SECONDS = max(0.0, float(os.getenv("UPSTREAM_MIN_INTERVAL_SECONDS", "0.8")))
CAPTCHA_RETRY_DELAY_SECONDS = max(0.0, float(os.getenv("CAPTCHA_RETRY_DELAY_SECONDS", "1.5")))
UPSTREAM_BLOCK_RETRY_AFTER_SECONDS = max(5, int(os.getenv("UPSTREAM_BLOCK_RETRY_AFTER_SECONDS", "30")))
USER_AGENT = "BirthVerificationAPI/2.0 (authorized-use-only)"

# Tesseract path – set via environment or default
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "/usr/bin/tesseract")
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# ------------------------------------------------------------
# Rate limiting & authentication
# ------------------------------------------------------------
_request_times = defaultdict(deque)
_rate_limit_lock = threading.Lock()
_upstream_request_lock = threading.Lock()
_last_upstream_request_at = 0.0


def _is_rate_limited(client_ip):
    now = time.monotonic()
    with _rate_limit_lock:
        timestamps = _request_times[client_ip]
        while timestamps and now - timestamps[0] >= RATE_LIMIT_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
            return True
        timestamps.append(now)
    return False


def _authorized():
    if not API_KEY:
        logger.error("API_KEY environment variable is not set")
        return False, jsonify({"success": False, "error": "Server API key is not configured."}), 503
    supplied_key = request.headers.get("X-API-Key", "")
    if not supplied_key and ALLOW_QUERY_API_KEY:
        supplied_key = request.args.get("api", "")
    if not supplied_key or not hmac.compare_digest(supplied_key, API_KEY):
        logger.warning("Unauthorized access attempt from %s", request.remote_addr)
        return False, jsonify({"success": False, "error": "Unauthorized."}), 401
    return True, None, None


def _json_error(message, status_code, headers=None):
    response = jsonify({"success": False, "error": message})
    if headers:
        response.headers.update(headers)
    return response, status_code


def _wait_for_upstream_slot():
    """Keep upstream requests separated across local worker threads."""
    global _last_upstream_request_at
    with _upstream_request_lock:
        now = time.monotonic()
        wait_seconds = UPSTREAM_MIN_INTERVAL_SECONDS - (now - _last_upstream_request_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _last_upstream_request_at = time.monotonic()


def _upstream_block_message(status_code, body_text=""):
    """Return a block message when the upstream service is throttling us."""
    block_terms = (
        "access denied",
        "temporarily blocked",
        "too many requests",
        "rate limit",
        "request limit",
        "unusual traffic",
        "forbidden",
        "blocked",
    )
    normalized = (body_text or "").lower()
    if status_code in {403, 429} or any(term in normalized for term in block_terms):
        return "Verification site is temporarily limiting requests. Please wait before trying again."
    if status_code == 503:
        return "Verification site is temporarily unavailable. Please wait before trying again."
    return None


def _retryable_upstream_error(message):
    return _json_error(
        message,
        429,
        headers={"Retry-After": str(UPSTREAM_BLOCK_RETRY_AFTER_SECONDS)},
    )


def _client_ip():
    return request.remote_addr or "unknown"


# ------------------------------------------------------------
# Input validation
# ------------------------------------------------------------
def _validate_inputs(brn, dob):
    if not brn or not dob:
        return "Both 'brn' and 'dob' are required."
    if not re.fullmatch(r"\d{17}", brn):
        return "Invalid BRN. It must contain exactly 17 digits."
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
        return "Invalid DOB. Use YYYY-MM-DD."
    try:
        datetime.strptime(dob, "%Y-%m-%d")
    except ValueError:
        return "Invalid DOB. Use a real calendar date."
    return None


# ------------------------------------------------------------
# OCR pipeline (math CAPTCHA solver)
# ------------------------------------------------------------
def image_to_math_json(image_bytes):
    """Extract a math equation from a noisy CAPTCHA image."""
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("image_bytes must contain image data.")
    if len(image_bytes) > MAX_OCR_IMAGE_BYTES:
        raise ValueError("The CAPTCHA image is too large.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            rgb = image.convert("RGB")
            red, green, blue = rgb.split()
            blue_score = Image.new("L", rgb.size)
            blue_score.putdata(
                [
                    max(0, min(255, int(b - (r + g) / 2)))
                    for r, g, b in zip(red.getdata(), green.getdata(), blue.getdata())
                ]
            )
            # Most CAPTCHAs are solved by this small fast pass. The broader
            # fallback is retained for harder distortion patterns.
            fast_channel_images = [
                ("red", red, "dark", (100, 120, 140)),
                ("min_rg", ImageChops.darker(red, green), "dark", (100, 120, 140)),
                ("blue_score", blue_score, "light", (50, 90)),
            ]
            fallback_channel_images = [
                ("red", red, "dark", (90, 110, 130, 150, 180)),
                ("min_rg", ImageChops.darker(red, green), "dark", (90, 110, 130, 150, 180)),
                ("blue_score", blue_score, "light", (30, 50, 75, 90, 110)),
            ]
            ocr_passes = (
                ("fast", fast_channel_images, ("--psm 8", "--psm 10")),
                ("fallback", fallback_channel_images, ("--psm 6", "--psm 8", "--psm 10", "--psm 13")),
            )
            selected = None
            whitelist = "0123456789+-*=?"
            for pass_name, channel_images, ocr_configs in ocr_passes:
                ocr_images = []
                for name, channel, polarity, thresholds in channel_images:
                    for threshold in thresholds:
                        if polarity == "dark":
                            mask = channel.point(
                                lambda value, limit=threshold: 255 if value < limit else 0
                            )
                        else:
                            mask = channel.point(
                                lambda value, limit=threshold: 255 if value > limit else 0
                            )
                        # Keep the central text band and lightly close holes in
                        # anti-aliased strokes without erasing small characters.
                        top = min(8, max(0, mask.height // 10))
                        bottom = max(top + 1, min(mask.height, mask.height - 8))
                        band = Image.new("L", mask.size, 0)
                        band.paste(mask.crop((0, top, mask.width, bottom)), (0, top))
                        closed = band.filter(ImageFilter.MaxFilter(3)).filter(
                            ImageFilter.MinFilter(3)
                        )
                        crop_box = (12, 8, max(13, closed.width - 16), max(9, closed.height - 14))
                        ocr_images.append((f"{name}_t{threshold}", closed.crop(crop_box)))
                        if pass_name == "fallback":
                            median = band.filter(ImageFilter.MedianFilter(3))
                            ocr_images.append((f"{name}_t{threshold}_median", median.crop(crop_box)))

                candidates = []
                early_selected = None
                for image_name, ocr_image in ocr_images:
                    enlarged = ocr_image.resize(
                        (ocr_image.width * 8, ocr_image.height * 8),
                        Image.Resampling.NEAREST,
                    )
                    # GIF transparency metadata can break Pillow's temporary PNG
                    # serialization inside pytesseract.
                    enlarged.info.pop("transparency", None)
                    for config in ocr_configs:
                        try:
                            raw_text = pytesseract.image_to_string(
                                enlarged,
                                config=(
                                    f"{config} -c tessedit_char_whitelist={whitelist} "
                                    "-c load_system_dawg=0 -c load_freq_dawg=0"
                                ),
                            )
                        except pytesseract.TesseractNotFoundError as exc:
                            raise RuntimeError(
                                "Tesseract OCR is not installed on the server."
                            ) from exc
                        except pytesseract.TesseractError:
                            continue

                        normalized = (
                            raw_text.replace("×", "*")
                            .replace("x", "*")
                            .replace("X", "*")
                            .replace("−", "-")
                            .replace("–", "-")
                            .replace("—", "-")
                        )
                        match = re.search(
                            rf"(?<!\d)(\d{{1,{MAX_OCR_OPERAND_DIGITS}}})\s*"
                            rf"([+\-*])\s*(\d{{1,{MAX_OCR_OPERAND_DIGITS}}})(?!\d)",
                            normalized,
                        )
                        if match is None:
                            continue
                        first, operator, second = match.groups()
                        if int(first) > 10**MAX_OCR_OPERAND_DIGITS or int(second) > 10**MAX_OCR_OPERAND_DIGITS:
                            continue
                        allowed_text = re.sub(r"[\s\d+\-*=?]", "", normalized)
                        score = len(first) + len(second)
                        if "=" in normalized:
                            score += 3
                        if re.search(r"=\s*[?0-9]", normalized):
                            score += 1
                        if not allowed_text:
                            score += 2
                        candidate = (score, first, operator, second, image_name, config, normalized.strip())
                        candidates.append(candidate)
                        if (
                            pass_name == "fallback"
                            and score >= 8
                            and not allowed_text
                            and ("=" in normalized or "?" in normalized)
                        ):
                            early_selected = candidate
                            break
                    if early_selected is not None:
                        break

                if early_selected is not None:
                    selected = early_selected
                    break
                if not candidates:
                    continue
                marked_candidates = [
                    candidate
                    for candidate in candidates
                    if "=" in candidate[6] or "?" in candidate[6]
                ]
                if marked_candidates:
                    candidates = marked_candidates
                elif pass_name != "fallback":
                    continue
                selected = max(candidates, key=lambda candidate: candidate[0])
                break

            if selected is None:
                raise ValueError("No supported math equation could be read from the image.")
            _score, first, operator, second, image_name, config, raw_text = selected
            logger.debug(
                "CAPTCHA OCR selected %s/%s: %s",
                image_name,
                config,
                raw_text,
            )
    except UnidentifiedImageError as exc:
        raise ValueError("The uploaded file is not a readable image.") from exc
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError("Tesseract OCR is not installed on the server.") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError("The image could not be processed.") from exc

    return {
        "equationDetected": f"{first} {operator} {second}",
        "firstNumber": int(first),
        "operator": operator,
        "secondNumber": int(second),
    }


def calculate_math_json(math_data):
    """Calculate the result from the structured OCR JSON."""
    if not isinstance(math_data, dict):
        raise ValueError("OCR data must be a JSON object.")

    operator = math_data.get("operator")
    first = math_data.get("firstNumber")
    second = math_data.get("secondNumber")
    if operator not in {"+", "-", "*"} or not isinstance(first, int) or isinstance(first, bool) or not isinstance(second, int) or isinstance(second, bool):
        raise ValueError("OCR data does not contain a supported math operation.")

    if operator == "+":
        return first + second
    if operator == "-":
        return first - second
    return first * second


def solve_math_captcha(image_bytes):
    """Complete OCR‑to‑answer pipeline."""
    math_data = image_to_math_json(image_bytes)
    return math_data["equationDetected"], calculate_math_json(math_data)


# ------------------------------------------------------------
# Session & form helpers
# ------------------------------------------------------------
def _new_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _form_payload(form, brn, dob, captcha_answer):
    payload = {}
    for field in form.select("input[name]"):
        field_type = (field.get("type") or "text").lower()
        if field_type in {"submit", "button", "reset", "image", "file"}:
            continue
        payload[field["name"]] = field.get("value", "")
    payload["UBRN"] = brn
    payload["BirthDate"] = dob
    payload["CaptchaInputText"] = captcha_answer
    return payload


def _extract_result(response_text):
    """Parse the HTML response tables into a dictionary."""
    if "Captcha is not valid" in response_text:
        return None, "CAPTCHA is not valid."

    soup = BeautifulSoup(response_text, "html.parser")
    result = {}
    tables = soup.select("table")
    if not tables:
        return None, "Result data was not found."

    # First table: summary rows
    summary_rows = []
    for row in tables[0].select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        cells = [cell for cell in cells if cell]
        if len(cells) == 3:
            summary_rows.append(cells)
    for index in range(0, len(summary_rows) - 1, 2):
        labels, values = summary_rows[index], summary_rows[index + 1]
        for key, value in zip(labels, values):
            result[key.rstrip(":").strip()] = value.strip()

    # Additional tables (e.g., name, parents)
    for table in tables[1:]:
        for row in table.select("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            cells = [cell for cell in cells if cell]
            for index in range(0, min(len(cells), 4) - 1, 2):
                key = cells[index].rstrip(":").strip()
                value = cells[index + 1].strip()
                if key and value:
                    result[key] = value

    if not result:
        return None, "Result data was not found."
    return result, None


# ------------------------------------------------------------
# Flask endpoints
# ------------------------------------------------------------
@app.get("/health")
def health_check():
    # Check Tesseract availability
    try:
        version = pytesseract.get_tesseract_version()
        logger.info("Tesseract version: %s", version)
    except Exception as e:
        logger.error("Tesseract not available: %s", e)
        return jsonify({"status": "degraded", "error": "Tesseract not found"}), 503
    return jsonify({"status": "ok"})


@app.get("/docs")
def docs():
    return jsonify({
        "description": "Automated Birth Registration Verification API",
        "endpoints": {
            "/health": {"method": "GET", "description": "Health check"},
            "/docs": {"method": "GET", "description": "This documentation"},
            "/birth": {
                "method": "GET",
                "description": "Verify a birth record automatically. Requires X‑API‑Key header.",
                "parameters": {
                    "brn": "17‑digit Birth Registration Number (required)",
                    "dob": "Date of birth in YYYY‑MM‑DD format (required)"
                },
                "response": {
                    "success": "boolean",
                    "data": "object containing all extracted fields"
                }
            }
        },
        "authentication": {
            "recommended": "Pass API key in X‑API‑Key header",
            "temporary_query_parameter": "Pass API key as ?api=... while ALLOW_QUERY_API_KEY is enabled"
        },
        "rate_limit": f"{RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds per IP",
        "request_policy": {
            "upstream_min_interval_seconds": UPSTREAM_MIN_INTERVAL_SECONDS,
            "captcha_retry_delay_seconds": CAPTCHA_RETRY_DELAY_SECONDS,
            "block_retry_after_seconds": UPSTREAM_BLOCK_RETRY_AFTER_SECONDS,
        },
    })


@app.get("/birth")
def automated_verification():
    # Rate limit
    if _is_rate_limited(_client_ip()):
        logger.warning("Rate limit exceeded for %s", _client_ip())
        return _json_error("Too many requests. Please try again later.", 429)

    # Authenticate
    authorized, auth_response, status = _authorized()
    if not authorized:
        return auth_response, status

    # Get and validate parameters
    brn = request.args.get("brn", "").strip()
    dob = request.args.get("dob", "").strip()
    validation_error = _validate_inputs(brn, dob)
    if validation_error:
        logger.info("Validation failed: %s", validation_error)
        return _json_error(validation_error, 400)

    # Each attempt uses a fresh upstream session so an OCR miss or a
    # CAPTCHA-invalid submission gets a new image and matching server state.
    for attempt in range(1, CAPTCHA_MAX_ATTEMPTS + 1):
        session = _new_session()
        try:
            _wait_for_upstream_slot()
            response = session.get(TARGET_URL, timeout=HTTP_TIMEOUT_SECONDS)
            block_message = _upstream_block_message(response.status_code, response.text)
            if block_message:
                logger.warning("Upstream block detected while loading page: %s", block_message)
                return _retryable_upstream_error(block_message)
            response.raise_for_status()
        except requests.Timeout:
            logger.error("Timeout fetching main page")
            return _json_error("Verification site timed out.", 504)
        except requests.RequestException as e:
            logger.exception("Failed to fetch target page: %s", e)
            return _json_error("Could not reach verification site.", 502)

        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.select_one("#ubrnsearchform")
        captcha_img = soup.select_one("#CaptchaImage")
        if form is None or captcha_img is None:
            logger.error("Form structure changed – form or captcha image missing")
            return _json_error("Verification form structure changed.", 502)

        image_url = urljoin(response.url, captcha_img.get("src", ""))
        try:
            _wait_for_upstream_slot()
            img_resp = session.get(
                image_url,
                headers={"Referer": response.url},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            image_content_type = img_resp.headers.get("Content-Type", "").lower()
            block_body = "" if image_content_type.startswith("image/") else img_resp.text
            block_message = _upstream_block_message(img_resp.status_code, block_body)
            if block_message:
                logger.warning("Upstream block detected while loading CAPTCHA: %s", block_message)
                return _retryable_upstream_error(block_message)
            img_resp.raise_for_status()
            if not img_resp.headers.get("Content-Type", "").lower().startswith("image/"):
                logger.error("CAPTCHA URL did not return image")
                return _json_error("CAPTCHA endpoint did not return an image.", 502)
            captcha_bytes = img_resp.content
        except requests.Timeout:
            logger.error("Timeout downloading CAPTCHA")
            return _json_error("CAPTCHA download timed out.", 504)
        except requests.RequestException as e:
            logger.exception("Failed to download CAPTCHA: %s", e)
            return _json_error("Could not retrieve CAPTCHA image.", 502)

        try:
            _, captcha_answer = solve_math_captcha(captcha_bytes)
            logger.info(
                "CAPTCHA solved on attempt %d/%d: %d",
                attempt,
                CAPTCHA_MAX_ATTEMPTS,
                captcha_answer,
            )
        except (ValueError, RuntimeError) as e:
            logger.warning(
                "OCR failed on attempt %d/%d: %s",
                attempt,
                CAPTCHA_MAX_ATTEMPTS,
                e,
            )
            if attempt == CAPTCHA_MAX_ATTEMPTS:
                return _json_error("Auto-captcha failed, try again.", 502)
            time.sleep(CAPTCHA_RETRY_DELAY_SECONDS * attempt)
            continue
        except Exception as e:
            logger.exception(
                "Unexpected OCR error on attempt %d/%d: %s",
                attempt,
                CAPTCHA_MAX_ATTEMPTS,
                e,
            )
            if attempt == CAPTCHA_MAX_ATTEMPTS:
                return _json_error("Auto-captcha failed, try again.", 502)
            time.sleep(CAPTCHA_RETRY_DELAY_SECONDS * attempt)
            continue

        payload = _form_payload(form, brn, dob, str(captcha_answer))
        form_action = urljoin(response.url, form.get("action", ""))
        try:
            _wait_for_upstream_slot()
            submit_resp = session.post(
                form_action,
                data=payload,
                headers={"Referer": response.url},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            block_message = _upstream_block_message(submit_resp.status_code, submit_resp.text)
            if block_message:
                logger.warning("Upstream block detected during submit: %s", block_message)
                return _retryable_upstream_error(block_message)
            submit_resp.raise_for_status()
        except requests.Timeout:
            logger.error("Timeout during form submission")
            return _json_error("Verification site timed out during submission.", 504)
        except requests.RequestException as e:
            logger.exception("Form submission failed: %s", e)
            return _json_error("Could not submit verification request.", 502)

        result_data, error_msg = _extract_result(submit_resp.text)
        if error_msg:
            if "CAPTCHA" in error_msg and attempt < CAPTCHA_MAX_ATTEMPTS:
                logger.info(
                    "CAPTCHA rejected by upstream on attempt %d/%d; retrying after backoff",
                    attempt,
                    CAPTCHA_MAX_ATTEMPTS,
                )
                time.sleep(CAPTCHA_RETRY_DELAY_SECONDS * attempt)
                continue
            status = 422 if "CAPTCHA" in error_msg else 404
            logger.info("Result extraction failed: %s", error_msg)
            return _json_error(error_msg, status)

        return jsonify({
            "data": result_data,
            "success": True,
            "footer": {
                "auth": "infinitySFX",
                "creator": "https://t.me/zerox6t9",
                "channel": "https://t.me/INFINITYSFX",
            },
        })

    return _json_error("Auto-captcha failed, try again.", 502)


# ------------------------------------------------------------
# Main (for local development, Railway uses Gunicorn)
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
