# deeppcv

`deeppcv` is a Vercel-compatible Flask serverless API. It exposes `/`, `/health`, `/docs`, and `/birth` through the Python Function in `api/index.py`.

The bridge authenticates incoming requests, forwards `/birth` requests to the configured birth-verification service, and returns the upstream JSON response unchanged. The current temporary upstream URL is included as a replaceable code default, so the only required Vercel environment variable is `API_KEY`.

## Files

| Path | Purpose |
|---|---|
| `api/index.py` | Flask application loaded by Vercel as a Python Function. |
| `vercel.json` | Routes `/`, `/health`, `/docs`, and `/birth` to the Function. |
| `requirements.txt` | Minimal Flask and requests dependencies. |
| `.env.example` | Contains only the `API_KEY` placeholder. |
| `test_vercel_bridge.py` | Local regression test for authentication and upstream forwarding. |

## Vercel setup

Import this repository into Vercel, keep the repository root as the project root, and add one environment variable:

```text
API_KEY=your-api-key
```

The current default upstream is the temporary birth API used for testing. To replace it later without changing the public API, add `UPSTREAM_API_URL` and, when needed, `UPSTREAM_API_KEY` as optional Vercel environment variables. Environment variables are preferred for secrets because they remain outside the source code.

A temporary test request has this form:

```text
https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD&api=YOUR_API_KEY
```

For production, set `ALLOW_QUERY_API_KEY=false` and use the recommended header form:

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD"
```

The actual API key is not included in this repository. Vercel applies environment-variable changes only to new deployments, so redeploy after changing a variable.
