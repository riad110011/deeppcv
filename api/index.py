import hmac
import os
from urllib.parse import urljoin

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

PUBLIC_API_KEY = os.getenv("API_KEY", "").strip()
UPSTREAM_API_URL = os.getenv(
    "UPSTREAM_API_URL",
    "https://18082-ii8kekemceblzi10mjavl-6b7bb924.us3.manus.computer/birth",
).strip().rstrip("/")
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY", "").strip() or PUBLIC_API_KEY
HTTP_TIMEOUT_SECONDS = max(5, int(os.getenv("HTTP_TIMEOUT_SECONDS", "25")))
ALLOW_QUERY_API_KEY = os.getenv("ALLOW_QUERY_API_KEY", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _authorized():
    if not PUBLIC_API_KEY:
        return False, jsonify({"success": False, "error": "Server API key is not configured."}), 503

    supplied_key = request.headers.get("X-API-Key", "")
    if not supplied_key and ALLOW_QUERY_API_KEY:
        supplied_key = request.args.get("api", "")

    if not supplied_key or not hmac.compare_digest(supplied_key, PUBLIC_API_KEY):
        return False, jsonify({"success": False, "error": "Unauthorized."}), 401
    return True, None, None


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


@app.get("/")
def home():
    return jsonify({
        "service": "deeppcv Vercel API",
        "status": "ok",
        "endpoints": ["/health", "/docs", "/birth"],
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "runtime": "vercel-python",
        "upstream_configured": bool(UPSTREAM_API_URL),
    })


@app.get("/docs")
def docs():
    return jsonify({
        "description": "Vercel serverless bridge for the birth verification API",
        "endpoints": {
            "/health": {"method": "GET", "description": "Health check"},
            "/docs": {"method": "GET", "description": "This documentation"},
            "/birth": {
                "method": "GET",
                "parameters": {
                    "brn": "17-digit Birth Registration Number",
                    "dob": "Date of birth in YYYY-MM-DD format",
                    "api": "Temporary query-key compatibility; X-API-Key is recommended",
                },
                "response": {
                    "success": "boolean",
                    "data": "object containing extracted fields",
                    "footer": "developer credit object on successful responses",
                },
            },
        },
        "deployment_note": "Only API_KEY is required for the current temporary upstream. UPSTREAM_API_URL and UPSTREAM_API_KEY are optional overrides for a future permanent upstream.",
    })


@app.get("/birth")
def birth():
    authorized, response, status = _authorized()
    if not authorized:
        return response, status

    if not UPSTREAM_API_URL:
        return _error("UPSTREAM_API_URL is not configured.", 503)

    brn = request.args.get("brn", "").strip()
    dob = request.args.get("dob", "").strip()
    if not brn or not dob:
        return _error("Both 'brn' and 'dob' are required.", 400)

    try:
        upstream_response = requests.get(
            UPSTREAM_API_URL,
            params={"brn": brn, "dob": dob},
            headers={
                "Accept": "application/json",
                "X-API-Key": UPSTREAM_API_KEY,
                "User-Agent": "deeppcv-vercel-bridge/1.0",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        return _error("Upstream verification request timed out.", 504)
    except requests.RequestException:
        return _error("Could not reach the upstream verification API.", 502)

    content_type = upstream_response.headers.get("Content-Type", "application/json")
    return Response(
        upstream_response.content,
        status=upstream_response.status_code,
        content_type=content_type,
    )


# Vercel loads the top-level Flask object named `app` from this file.
