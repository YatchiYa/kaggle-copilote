# How Podium uses your Claude Code subscription

This explains how Podium's agents and the Copilot run on your own Claude Code login with no API key, what that costs, and where each piece lives in the code.

## 1. The idea in one picture

```
 ┌──────────────────────────── Podium (python -m podium) ────────────────────────────┐
 │                                                                                   │
 │  Strategist ─┐                                                                    │
 │  Solver ─────┤                                                                    │
 │  Critic ─────┼──▶ podium/llm.py ──▶ runs a subprocess:                             │
 │  Copilot ────┤      complete()        ~/.local/bin/claude -p  (Claude Code CLI)    │
 │  Settings ───┘      stream()                │                                     │
 │                         │                   │ uses YOUR login stored in ~/.claude │
 │                         ▼                   ▼                                     │
 │                  llm_calls table      Anthropic (claude.ai account, Max plan)     │
 │                  (Spend page)                                                     │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

Podium never talks to the Anthropic API directly in this mode. It runs the **Claude Code CLI** in non-interactive mode (`claude -p`), just as you would in a terminal. The CLI is already logged in as you, so every request is authenticated with your Claude account.

## 2. One AI call, step by step

`podium/llm.py → _claude_code()` builds this command:

```bash
~/.local/bin/claude -p \
  --output-format json \             # machine-readable answer + usage + cost
  --tools "" \                       # NO tools: Claude can't touch files or run commands here
  --system-prompt "<agent role>" \   # replaces Claude Code's default coding-assistant prompt
  --no-session-persistence \         # nothing saved to your Claude Code history
  --setting-sources "" \             # ignore project/user settings, hooks and plugins
  --model claude-opus-5-5            # which model (see section 4)
```

The **prompt** goes to the CLI through stdin. That's the task, the data profile, the strategy, earlier results and so on. The CLI returns JSON:

```json
{ "result": "…the answer…",
  "usage": { "input_tokens": …, "output_tokens": …, "cache_read_input_tokens": … },
  "modelUsage": { "claude-opus-5-5": { … } },
  "total_cost_usd": 0.27 }
```

Podium reads `result` as the answer and logs the usage and cost (section 7).

Why these flags matter:
- **`--tools ""`**: inside Podium, Claude only *writes text*. When it writes experiment code, Podium runs that code itself in a locked Docker sandbox (no network, no secrets). Claude never executes anything on your machine.
- **`--system-prompt`**: each agent gets its own role, for example "Kaggle Grandmaster writing the next experiment" or "strict reviewer".
- **`--setting-sources ""` and `--no-session-persistence`**: your personal Claude Code setup (CLAUDE.md, hooks, plugins, history) is neither used nor polluted.

## 3. Authentication and billing

| Question | Answer |
|---|---|
| Which account? | The one Claude Code is logged into. Check with `claude auth status`. Here: **yarab@novagen.tech, `claude.ai` login, Max plan**. |
| Where are the credentials? | In Claude Code's own config, `~/.claude`. Podium never sees or stores your Claude password or tokens. |
| Is an API key used? | **No.** The `ANTHROPIC_API_KEY` field in Settings is only for the separate "Anthropic API" mode. |
| Am I billed per call? | **No.** With a claude.ai Pro/Max login, calls are covered by your subscription. They count against your plan's **usage limits**. |
| Then what are the dollars in Spend? | The **equivalent API price** Claude Code reports (`total_cost_usd`). It shows how much work the fleet does and drives the budget caps. It isn't an invoice. |
| What if I hit my plan's limit? | Calls fail until the limit resets. The failure shows up as an agent error in Alerts and Activity, and the fleet carries on afterwards. For heavy unattended runs, switch to **Anthropic API** mode in Settings (pay per token, no plan limits). |

The Spend page labels every call **Covered by plan**, **Billed per call** or **Local, free**, based on how the CLI is authenticated (`podium/llm.py → claude_code_auth()`, cached for 10 minutes).

## 4. Which model does what

| Role | Default model | Used by | Why |
|---|---|---|---|
| **Work** (`PODIUM_CLAUDE_CODE_MODEL`) | `claude-opus-5-5` | Solver experiments, Critic code review, Copilot, connection tests | Most calls; strong coding at a lower cost |
| **Strategy** (`PODIUM_STRATEGY_MODEL`) | `claude-fable-5-1` | Strategist: first plan and every reflection | Few calls, biggest impact, so it uses Anthropic's most capable model |

Change them in **Settings → AI engine**. They're saved to `.env`.

**The CLI version matters.** The newest models need Claude Code v2.1.280 or later. Your old pnpm copy (v2.1.211) silently fell back to Opus 4.8. Podium therefore uses the native build at `~/.local/bin/claude` (v2.1.288), configurable with `PODIUM_CLAUDE_BIN`. Your terminal's `claude` still points to the old pnpm copy until you remove it (`pnpm rm -g @anthropic-ai/claude-code`).

## 5. Who calls the AI, and when

| Agent | Purpose (in Spend) | When | Model | Input | Output |
|---|---|---|---|---|---|
| **Strategist** | `strategy` | Once per competition | Strategy | Data profile, playbook for the competition type, permitted external datasets | Plan: expert team, validation design, features, models, hypothesis queue, external data |
| **Strategist** | `reflection` | Every 4 experiments and after each new leaderboard score | Strategy | Current plan, experiment log, CV↔LB pairs, rank | Rewritten plan with "Lessons learned" |
| **Solver** | `experiment` | Each experiment, one at a time per competition | Work | Task, plan, best script so far, recent failures | `SUMMARY:` line + one Python script |
| **Solver** | `blend` | Every 4th experiment once 3+ good models exist | Work | Top experiments' out-of-fold files | Blending script |
| **Critic** | `code review` | Before any AI-written submission | Work | Script, log, plan | JSON verdict `pass`/`fail` + issues |
| **Copilot** | `chat` | When you talk to it (one call per reasoning step) | Work | Your conversation + live system snapshot + tool results | Streamed answer, tool calls, suggestions |

## 6. How the Copilot can *act* without Claude Code's tools

Claude Code's built-in tools (Bash, Edit and so on) are switched off (`--tools ""`), so Podium gives the Copilot **its own tools**, defined in `podium/copilot.py`:

```
fleet · competition · leaderboard · strategy · events · search_kaggle · assess · read_file      (read)
recommend · start · pause · stop · resolve_decision · set_setting · scout_now · navigate         (act)
```

The protocol is plain text, so it works with any model provider:

1. Podium sends the **system prompt**: the tool list, the rules, and a **live snapshot** of the fleet (competitions, ranks, CV, spend, open decisions, recent activity, the page you're on).
2. To use a tool, Claude replies with a fenced block, either one call or a list of calls run together:

   ````
   ```tool
   [{"name": "assess", "args": {"slug": "gemma-4-developer-agent-paper"}},
    {"name": "navigate", "args": {"path": "decisions"}}]
   ```
   ````

3. Podium runs the tools itself, in Python, with your permissions, and appends `TOOL RESULT (…)` to the conversation.
4. It asks Claude again. This repeats for up to 6 steps, then Claude writes the final answer.
5. The answer ends with a hidden ```` ```suggest ```` block, which becomes the clickable follow-ups.

`navigate` makes the dashboard switch pages for you. Links like `[Store Sales](#/competition/store-sales-time-series-forecasting)` in answers are clickable routes. The Copilot can't submit to Kaggle: submissions only go through the fleet's review gate.

## 7. Streaming: why text appears word by word

For the Copilot, Podium runs the CLI in streaming mode:

```bash
claude -p --output-format stream-json --include-partial-messages --verbose …
```

The CLI prints one JSON event per line. Podium (`llm.stream()`) forwards each `text_delta` immediately. `copilot.chat_stream()` then:
- hides tool and suggest blocks as they're being typed;
- sends the browser server-sent events: `delta` (text), `step` / `step_done` (tool timeline), `navigate`, `done` (final answer + suggestions);
- has the browser (`static/app.js → sendCopilot()`) read the stream and redraw the bubble each animation frame.

**Stop** aborts the request, and Podium kills the CLI process.

## 8. Every call is logged

`llm.complete()` and `llm.stream()` write one row per call to the `llm_calls` table: time, agent, purpose, competition, backend, model, billing type, input/output/cache tokens, cost and duration, plus whether it succeeded and any error message. The **Spend** page shows the totals, the daily chart, breakdowns by model, provider, purpose and competition, and every call.

## 9. Switching away from Claude Code

**Settings → AI engine → Connection mode** offers the following. Each has a setup guide and a **Test** button.
- Anthropic API key
- AWS Bedrock
- Gemini
- OpenAI
- Ollama (free, local)
- No AI

Those modes go through LiteLLM instead of the CLI. Everything else is identical, including the agents, Copilot tools, streaming, logging and review gate.

## 10. Where to look in the code

| File | What it does |
|---|---|
| `podium/llm.py` | `complete()`, `stream()`, CLI command, auth detection, call logging |
| `podium/config.py` | `PODIUM_MODEL`, `PODIUM_CLAUDE_CODE_MODEL`, `PODIUM_STRATEGY_MODEL`, `PODIUM_CLAUDE_BIN` |
| `podium/engines.py` | Prompts for experiments, blends, strategy and reflection; playbooks; sandbox runner |
| `podium/agents.py` | When the agents call the AI; the review gate |
| `podium/copilot.py` | Copilot tools, snapshot, tool loop, streaming events |
| `podium/api.py` | `/api/copilot/stream`, `/api/ai`, `/api/ai/test`, `/api/spend/detail` |
| `podium/static/app.js` | Copilot panel, Spend page, Settings → AI engine |
