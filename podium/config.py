"""All settings come from environment variables (loaded from .env). See .env.example."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path=ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ('"', "'"):
            v = v[1:].split(v[0], 1)[0]  # quoted: keep everything inside the quotes
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()  # unquoted: drop inline "  # comment"
        os.environ.setdefault(k.strip(), v)


_load_dotenv()
# Accept the older name used in this repo's .env.
if os.environ.get("KAGGLE_TOKEN") and not os.environ.get("KAGGLE_API_TOKEN"):
    os.environ["KAGGLE_API_TOKEN"] = os.environ["KAGGLE_TOKEN"]
# kaggle.json next to the project also works (legacy username/key auth).
if not os.environ.get("KAGGLE_API_TOKEN") and (ROOT / "kaggle.json").exists():
    os.environ.setdefault("KAGGLE_CONFIG_DIR", str(ROOT))


def env(name, default):
    v = os.environ.get(name)
    return type(default)(v) if v not in (None, "") else default


# LLM: "none" | "claude-code" | any LiteLLM model string (ollama_chat/..., gemini/..., anthropic/..., bedrock/..., openai/...)
MODEL = env("PODIUM_MODEL", "none")
CLAUDE_CODE_MODEL = env("PODIUM_CLAUDE_CODE_MODEL", "claude-opus-5-5")  # experiments, review, copilot
# Strategy & reflection are few calls with the biggest impact: use the most capable model there.
STRATEGY_MODEL = env("PODIUM_STRATEGY_MODEL", "claude-fable-5-1" if MODEL == "claude-code" else "")
# Native Claude Code build (needed for the newest models); falls back to `claude` on PATH.
CLAUDE_BIN = env("PODIUM_CLAUDE_BIN", str(Path.home() / ".local/bin/claude")
                 if (Path.home() / ".local/bin/claude").exists() else "claude")


# ---- AI model per role --------------------------------------------------------------------------------------
# Value: "claude-code:<model>" (your Claude Code login, no API key) | any LiteLLM model string
# ("anthropic/claude-opus-5-5", "bedrock/<model-id>", "gemini/gemini-2.5-pro", "openai/gpt-5",
#  "ollama_chat/qwen2.5-coder:32b") | "none". Roles can mix providers.
def _default_role(strategy=False):
    if MODEL == "none":
        return "none"
    if MODEL == "claude-code":
        return "claude-code:" + (STRATEGY_MODEL if strategy and STRATEGY_MODEL else CLAUDE_CODE_MODEL)
    return STRATEGY_MODEL if strategy and STRATEGY_MODEL else MODEL


ROLES = ("strategy", "experiment", "review", "copilot")
MODEL_STRATEGY = env("PODIUM_MODEL_STRATEGY", _default_role(strategy=True))  # plan + reflect (+ web research)
MODEL_EXPERIMENT = env("PODIUM_MODEL_EXPERIMENT", _default_role())  # writes experiment / blend code
MODEL_REVIEW = env("PODIUM_MODEL_REVIEW", _default_role())  # code review gate before submission
MODEL_COPILOT = env("PODIUM_MODEL_COPILOT", _default_role())  # dashboard chat


def model_for(role):
    """Live lookup: Settings changes these module attributes at runtime."""
    return globals()["MODEL_" + (role if role in ROLES else "experiment").upper()]


PLAN_RESERVE = env("PODIUM_PLAN_RESERVE", 0.15)  # keep this share of your Claude plan's weekly allowance for yourself
FALLBACK_MODEL = env("PODIUM_FALLBACK_MODEL", "")  # used while Claude Code is rate/usage limited, e.g. anthropic/claude-sonnet-5-5
BACKUP_KEEP = env("PODIUM_BACKUP_KEEP", 7)  # nightly database backups kept in data/backups/
RESEARCH = env("PODIUM_RESEARCH", 1) == 1  # strategist may search the web (claude-code backend)
CONVERSATIONS = env("PODIUM_CONVERSATIONS", 1) == 1  # one persistent Solver conversation per competition
CONVERSATION_MAX_TURNS = env("PODIUM_CONVERSATION_MAX_TURNS", 40)  # then start a fresh, re-briefed one
ENGINE = env("PODIUM_ENGINE", "baseline" if MODEL_EXPERIMENT == "none" else "llm")
SANDBOX = env("PODIUM_SANDBOX", "docker")  # docker | local (local = no isolation, trusted machines only)
SANDBOX_IMAGE = env("PODIUM_SANDBOX_IMAGE", "podium-sandbox")
EXPERIMENT_TIMEOUT_S = env("PODIUM_EXPERIMENT_TIMEOUT_S", 1800)
# Where experiments run: "docker" (local sandbox) | "kaggle" (private Kaggle notebook: free GPU, needed for
# code-only competitions). "auto" = docker for file-submission tabular, kaggle for code-only / cv / nlp / audio.
EXECUTOR = env("PODIUM_EXECUTOR", "auto")
KAGGLE_ACCELERATOR = env("PODIUM_KAGGLE_ACCELERATOR", "NvidiaTeslaT4")  # used for cv/nlp/audio notebooks
NOTEBOOK_TIMEOUT_S = env("PODIUM_NOTEBOOK_TIMEOUT_S", 4 * 3600)  # max wait for one Kaggle notebook experiment

DATA_DIR = Path(env("PODIUM_DATA_DIR", str(ROOT / "data")))
DB_PATH = DATA_DIR / "podium.db"
LOOP_SECONDS = env("PODIUM_LOOP_SECONDS", 60)
SCOUT_SECONDS = env("PODIUM_SCOUT_SECONDS", 3600)  # how often to look for new competitions
MAX_ACTIVE = env("PODIUM_MAX_ACTIVE", 3)
KINDS = set(env("PODIUM_KINDS", "tabular").split(","))  # which competition kinds the fleet works on
MAX_EXPERIMENTS = env("PODIUM_MAX_EXPERIMENTS", 40)  # per competition run
MIN_CHANCE = env("PODIUM_MIN_CHANCE", 45)  # skip competitions whose rank-chance score (0-100) is below this
REVIEW = env("PODIUM_REVIEW", 1) == 1  # LLM code review gate before any LLM-written submission
NOTIFY_URL = env("PODIUM_NOTIFY_URL", "")  # optional: https://ntfy.sh/<topic> or a Slack/Discord webhook
NOTIFY_EMAIL = env("PODIUM_NOTIFY_EMAIL", "")  # where alerts are emailed (uses SMTP_* below)
SMTP_HOST, SMTP_PORT = env("SMTP_HOST", ""), env("SMTP_PORT", 587)
SMTP_USER, SMTP_PASSWORD = env("SMTP_USER", ""), env("SMTP_PASSWORD", "")
FROM_EMAIL = env("FROM_EMAIL", "") or env("EMAIL_FROM_ADDRESS", "") or SMTP_USER
FROM_NAME = env("FROM_NAME", "") or env("EMAIL_FROM_NAME", "") or "Podium"
PUBLIC_URL = env("PODIUM_PUBLIC_URL", "")  # link used in emails, e.g. http://192.168.1.20:8000
PASSWORD = env("PODIUM_PASSWORD", "")  # dashboard password (required when PODIUM_HOST is not localhost)
REFLECT_EVERY = env("PODIUM_REFLECT_EVERY", 4)  # strategist re-plans after this many experiments

AUTO_SUBMIT = env("PODIUM_AUTO_SUBMIT", 1) == 1
# Projects: launch the public baseline (fork of the best public notebook) by itself, one project at a time
PROJECT_AUTOPILOT = env("PODIUM_PROJECT_AUTOPILOT", 1) == 1
DAILY_SUBMISSIONS = env("PODIUM_DAILY_SUBMISSIONS", 0)  # 0 = each competition's own Kaggle daily limit
COMP_CAP_USD = env("PODIUM_COMP_CAP_USD", 20.0)
COMP_CAP_HOURS = env("PODIUM_COMP_CAP_HOURS", 200.0)  # local compute is free; this is a runaway guard
STAGNATION = env("PODIUM_STAGNATION", 6)  # experiments without a new best -> emergency reflection + research
PLATEAU = env("PODIUM_PLATEAU", 12)  # experiments without a new best (after MAX_EXPERIMENTS) before a run plateaus
WEEKLY_CAP_USD = env("PODIUM_WEEKLY_CAP_USD", 50.0)

FLEET = env("PODIUM_FLEET", 1) == 1  # 0 = dashboard only. Run ONE active fleet per Kaggle account.
HOST = env("PODIUM_HOST", "127.0.0.1")
PORT = env("PODIUM_PORT", 8000)
