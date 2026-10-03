Podium

[Reuse or build](#reuse) [Architecture](#architecture) [Monitoring app](#app) [Data & API](#data) [Stack](#stack) [Roadmap](#roadmap) [Guardrails](#guardrails)

# Mission control for an autonomous Kaggle agent fleet Spec v0.1 for a small SaaS: Google ADK agents find competitions, solve, submit and climb. You watch everything live.

The agents do the work around the clock. Podium is the cockpit: every thought, experiment, submission, rank change and dollar spent, in one place, with a short inbox for the few things only you can do.

**Rank over a competition**Simulated · each dot is a submission

**Don't write the solver**

Open-source ML engineering agents already medal on most Kaggle-style tasks. Plug one in as an engine.

**Build the live layer**

None of them scout real competitions, respect quotas, submit or track the real leaderboard. That's your product.

**Make engines swappable**

One interface, several solvers. Run two engines on the same competition and keep the better CV.

## Reuse or build

These are the strongest open projects for this use case today. Filter by what you need.

| Project | Role in Podium | Why it matters | License | Use it as |
| --- | --- | --- | --- | --- |
| [MLEvolve](https://github.com/InternScience/MLEvolve) InternScience | Solver engine | Ranked #1 on MLE-bench with a 12-hour budget: multi-agent Monte Carlo graph search with a memory that reuses past experience. Open-sourced Feb 2026. | Check terms before commercial use | Primary engine for tabular and classic tasks once the license is cleared |
| [R&D-Agent](https://github.com/microsoft/RD-Agent) Microsoft | Solver engine | Mature, actively maintained, ships a dedicated Kaggle scenario and its own loop-monitoring demo app. Strong MLE-bench results. Linux only. | Commercial-friendly | Safest default engine to start the SaaS with |
| [MLE-STAR](https://github.com/google/adk-samples) google/adk-samples | ADK-native engine | Built on ADK itself: web search for strong models, ablation-guided targeted refinement, ensembling, data-leakage and data-usage checkers. Improved forks rank near the top. | Apache 2.0 (adk-samples) | Reference for your ADK agent design, and a second engine |
| [AIDE](https://github.com/WecoAI/aideml) Weco AI | Lightweight engine | Small, readable tree-search agent; the original MLE-bench baseline. Easy to fork and understand in an afternoon. | Open source (verify version) | Fast, cheap engine for low-budget or playground competitions |
| [MLE-bench](https://github.com/openai/mle-bench) OpenAI | Offline test bench | 75 past Kaggle competitions with local graders and medal thresholds. Lets you test the whole fleet without burning real submissions. | Open source | Your regression suite before every engine or prompt change |

**The gap you fill:** all of these run on a local copy of a competition with a local grader. On live Kaggle you only get cross-validation plus a few leaderboard probes a day, and someone has to accept the rules. Scouting, gating, quota-aware submission, final-pick selection and monitoring are what Podium adds.

## Architecture

Select a component to see what it does, the tools it calls and the events it emits to the dashboard.

## Monitoring app

Six screens. This is a working mock with simulated data, so you can feel the product before building it.

Everything the fleet is doing, at a glance. Rows sort by urgency: blocked first, then nearest deadline.

Active competitions**4**

Experiments today**212**

Spend this week**$184**

Projected medals**2**

| Competition | State | Engine | Best CV | Public LB | Projected | Deadline |  |
| --- | --- | --- | --- | --- | --- | --- | --- |
| retina-lesion-seg | Needs rules | — | — | — | — | 41 days |  |
| tabular-churn-s6e10 | Active | MLEvolve | 0.9182 | 0.9176 | top 3.1% | 27 days |  |
| energy-load-forecast | Active | R&D-Agent | 12.84 | 13.02 | top 8.4% | 13 days |  |
| receipt-text-extract | Quota used | MLE-STAR | 0.874 | 0.869 | top 22% | 9 days |  |
| playground-s6e9 | Finished | MLEvolve | 0.7713 | 0.7709 | private 0.6% | ended |  |

tabular-churn-s6e10 · ROC AUC · 2,140 teams. The CV–LB plot is the single most important view: if dots leave the diagonal, the validation is lying.

#### CV vs public leaderboard

#### Experiment tree (best path highlighted)

- baseline · LightGBM0.9031
- \+ target encoding0.9088
- \+ tenure × plan features0.9124
- \+ pseudo-labels (leak flagged)rejected
- CatBoost branch0.9141
- ensemble · LGBM + CatBoost + XGB0.9182
- MLP branch0.8957

#### Budget for this competition

LLM tokens

$41 / $70

GPU hours

17 / 50 h

Submissions

3 / 5 today

Every agent step streamed as it happens. In production this comes from ADK events and OpenTelemetry spans over server-sent events.

Every real submission, why it was made, and which two will count at the end. The Submitter only spends quota when CV gain beats the noise.

**Today's quota · tabular-churn-s6e10**

| When | Experiment | CV | Public LB | Reason | Final pick |
| --- | --- | --- | --- | --- | --- |

Spend this week by agent. Hard caps live per competition and per week; hitting one pauses the fleet and pings you.

The only things the fleet can't do alone.

## Data & API

One append-only event table feeds the live trace; everything else is state the agents update.

### competitions

- slugpk text
- title, metrictext
- kindtabular|cv|nlp|code
- deadlinetimestamptz
- stateenum
- interest_scorefloat
- rules_acceptedbool

### runs

- iduuid
- competition_slugfk
- enginetext
- statusenum
- budget_usd, budget_gpu_hnumeric
- started_attimestamptz

### experiments

- iduuid
- run_idfk
- parent_idfk, tree
- summarytext
- cv_mean, cv_stdfloat
- code_uri, oof_uritext
- critic_flagsjsonb

### submissions

- iduuid
- experiment_idfk
- kaggle_reftext
- lb_public, lb_privatefloat
- reasontext
- is_final_pickbool

### events

- idbigserial
- run_id, agenttext
- typeenum
- payloadjsonb
- tokens, cost_usdnumeric
- tstimestamptz

### human_tasks

- iduuid
- competition_slugfk
- kindaccept_rules|approve|fix
- urltext
- statusopen|done

```
// event emitted by every agent step (ADK callback → event bus → Postgres + SSE)
{
  "run_id": "r_7f2c", "competition": "tabular-churn-s6e10",
  "agent": "Critic", "type": "experiment.flagged",
  "payload": { "experiment_id": "e_311", "flag": "target_leak",
               "detail": "pseudo-labels built from fold that includes validation rows" },
  "tokens": 3810, "cost_usd": 0.021, "ts": "2026-10-03T09:41:12Z"
}
```

| Endpoint | What it does |
| --- | --- |
| GET `/api/fleet` | Fleet overview: competitions, states, best scores, projections |
| GET `/api/competitions/{slug}` | Detail: experiment tree, CV–LB pairs, budgets |
| GET `/api/stream?run_id=` | Server-sent events for the live trace |
| POST `/api/competitions/{slug}/pause` | Pause or resume the fleet on one competition |
| POST `/api/human-tasks/{id}/done` | Mark rules accepted; the Gatekeeper re-checks and unblocks |
| POST `/api/submissions/{id}/final` | Override the final-pick choice |
| PUT `/api/budgets` | Set caps per competition and per week |

## Stack

**Agents**Google ADK (Python)

LoopAgent orchestrator, SequentialAgent per competition, FunctionTools over the Kaggle API.

**Solver engines**MLEvolve · R&D-Agent · MLE-STAR

Each wrapped behind one `run(task_dir, budget) → candidates` interface, in its own container.

**Compute**GPU VM or Cloud Run jobs

Docker sandbox per experiment, network locked except package mirrors.

**State**Postgres

Plus ADK's DatabaseSessionService so the fleet resumes after a crash.

**Events**Redis Streams

Agents publish; API fans out to the browser over SSE.

**Tracing**OpenTelemetry → Langfuse or Phoenix

Token-level traces per agent call, linked from the live trace.

**Artifacts**S3 / GCS

Code, out-of-fold predictions, submission files, model weights.

**Web app**Next.js + FastAPI

Auth, dashboard, inbox. Notifications via Telegram or Slack.

## Roadmap

Tick tasks as you ship them; progress is saved in this browser.

0 of 0 done

## Guardrails

### Stay inside Kaggle rules

One account, no private code sharing, respect each competition's rules on external data and models. The Gatekeeper parses rules into constraints the Solver must obey.

### Trust CV, not the public board

Submit only on significant CV gains; alert when CV and LB diverge. Final picks favour the best CV plus one diverse, robust candidate.

### Hard spend caps

Per-competition and weekly caps on tokens and GPU hours. Hitting a cap pauses work and pings you instead of silently burning money.

### Sandbox all generated code

Every experiment runs in a throwaway container with no secrets mounted. Only the Submitter holds the Kaggle token.

Podium spec v0.1 · Dashboard data on this page is simulated · Benchmark claims come from each project's own published results; verify licenses before commercial use.