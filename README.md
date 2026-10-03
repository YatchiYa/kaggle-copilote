# Podium

Podium is an autonomous Kaggle competition fleet with a monitoring and control dashboard. It finds competitions where it has a good chance of ranking well. Then it plans like an expert team, experiments, reviews its own work, submits within Kaggle's limits, and adapts its plan from leaderboard feedback.

## How it works

A Google ADK `LoopAgent` runs a pass every 60 s. Each active competition runs its own experiment stream in a background thread, so a long experiment never blocks the fleet.

| Agent | Job |
|---|---|
| **Scout** | Lists live competitions every hour (`PODIUM_SCOUT_SECONDS`). It scores each one 0–100 on how likely the fleet is to rank well, using category, time left, data size, metric, team count and the full public leaderboard (leaked boards are penalised). Good ones go to your Inbox and email. |
| **Gatekeeper** | Notices competitions you joined on kaggle.com (every pass) or marked done in Decisions, downloads the data and starts a run. |
| **Strategist** | Profiles the data in the sandbox: shapes, target, time split, and an adversarial-validation check of train/test shift. Then it writes an expert strategy: validation design, features, model portfolio, ensembling and a hypothesis queue. It rewrites the plan every `PODIUM_REFLECT_EVERY` experiments and after each new leaderboard score, adding a "Lessons learned" section tied to the evidence. |
| **Solver** | Works as the competition's domain expert team: the strategy names it, and archetype playbooks apply (Playground, time series, tabular). Where the rules allow external data (Playground and Getting Started), it downloads and uses the public source datasets. Runs one experiment at a time, following the plan, inside a Docker sandbox with no network. It fixes validation first when CV and the leaderboard disagree, and blends the top models every 4th experiment using out-of-fold predictions. |
| **Critic** | Deterministic checks: format, ids, NaN, value ranges, constant predictions, labels, suspicious scores. Before any submission of LLM-written code, an expert LLM code review must pass (leakage, validation, preprocessing, metric). Rejections go back to the Solver. |
| **Submitter** | Respects each competition's Kaggle daily limit, counted from Kaggle itself. It submits only on CV gains beyond fold noise and probes the leaderboard with near-best candidates using up to half the quota. CV is compared only within one validation scheme. It tracks your real rank and picks finals as the best CV plus the best public score. |

The one step that always needs you is **joining a competition**, because Kaggle requires a person to accept the rules. Join it on Kaggle and the fleet takes it from there within a minute.

## Setup

```bash
make up        # installs/repairs everything (Python, .env, Claude Code, sandbox), checks, starts in the background
```

Then open http://127.0.0.1:8000. For a new machine, migration, phone access and troubleshooting, see **[docs/SETUP.md](docs/SETUP.md)**.
Run `make help` for every command (`down`, `restart`, `status`, `logs`, `check`, `test`, `service`, `backup`, `restore`).

## Phone access

Set `PODIUM_HOST=0.0.0.0` and `PODIUM_PASSWORD=...` in `.env`, then open `http://<your-PC-LAN-IP>:8000`. Podium refuses to listen on the network without a password.

## The dashboard

| Page | What it shows |
|---|---|
| **Overview** | KPIs, competitions ranked by live position, agent status, decisions, alerts. |
| **Competitions** | Rank chance (hover for the reasons), state, rank, CV, LB, quota, spend. |
| **Competition** | Six tabs. **Overview:** charts, rank history, caps. **Leaderboard:** your position, the teams around you, the score distribution. **Strategy:** every plan version and the data profile. **Experiments:** code, logs, verdicts. **Submissions:** your full Kaggle history, why each was sent, final picks. **Files.** |
| **Decisions** | Joins needed, recommended competitions, plateaued runs. |
| **Alerts / Activity** | Alerts you haven't read, and a live feed of every agent step. |
| **Spend** | LLM cost by competition and agent, plus caps. |
| **Platforms** | Other competition sites and how well each suits automation. |
| **Settings** | Kaggle account switch (verified before it's saved), AI engine (mode, keys, models, test, guides), autonomy settings, budgets, notifications with a test send. |
| **Copilot** | Chat with the system; it answers from live data and can act. |

## AI engine

Configure it in **Settings → AI engine**. It has a connection-mode picker, credential fields, a live "Test" button and a setup guide for each mode. You can also set it in `.env`.

| Mode | How it connects | Billing | `.env` |
|---|---|---|---|
| **Claude Code** (default) | Your `claude` CLI login (`claude auth login`) | Pro/Max plan usage. Spend shows the equivalent API price. | `PODIUM_MODEL=claude-code`, `PODIUM_CLAUDE_CODE_MODEL=claude-opus-5-5`, `PODIUM_STRATEGY_MODEL=claude-fable-5-1` |
| Anthropic API | API key | Per token | `PODIUM_MODEL=anthropic/claude-opus-5-5`, `ANTHROPIC_API_KEY` |
| AWS Bedrock | AWS keys or profile | AWS | `PODIUM_MODEL=bedrock/<model id from console>`, `AWS_REGION_NAME`, keys or `AWS_PROFILE` |
| Gemini | API key | Google | `PODIUM_MODEL=gemini/gemini-2.5-pro`, `GEMINI_API_KEY` |
| OpenAI | API key | Per token | `PODIUM_MODEL=openai/gpt-5`, `OPENAI_API_KEY` |
| Ollama | Local or remote URL | Free | `PODIUM_MODEL=ollama_chat/qwen2.5-coder:32b`, `OLLAMA_API_BASE` |
| None | n/a | Free | `PODIUM_MODEL=none` (baseline engine only) |

There are two model roles:
- **Work model:** experiments, code review, Copilot. These are most of the calls.
- **Strategy model:** strategy and reflection. There are few calls but they have the biggest impact, so use the most capable model here.

The newest Claude models need the native Claude Code build (`~/.local/bin/claude`, v2.1.280 or later). Install it with `claude install` or the official installer.

## Copilot

Click **Copilot** in the header or sidebar on any page. It sees the whole fleet: competitions, experiments, strategies, leaderboards, alerts, spend and settings. It can act through tools:
- Search and assess Kaggle competitions.
- Recommend a join (this emails you).
- Start, pause or stop a competition.
- Change settings.
- Run the Scout now.
- Read experiment files.

It can't submit: every submission goes through the review gate. Conversations stay in your browser.

## Spend

Every AI call is logged in the `llm_calls` table with its agent, purpose, competition, model, provider, billing type, input/output/cache tokens, cost and duration. **Spend** breaks it down by model, provider and billing, purpose and competition, with a daily chart and a table of every call.

## Files on disk

```
data/podium.db                         state (SQLite)
data/competitions/<slug>/
  data/                                raw Kaggle files
  strategy/profile.md, plan.md, plan_vN.md
  experiments/<exp_id>/                main.py, log.txt, submission.csv, oof.csv, result.json, meta.json
  submissions/<utc>_<exp_id>.csv/.json exact files sent to Kaggle and why
  leaderboard.csv                      latest full public leaderboard
```

## Safety rails

- **Spend caps.** Per competition and per week. Hitting one pauses work and opens a decision.
- **Kill switch.** `PODIUM_AUTO_SUBMIT=0` stops all submissions.
- **Sandbox.** Every experiment runs in a throwaway Docker container with no network and no secrets.
- **Secrets.** `.env`, `kaggle.json`, `docs/kaggle/info.md` and `data/` are gitignored. `.env` is written with mode 600.
- **Tests.** `python test_podium.py` runs offline checks of the engine, sandbox, Critic, review gate, quotas, validation schemes, rank and rank chance.
