# deeppcv

This repository contains two deployment versions of the birth-verification API. The complete OCR-enabled Flask service is preserved under `railway/` for Railway or another container-capable host. The repository root contains a Vercel-compatible Python serverless bridge under `api/index.py`.

The Vercel version is intentionally lightweight. It authenticates incoming requests, forwards `/birth` requests to the configured full API, and returns the upstream JSON unchanged. This keeps the OCR system dependency in the container version while allowing the public entrypoint to run as a Vercel Function.

## Repository layout

| Path | Purpose |
|---|---|
| `api/index.py` | Vercel Python Function exposing `/`, `/health`, `/docs`, and `/birth`. |
| `vercel.json` | Routes the public paths to the serverless function and sets the function duration. |
| `requirements.txt` | Minimal Flask and requests dependencies for Vercel. |
| `railway/` | Complete OCR-enabled Railway version, including Dockerfile, Tesseract configuration, retry logic, and JSON footer credits. |

## Vercel environment variables

Configure these values in the Vercel project settings before deployment. Do not commit them to GitHub.

| Variable | Required | Description |
|---|---:|---|
| `API_KEY` | Yes | API key accepted by the Vercel public endpoint. |
| `UPSTREAM_API_URL` | Yes | Full upstream `/birth` URL, for example the deployed Railway endpoint ending in `/birth`. |
| `UPSTREAM_API_KEY` | Recommended | API key used when calling the upstream service. If omitted, the bridge falls back to `API_KEY`. |
| `ALLOW_QUERY_API_KEY` | Optional | Defaults to `true` for temporary `?api=...` testing. Set to `false` and use `X-API-Key` for production. |
| `HTTP_TIMEOUT_SECONDS` | Optional | Upstream request timeout; defaults to 25 seconds. |

## Deploying to Vercel

Import this GitHub repository into Vercel, select the project root, add the environment variables for Production and Preview, and deploy. Vercel detects the Python Function from `api/index.py`. The public routes are `/health`, `/docs`, and `/birth`; query parameters are preserved by the rewrites.

A test request has this form:

```text
https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD&api=YOUR_API_KEY
```

For production, prefer the header form:

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD"
```

The Vercel serverless function does not contain the local Tesseract binary. The full OCR-enabled implementation remains in `railway/`; set `UPSTREAM_API_URL` to that deployed service to keep OCR behavior unchanged.
