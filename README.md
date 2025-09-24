# Oura Daily Coach

A self-hosted FastAPI web app that pulls daily readiness, sleep, and activity summaries from the Oura Cloud API and turns them into a personalised coaching message with OpenAI.

## Features
- Fetches daily readiness, sleep, and activity summaries from Oura.
- Summarises the most relevant metrics for quick scanning.
- Crafts a tailored daily coaching message using OpenAI chat completions.
- Responsive HTML UI with instant refresh via HTMX.
- OAuth login flow for Oura so you never have to paste raw tokens.
- Configurable caching window to limit duplicate API calls.
- Ready for local development or self-hosted deployment via Docker.

## Requirements
- Python 3.11+
- Oura developer app credentials (client ID + secret) **or** a personal access token with daily scopes.
- OpenAI API key with access to the desired chat completion model (default `gpt-4o-mini`).

## Configuration
Create a `.env` file (or set environment variables directly) with one of the following setups.

### Option A – Personal Access Token
```
OURA_PERSONAL_ACCESS_TOKEN=your-oura-token
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-5            # optional
APP_TIMEZONE=UTC                    # optional default; browser timezone auto-detected when available
CACHE_TTL_MINUTES=15                # optional
DATA_FALLBACK_DAYS=1               # optional fallback window for missing daily data
```

### Option B – OAuth (recommended)
```
OURA_CLIENT_ID=your-client-id
OURA_CLIENT_SECRET=your-client-secret
OURA_SCOPES=email personal daily            # set blank to use app defaults
# OURA_AUTHORIZE_URL=https://cloud.ouraring.com/oauth/authorize
# OURA_TOKEN_URL=https://cloud.ouraring.com/oauth/token
OPENAI_API_KEY=your-openai-key
PUBLIC_BASE_URL=http://localhost:8000   # adjust when exposing publicly
TOKEN_STORE_PATH=var/tokens.json        # where tokens are cached locally
APP_SECRET_KEY=change-me                 # required for session cookies
APP_USERNAME=admin                       # login credentials
APP_PASSWORD=change-me
```
- Start the server, open the dashboard, and click **Connect Oura**.
- Complete the authorisation prompt; tokens are stored locally in `TOKEN_STORE_PATH`.
- Use **Disconnect Oura** from the header to revoke local tokens.

An `.env.example` file is included for convenience.

## Install & Run (local)
```bash
python -m venv .venv
./.venv/Scripts/Activate.ps1  # or source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt
uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://localhost:8000` in your browser. You will be prompted to sign in with the credentials from `APP_USERNAME` / `APP_PASSWORD`. Use the **Refresh** button to fetch a fresh message on demand.

## Run with Docker
```bash
docker build -t oura-daily-coach .
docker run --rm -p 8000:8000 --env-file .env oura-daily-coach
```

The container entrypoint launches Uvicorn with the FastAPI app listening on port `8000`.

## Publish to GitHub Container Registry
A GitHub Actions workflow is included to build and push the image whenever you tag a release.

1. Commit the changes and push to GitHub.
2. Create a tag (e.g. `v1.0.0`) and push it: `git tag v1.0.0 && git push origin v1.0.0`.
3. The `docker-publish` workflow builds the container and pushes to `ghcr.io/<owner>/<repo>:<tag>`.

To pull the image from another host/container:
```bash
docker pull ghcr.io/<owner>/<repo>:v1.0.0
```

Authenticate with `ghcr.io` first if the repository is private:
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u <username> --password-stdin
```

## Project Structure
```
app/
  clients/        # Integrations with Oura and OpenAI APIs
  oauth/          # Oura OAuth token orchestration + persistence
  services/       # Domain logic for transforming metric data into messages
  web/            # FastAPI app, templates, and static assets
```

## Next Steps & Ideas
- Add persistent storage (e.g. SQLite) to keep historical messages.
- Schedule automatic refreshes with APScheduler or Celery.
- Extend the UI with charts using historical Oura data.
- Localise tone/style by adding configurable personas in settings.
