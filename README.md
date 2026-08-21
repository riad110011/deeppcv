# deeppcv

`deeppcv` is a Vercel-compatible Flask serverless API. The root `index.py` exposes the Flask application to Vercel, while `api/index.py` contains the implementation. The public routes are `/`, `/health`, `/docs`, and `/birth`.

The application authenticates incoming requests, forwards `/birth` requests to the configured birth-verification service, and returns the upstream JSON response unchanged. The temporary upstream URL is included as a replaceable code default, so the only required Vercel environment variable is `API_KEY`.

## Vercel setup

Import this repository into Vercel, keep the repository root as the project root, and redeploy the latest commit. Add this environment variable:

```text
API_KEY=your-api-key
```

Vercel uses the root `index.py` Flask entrypoint. No `/api` rewrite is required.

The current default upstream is the temporary birth API used for testing. To replace it later, add optional `UPSTREAM_API_URL` and `UPSTREAM_API_KEY` variables in Vercel. `ALLOW_QUERY_API_KEY` defaults to `true` for temporary testing; set it to `false` for production and use the `X-API-Key` header.

A test request has this form:

```text
https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD&api=YOUR_API_KEY
```

For production, prefer:

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://YOUR-VERCEL-DOMAIN.vercel.app/birth?brn=YOUR_BRN&dob=YYYY-MM-DD"
```

The actual API key is not included in the repository. After changing Vercel environment variables or pushing a new commit, create a new deployment before testing the URL.
