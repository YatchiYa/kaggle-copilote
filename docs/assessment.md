# Podium: an honest assessment (2026-10-03, updated in the evening)

## Verdict in one paragraph

Podium is a **serious, working autonomous Kaggle system**. It finds competitions, plans like an expert team, writes and runs its own experiments in a sandbox, reviews its own code, submits within Kaggle's quotas, reads the real leaderboard and corrects itself. It does this live, which most open-source ML agents don't. The proof so far: Store Sales went from **#395 to #76 (top 14%) in one day with no human in the loop**, after the Strategist found that its own validation was leaking.

**It is not yet "world-level S+".** World-level means medals on real competitions and a measured, comparable score on a public benchmark (MLE-bench), and neither has been demonstrated yet. Today I'd grade it **A- as a system, B on proven results.** Most of what's missing is known and listed below.

## Scorecard

| Dimension | Grade | Evidence / reason |
|---|---|---|
| Live Kaggle loop (scout → join → plan → experiment → review → submit → rank → re-plan) | **A** | Runs unattended. Quotas come from Kaggle itself. Manual joins are auto-detected. |
| Self-correction | **A** | Reflection every 4 experiments and after each leaderboard score. Validation-first rule. CV↔LB calibration. 15 transferable lessons in fleet memory after day 1. |
| Code safety | **A-** | Docker sandbox with no network and no secrets. Deterministic critic plus LLM review gate. CPU/RAM fair share. |
| Model flexibility | **A** | One model per role, any provider (Claude Code login, Anthropic, Bedrock, Gemini, OpenAI, Ollama). Per-call cost tracking. |
| Dashboard and Copilot | **A-** | Sidebar app, works on phone, live alerts, leaderboards, files, spend. Streaming Copilot with tools, navigation and attachments. |
| Results | **B** | Top 14% (Store Sales), top ~57% (Playground S6E10). No medal yet. Gemma v1 is still being scored. |
| Breadth | **C+** | Tabular and file-submission only. No GPU deep learning, no notebook (code) competitions, which is where most prize money is. |
| Measured vs the state of the art | **C** | Not yet benchmarked on MLE-bench, so it can't be compared fairly with MLEvolve, R&D-Agent, MLE-STAR or AIDE. |
| Operations | **B** | A single process. A restart kills experiments in flight. SQLite. The dashboard has no password unless you set one. |

## What's already done

- [x] Rank-chance scouting (category, data size, metric, teams, leaderboard saturation), hourly
- [x] Auto-detection of competitions you join manually on kaggle.com, including ones never seen before
- [x] Expert strategy per competition: domain team, playbook by type, validation design, hypothesis queue
- [x] Web research by the Strategist (Claude Code roles)
- [x] One ongoing Solver conversation per competition (learns round after round)
- [x] Fleet memory: transferable lessons carried into every new competition
- [x] Data profiling with adversarial validation; external public data where the rules allow it
- [x] Deterministic critic, LLM code review gate, validation-scheme-aware submitting, leaderboard probes
- [x] Real rank tracking, CV↔LB divergence alerts, final-pick hedge
- [x] Optuna, PyTorch (CPU), LightGBM, XGBoost and CatBoost in the sandbox; blending on out-of-fold predictions
- [x] Per-role models in `.env`; multi-provider; spend per call, model, purpose and competition
- [x] Email alerts; professional dashboard; streaming Copilot that can act, navigate and read attachments
- [x] Stop, Archive and Resume that stick; dedicated-project state for agent and paper competitions
- [x] Gemma 4 main track: harness studied, **Podium-SWE v1 submitted**; Paper Track plan and skeleton written

## Gaps to world level (to-do)

### Results and measurement
- [ ] **Benchmark on MLE-bench (lite split)** for a medal rate comparable with MLEvolve, R&D-Agent and MLE-STAR. This is the single most important proof.
- [ ] Get one competition into the **top 10%**, then a **medal** on a featured competition.
- [ ] A regression suite: re-run a fixed benchmark before every prompt or engine change.

### Breadth (where the prize money is)
- [x] **Code (notebook) competitions**: experiments run as private Kaggle notebooks (`podium/kaggle_runner.py`, verified live on Titanic) and code competitions are submitted from the notebook (`competition_submit_code`). Enable them with `PODIUM_KINDS=tabular,code`.
- [x] **GPU deep learning**: vision, NLP and audio experiments run as Kaggle GPU notebooks (`PODIUM_EXECUTOR=auto`, `PODIUM_KAGGLE_ACCELERATOR`). [ ] Not yet battle-tested on a real vision or NLP competition.
- [x] Vision and NLP playbooks (pretrained backbones from attached Kaggle Models, augmentations, TTA, k-fold), plus a `## Kaggle sources` strategy section to attach offline weights. [ ] No reusable pipeline library: the Solver writes each pipeline itself.

### Search and modelling power
- [x] Tree search: UCB node selection over the experiment tree plus an exploration draft every 6th experiment (tested). [ ] Branches still run one at a time per competition.
- [x] Persistent per-competition workspace: Optuna studies (`optuna.db`), shared fold indices, feature cache (Parquet).
- [x] Final round within 3 days of the deadline (5-seed rebuild); final picks are the best CV plus the most robust candidate (CV minus fold std).
- [x] Feature and data caching between experiments (see the workspace above).

### Reliability and operations
- [x] Interrupted experiments resume after a restart with the same script and no new AI call.
- [x] `make service` (systemd, auto-restart) and nightly database plus fleet-memory backups (7 kept).
- [ ] Postgres instead of SQLite. Not needed until more than about 5 competitions run in parallel (SQLite with WAL handles today's load).
- [x] Plan-limit protection: a 30-minute cooldown plus an alert, and an optional `PODIUM_FALLBACK_MODEL` to keep going.

### Security
- [ ] Set `PODIUM_PASSWORD` before opening the dashboard to your network or phone.
- [ ] Rotate the secrets that were exposed: the Kaggle token (also stored in `docs/kaggle/info.md`) and the Gmail App Password (pasted in chat).
- [ ] Move secrets out of plain `.env`, into a keyring or Infisical (which you already run).

### Gemma 4 project
- [x] Project submissions are tracked automatically (status, score, rank, alerts). [ ] v1 hidden-test score pending; then iterate daily.
- [x] Development loop (`projects/gemma4-swe-paper/devloop.py`): the official harness on Kaggle L4, any agent version, any task subset. First run (v1, 10 tasks) launched. [ ] Full 129-task baseline.
- [ ] Skill distillation, ablations, leave-one-repo-out study.
- [ ] Write the paper (3,000 words), submit by **2026-11-07**, final by 2026-11-12.

### Other
- [ ] Numerai adapter (best fit among other platforms), then CrunchDAO.
- [x] End-to-end UI test (`make e2e`: 9 pages × desktop and phone, table, Copilot) and a CI workflow (`.github/workflows/ci.yml`).

## Is it worth a LinkedIn post?

**Yes, and the best moment is after the Gemma results**, not before.

- **Now:** a "building in public" post is fine if it's framed honestly. "I built an autonomous agent team that competes on Kaggle and corrects itself. Here's the moment it discovered its own validation was lying, and went from #395 to #76 in a day." Show the dashboard and the rank chart. Don't claim a win.
- **After Gemma (December 2026):** a much stronger story, especially with a Paper Track write-up or a good main-track rank. "My autonomous system competed in Google DeepMind's Gemma 4 agent challenge: here's what a 31B model learned to do by writing its own skills." That's novel, technical and verifiable, which is exactly what performs on LinkedIn for an engineering audience.
- **Don't claim:** "best in the world", "state of the art" or "wins Kaggle" until a benchmark or medal proves it. Experienced readers will check, and specific, honest numbers are more impressive anyway.
