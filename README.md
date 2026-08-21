# deeppcv

`deeppcv` is a lightweight Flask API packaged for Vercel Python Functions. It provides `/`, `/health`, `/docs`, and `/birth` routes through the serverless function in `api/index.py`.

The Vercel function authenticates incoming requests, forwards `/birth` requests to the configured upstream verification API, and returns the upstream JSON response unchanged. This keeps the Vercel bundle small and avoids placing credentials or host-specific OCR binaries in the repository.

## Repository layout

| Path | Purpose |
|---|---|
| `api/index.py` | Vercel Python Function exposing the API routes. |
| `vercel.json` | Routes the public paths to the serverless function and configures its maximum duration. |
| `requirements.txt` | Flask and requests dependencies for Vercel. |
| `.env.example` | Safe environment-variable template without secrets. |
| `test_vercel_bridge.py` | Local regression test for authentication and upstream forwarding. |

## Vercel environment variables

Configure these values in Vercel Project Settings. Do not commit them to GitHub.

| Variable | Required | Description |
|---|---:|---|
| `API_KEY` | Yes | API key accepted by the public Vercel endpoint. |
| `UPSTREAM_API_URL` | Yes | Full upstream `/birth` URL. |
| `UPSTREAM_API_KEY` | Recommended | Key used when calling the upstream API. If omitted, the bridge uses `API_KEY`. |
| `ALLOW_QUERY_API_KEY` | Optional | Defaults to `true` for temporary `?api=...` testing. Set to `false` for production and use `X-API-Key`. |
| `HTTP_TIMEOUT_SECONDS` | Optional | Upstream request timeout; defaults to 25 seconds. |

## Deploying to Vercel

Import this GitHub repository into Vercel, keep the repository root as the project root, add the environment variables for Production and Preview, and deploy. Vercel detects the Python Function from `api/index.py`.

A temporary query-parameter test request has this form:

```text
https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD&api=YOUR_API_KEY
```

For production, prefer the header form:

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD"
```

The Vercel version is a serverless bridge. It does not include a local OCR engine; set `UPSTREAM_API_URL` to the full deployed verification service that performs OCR and record extraction. This separation keeps the Vercel Function lightweight and its secrets configurable through Vercel Environment Variables.
