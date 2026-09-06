# Deploying iHIS on Hugging Face Spaces

Two routes, depending on the account:

| Route | Account needed | Files used |
|-------|----------------|------------|
| **A. Gradio SDK on ZeroGPU** (free personal account: verified e-mail, account ≥ 30 days, max 2 ZeroGPU Spaces) | Free | `README.md` front matter (`sdk: gradio`), `hf_space_app.py`, `requirements.txt`, `deployment/bootstrap.py` |
| **B. Docker Space** (PRO / Team) or any Docker host (Cloud Run, Oracle Always-Free VM, Render…) | Paid on HF, free elsewhere | `Dockerfile`, `.dockerignore`, `deployment/entrypoint.sh`, `deployment/bootstrap.py` |

Both routes run the **same** Flask application. Hugging Face's own guidance
(`huggingface-spaces` skill, 2026): "Gradio and Docker Spaces run on compute
and require a paid plan to create … free personal accounts can host up to 2
ZeroGPU Spaces". Route A is therefore the free path; the Space's mandatory
`@spaces.GPU` function is a no-op, so no GPU quota is consumed.

## What the bootstrap does on every start

`deployment/bootstrap.py`:

1. `FLASK_CONFIG=production`; generates a `SECRET_KEY` if none is set.
2. Uses `DATABASE_URL` (PostgreSQL, e.g. a free Neon database). If absent and
   `IHIS_EPHEMERAL_DEMO=1`, falls back to an ephemeral SQLite file (data is
   lost on restart — demo only).
3. Downloads the optional image-AI weights from the model repo named in
   `IHIS_MODELS_REPO` (see below). Without them the dermatology / fracture /
   tooth pages say the model is not installed; everything else works,
   including the radiology critical-finding classifier (bundled in the repo).
4. `flask db upgrade`, then `seed.py --roles-only` — or the full synthetic
   demo hospital when `IHIS_DEMO_SEED=1` and the database is empty.

## Secrets / variables to set on the Space

| Name | Required | Notes |
|------|----------|-------|
| `SECRET_KEY` | recommended | 64 hex chars; otherwise regenerated per restart |
| `DATABASE_URL` | recommended | `postgresql+psycopg2://…?sslmode=require` (Neon free tier). Omit only with `IHIS_EPHEMERAL_DEMO=1` |
| `GEMINI_API_KEY` | optional | enables the AI Copilot's Gemini features (free tier); everything degrades to local tools without it |
| `IHIS_EPHEMERAL_DEMO` | `1` for a demo without a database | |
| `IHIS_DEMO_SEED` | `1` to load the synthetic demo hospital | never on a real deployment |
| `IHIS_MODELS_REPO` | optional | e.g. `elkotit/ihis-ai-models` |
| `HF_TOKEN` | only if the model repo is private | |
| `AI_MAX_REQUESTS_PER_DAY` … | optional | see `docs/AI_CLINICAL_COPILOT.md` |

## One-time: upload the model weights (≈ 300 MB, not in git)

```bash
hf auth login
hf repos create elkotit/ihis-ai-models --type model --private
hf upload elkotit/ihis-ai-models app/static/ai_models . --repo-type model --include "*.pt" --include "*.pth" --include "*.keras"
```

## Route A — free ZeroGPU Space

```bash
hf auth login
hf repos create elkotit/ihis --type space --space-sdk gradio --flavor zero-a10g --public \
   --secrets SECRET_KEY=<64-hex> --secrets GEMINI_API_KEY=<key> \
   --env IHIS_EPHEMERAL_DEMO=1 --env IHIS_DEMO_SEED=1 --env IHIS_MODELS_REPO=elkotit/ihis-ai-models
hf upload elkotit/ihis . --repo-type space --exclude "**/__pycache__/**" --exclude "venv/**" \
   --exclude "database/**" --exclude "AI apps/**" --exclude "app/static/ai_models/*.p*" \
   --exclude "app/static/ai_models/*.keras" --exclude "logs/**" --exclude "var/**" --exclude ".git/**"
hf spaces logs elkotit/ihis --follow
```

The app is served at `https://elkotit-ihis.hf.space/ihis/` (landing page at
`/`). Add `--secrets DATABASE_URL=…` for a persistent Neon database.

## Route B — Docker

```bash
docker build -t ihis .
docker run -p 7860:7860 -e IHIS_EPHEMERAL_DEMO=1 -e IHIS_DEMO_SEED=1 -e SECRET_KEY=<64-hex> ihis
```

On Hugging Face (PRO): change the front matter to `sdk: docker` and
`app_port: 7860`, remove `app_file`, then `hf upload … --repo-type space`.

## Limits to know

- Free Spaces sleep after 48 h without traffic and wake on the next request
  (first load takes a minute).
- Disk is ephemeral: uploaded documents and images vanish on restart. Use a
  Neon database for records; for durable files mount external storage.
- The free Gemini tier is rate-limited; the platform's budget, cache and
  fallback keep the app usable when the provider is busy.
