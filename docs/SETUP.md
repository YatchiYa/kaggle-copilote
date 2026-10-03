# Setting up Podium on a new machine

One command installs or repairs everything and starts Podium:

```bash
git clone <your-repo> podium && cd podium
make up
```

Then open **http://127.0.0.1:8000**.

`make up` is safe to run again at any time. It only does what's missing.

## What `make up` does, and how it adapts

| Step | Adapts to the machine |
|---|---|
| 1. Python environment | Installs `uv` if it's missing, creates `.venv` with Python 3.12, installs the pinned dependencies. |
| 2. `.env` | Creates it from `.env.example` (mode 600). Finds your Kaggle token in the `KAGGLE_API_TOKEN` environment variable, `~/.kaggle/access_token` or `kaggle.json`. Otherwise it asks you once. |
| 3. Claude Code | Finds the native build at `~/.local/bin/claude` or on your PATH. **If the version is older than 2.1.280** (too old for Opus 5.5 and Fable 5.1), it installs the native build. If you're logged in, it **sets every AI role to your Claude Code login automatically**, so no API key is needed. |
| 4. Sandbox | Builds the `podium-sandbox` Docker image (Python 3.12, LightGBM, XGBoost, CatBoost, Optuna, PyTorch CPU). |
| 5. Health check | Kaggle auth, Claude Code login, a live answer from each AI role, the sandbox image. |
| 6. Start | Runs Podium in the background, with logs in `data/server.log`. |

## Prerequisites, done once per machine

1. **Docker**, running, and usable without sudo. On Linux: `sudo usermod -aG docker $USER`, then log out and back in.
2. **Claude Code**, logged in: `curl -fsSL https://claude.ai/install.sh | bash`, then `claude auth login`. Choose your Claude.ai account (Pro or Max).
   - No Claude Code? Podium still runs on the free baseline engine. Or set another provider in `.env`: Anthropic, Bedrock, Gemini, OpenAI or Ollama (see `.env.example` and **Settings → AI engine**).
3. **A Kaggle API token**: kaggle.com → Settings → API → *Generate New Token*.
4. Optional: the **SMTP** lines in `.env` for email alerts. Gmail needs an *App Password*.

## Everyday commands

```
make up         install/repair + start in the background
make down       stop (only this folder's Podium) and cancel its running experiments
make restart    restart; data, strategies and conversations are kept
make status     running? + fleet summary
make logs       follow the log
make check      Kaggle + Claude Code + each AI role + sandbox
make test       offline test suite
make service    auto-start at login and restart on crash (systemd, Linux)
make backup     snapshot state into backups/
make restore    restore the newest backup
```

## Moving to another laptop: one fleet per Kaggle account

Two Podiums on the same Kaggle account would compete for the same daily submissions. Run **only one active fleet**:

1. On the old machine: `make backup`, then `make down` (and `make unservice` if you installed the service).
2. Copy `backups/podium-*.tgz` to the new machine's project folder.
3. On the new machine: `make restore`, then `make up`. Strategies, fleet memory, Solver conversations, history and spend all come back.

To watch the dashboard from a second machine without running the fleet there, set `PODIUM_FLEET=0` in its `.env`.

## Phone access

In `.env`:

```
PODIUM_HOST=0.0.0.0
PODIUM_PASSWORD=<something long>
PODIUM_PUBLIC_URL=http://<this-pc-lan-ip>:8000
```

Then `make restart`. Podium refuses to listen on the network without a password.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make check`: Claude Code not logged in | `claude auth login`, then `make check` |
| A model reports "does not support this model" | Old CLI: `make setup` installs the native build |
| Sandbox image missing / Docker permission denied | Start Docker; add yourself to the `docker` group |
| Email test fails with `535` | Use a Gmail App Password in `SMTP_PASSWORD` |
| Port 8000 in use | `PODIUM_PORT=8010` in `.env` |
