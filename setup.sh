#!/usr/bin/env bash
# Podium setup: safe to re-run. Adapts to the machine (uv, Docker, Claude Code, existing Kaggle credentials).
#   ./setup.sh           interactive (asks for the Kaggle token if none is found)
#   ./setup.sh --yes     non-interactive
set -euo pipefail
cd "$(dirname "$0")"
YES=0; [ "${1:-}" = "--yes" ] && YES=1
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
setenv() {  # setenv KEY VALUE: update or append in .env (keeps comments)
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }

step "1/6 Python environment"
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
[ -x .venv/bin/python ] || uv venv -q --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r requirements.txt
ok "Python $(.venv/bin/python -c 'import sys;print(sys.version.split()[0])') + dependencies"

step "2/6 Configuration (.env)"
[ -f .env ] || { cp .env.example .env; ok "created .env from .env.example"; }
chmod 600 .env
if ! grep -qE '^(KAGGLE_API_TOKEN|KAGGLE_TOKEN)=.+' .env; then
  if [ -n "${KAGGLE_API_TOKEN:-}" ]; then setenv KAGGLE_API_TOKEN "$KAGGLE_API_TOKEN"; ok "Kaggle token taken from the environment"
  elif [ -f "$HOME/.kaggle/access_token" ]; then setenv KAGGLE_API_TOKEN "$(cat "$HOME/.kaggle/access_token")"; ok "Kaggle token taken from ~/.kaggle/access_token"
  elif [ -f kaggle.json ] || [ -f "$HOME/.kaggle/kaggle.json" ]; then ok "Kaggle credentials: kaggle.json found"
  elif [ $YES = 0 ] && [ -t 0 ]; then
    read -r -s -p "  Kaggle API token (kaggle.com > Settings > API > Generate New Token): " T; echo
    [ -n "$T" ] && setenv KAGGLE_API_TOKEN "$T" && ok "Kaggle token saved to .env"
  else warn "no Kaggle token: set KAGGLE_API_TOKEN in .env"; fi
else ok "Kaggle token present"; fi

step "3/6 Claude Code (AI engine, no API key needed)"
CLAUDE=""
for c in "$HOME/.local/bin/claude" "$(command -v claude 2>/dev/null || true)"; do [ -n "$c" ] && [ -x "$c" ] && { CLAUDE="$c"; break; }; done
if [ -n "$CLAUDE" ]; then
  V=$("$CLAUDE" --version 2>/dev/null | awk '{print $1}')
  if [ "$(printf '%s\n2.1.280\n' "$V" | sort -V | head -1)" != "2.1.280" ]; then
    warn "Claude Code $V is too old for the newest models: installing the native build"
    "$CLAUDE" install >/dev/null 2>&1 || curl -fsSL https://claude.ai/install.sh | bash
    CLAUDE="$HOME/.local/bin/claude"
  fi
  ok "Claude Code $("$CLAUDE" --version | awk '{print $1}') at $CLAUDE"
  if "$CLAUDE" auth status --json 2>/dev/null | grep -q '"loggedIn": true'; then
    ok "logged in: $("$CLAUDE" auth status --json | .venv/bin/python -c 'import json,sys;d=json.load(sys.stdin);print(d.get("email"), "/", d.get("authMethod"), d.get("subscriptionType") or "")')"
    if ! grep -qE '^PODIUM_MODEL_EXPERIMENT=.+' .env; then
      setenv PODIUM_MODEL claude-code
      setenv PODIUM_MODEL_STRATEGY claude-code:claude-fable-5-1
      setenv PODIUM_MODEL_EXPERIMENT claude-code:claude-opus-5-5
      setenv PODIUM_MODEL_REVIEW claude-code:claude-opus-5-5
      setenv PODIUM_MODEL_COPILOT claude-code:claude-opus-5-5
      ok "AI roles set to your Claude Code login (edit in .env or Settings > AI engine)"
    fi
  else warn "Claude Code is not logged in: run  $CLAUDE auth login  then  make check"; fi
else
  warn "Claude Code not found. Install it (curl -fsSL https://claude.ai/install.sh | bash) and run  claude auth login,"
  warn "or configure another provider in .env (see .env.example). Until then Podium uses the free baseline engine."
fi

step "4/6 Experiment sandbox (Docker)"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  docker build -q -t podium-sandbox sandbox/ >/dev/null && ok "image podium-sandbox built"
else
  warn "Docker is not running or not accessible (Linux: sudo usermod -aG docker \$USER, then log in again)."
  warn "Without it set PODIUM_SANDBOX=local in .env (no isolation: trusted machine only)."
fi

step "5/6 Health check"
.venv/bin/python -m podium check || warn "fix the failed items above, then run: make check"

step "6/6 Done"
echo "  Start:   make up        (background)   or   make run   (foreground)"
echo "  Open:    http://127.0.0.1:8000"
echo "  Guide:   docs/SETUP.md"
