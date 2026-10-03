# Playbook: run your own app on your Claude Code subscription

How to rebuild what Podium does (AI agents, a chat, background jobs) **in any other project**, with every AI call going through your logged-in Claude Code CLI instead of an API key.

Everything here is tested. The drop-in module [`examples/claude_cli.py`](examples/claude_cli.py) (about 190 lines, standard library only) passes its self-check live on a Max plan: login check, a one-off call, a two-turn conversation that remembers, streaming, and plan usage.

---

## 0. Is this allowed? Read this first

- ✅ **Your own tools, for you.** Scripts, agents and dashboards you run on your machine, for your own work, on your own login. This is what Podium is.
- ❌ **A product for other people.** A SaaS, a client deliverable, or an app where other users' requests run on *your* subscription. Use an **Anthropic API key** for that (pay per token), for example through the Claude Agent SDK or the plain API. The CLI accepts an API key too, so the design below still works: only the login changes.
- Every call counts against your plan's 5-hour and weekly limits. Section 6 shows how to read them and keep a reserve for your own use.

---

## 1. The idea

```
your app ──▶ subprocess:  claude -p  (non-interactive Claude Code)  ──▶ your claude.ai login (~/.claude)
   ▲                         │ prompt on stdin
   └──── parse JSON lines ◀──┘ stream-json on stdout: text, usage, cost, plan-limit events
```

Your app never sees a token or password. It runs the same `claude` binary you use in the terminal, in **print mode** (`-p`), and reads its JSON output.

---

## 2. Setup on any machine (5 minutes)

```bash
curl -fsSL https://claude.ai/install.sh | bash    # native install -> ~/.local/bin/claude
~/.local/bin/claude                               # once, interactively: log in with your claude.ai account
~/.local/bin/claude auth status --json            # expect "loggedIn": true, "authMethod": "claude.ai"
~/.local/bin/claude --version                     # new models need a recent CLI
```

> **Pitfall I hit:** an old copy (pnpm, v2.1.211) was first on the `PATH`. It accepted `--model claude-opus-5-5` but **silently used an older model**. Always call an explicit binary path (`CLAUDE_BIN`) and check the version. `claude install` or the script above installs the native build.

Then copy the module and run its self-check:

```bash
cp docs/examples/claude_cli.py your_project/
python your_project/claude_cli.py      # prints auth, plan usage, then "ok"
```

---

## 3. The command, flag by flag

```bash
claude -p \
  --output-format stream-json --verbose \  # one JSON event per line (needed for plan-limit events)
  --system-prompt "You are ..." \          # REPLACES Claude Code's coding-assistant prompt: your agent's role
  --tools "" \                             # no tools: Claude only writes text (safest default)
  --setting-sources "" \                   # ignore your CLAUDE.md, hooks, plugins, settings
  --strict-mcp-config \                    # ignore your claude.ai connectors / MCP servers
  --no-session-persistence \               # one-off call: nothing written to your history
  --model claude-opus-5-5 \
  < prompt.txt
```

| Flag | Why it matters |
|---|---|
| `--system-prompt` | Gives each agent its own role ("strict code reviewer", "strategist"…). |
| `--tools ""` | Claude can't touch files or run commands. If it writes code, **your** app runs it, ideally in a sandbox (Podium uses Docker with no network). |
| `--tools WebSearch WebFetch --allowedTools WebSearch WebFetch` | Lets a research role browse the web, still with no file or shell access. |
| `--tools Read --allowedTools "Read(//abs/dir/**)"` | Lets the chat read attached images/PDFs **only** in one folder. |
| `--setting-sources ""` | Without it, your personal setup leaks into the app's behaviour. |
| `--strict-mcp-config` | **Pitfall I hit:** without it, my personal claude.ai connectors (Drive, etc.) showed up in the app's answers. |
| `--output-format stream-json --verbose` | `json` alone works, but only `stream-json` gives the `rate_limit_event` with your real plan usage. |

Output events you care about (one JSON object per line):

| `type` | What you do with it |
|---|---|
| `stream_event` with `delta.type == "text_delta"` | Text as it is generated (needs `--include-partial-messages`). |
| `rate_limit_event` | `rate_limit_info.unifiedWindows.five_hour / seven_day`: `utilization` (0–1) and `resetsAt`. Store it. |
| `result` | Final `result` text, `is_error`, `usage` (tokens), `total_cost_usd`. |

---

## 4. The five building blocks

All five are in `claude_cli.py`.

### 4.1 One call
```python
from claude_cli import ask
text, meta = ask("You are a strict reviewer. Reply in JSON.", code, model="claude-opus-5-5")
print(meta["cost_usd"], meta["input_tokens"], meta["output_tokens"])
```

### 4.2 Conversations (an agent that remembers)
```python
ask(SYSTEM, briefing, session="project-42")        # first call: --session-id <new uuid>
ask(SYSTEM, "result: 0.81. next?", session="project-42")  # later: --resume <same uuid>
```
- Claude Code keeps the history, caches it and auto-compacts it, so you only send the *new* information each turn. This is how Podium's Solver works: one conversation per competition.
- **Pitfall:** sessions are stored **per working directory**. Always run the CLI from the same fixed `cwd` (the module uses `.claude_cli/cwd`), or `--resume` won't find the session.
- If a resume fails (session deleted or broken), start a fresh session once and send the full briefing again (the module does this).
- Cap the length (Podium: 40 turns), then start a new conversation with a fresh briefing.

### 4.3 Streaming (a chat UI)
```python
for chunk in stream(SYSTEM, user_message):
    send_to_browser(chunk)          # e.g. Server-Sent Events
```
Closing the generator kills the CLI process, so a **Stop** button just stops reading. Podium's Copilot also runs the stream in a background thread that saves to the database, so a page refresh can resume watching the same answer.

### 4.4 Your own tools (an assistant that acts)
Claude Code's built-in tools are off, so give the model **your** tools with a plain-text protocol that works with any provider:

1. The system prompt lists your tools (`pause(id)`, `search(query)`…), plus a live snapshot of the app's state.
2. The model replies with a fenced block:
   ````
   ```tool
   [{"name": "search", "args": {"query": "..."}}]
   ```
   ````
3. Your code runs the tool(s) and appends `TOOL RESULT (...)` to the prompt, then asks again (max ~6 steps).
4. The final answer can end with a ```` ```suggest ```` block of follow-ups, shown as clickable chips.

You keep full control: only functions you wrote can run, with your own permission checks. Podium's implementation is in `podium/copilot.py` (`chat_stream`).

### 4.5 Logging and cost
Log every call (agent, purpose, model, tokens, `total_cost_usd`, seconds, ok/error). On a subscription, **`total_cost_usd` is the API-equivalent price, not a bill.** It measures how much work you're doing. Don't let it trip "dollar budget" caps (I made that mistake: the fleet kept pausing itself). Gate on plan usage instead (next section).

---

## 5. One model per role, switchable by `.env`

Name the backend inside the model string, so switching provider is a config change, not a code change:

```env
MODEL_STRATEGY=claude-code:claude-fable-5-1     # few calls, biggest impact -> most capable model
MODEL_WORK=claude-code:claude-opus-5-5          # most calls
MODEL_CHAT=claude-code:claude-opus-5-5
# Other options, same code path through LiteLLM:
# MODEL_WORK=anthropic/claude-opus-5-5   (ANTHROPIC_API_KEY)   bedrock/...   gemini/...   openai/...   ollama_chat/qwen3
```

```python
def call(role, system, prompt, **kw):
    m = os.environ[f"MODEL_{role.upper()}"]
    if m.startswith("claude-code:"):
        return ask(system, prompt, model=m.split(":", 1)[1], **kw)
    import litellm                                   # every other provider
    r = litellm.completion(model=m, messages=[{"role": "system", "content": system},
                                              {"role": "user", "content": prompt}])
    return r.choices[0].message.content, {"cost_usd": litellm.completion_cost(r)}
```
For conversations on LiteLLM, store the message history yourself and replay it (keep the first briefing + the last ~10 exchanges). Podium's full version is `podium/llm.py`.

---

## 6. Respect your plan limits

```python
from claude_cli import plan_usage, plan_ok
plan_usage()   # {'windows': {'five_hour': {'utilization': 0.29, 'resets_at': ...}, 'seven_day': {'utilization': 0.64, ...}}}
if plan_ok(reserve=0.15):   # background work only; keep 15% of the week for you
    run_background_job()
```

- Utilization is refreshed by **every** call (from the `rate_limit_event`). For a fresh reading when idle, make a tiny call with `claude-haiku-4-5`.
- **Background jobs** check `plan_ok()` before each AI step. **Interactive chat** skips the reserve check: you asking a question should always work.
- On a limit error (`usage limit`, `429`, `overloaded`…), stop Claude calls for 30 minutes or until `resetsAt`, send yourself one alert, and optionally switch to a fallback model (an API key or a local Ollama model) to keep going.
- Show it in your UI: a gauge per window, with the reset time. People trust a background agent much more when they can see what it uses.

---

## 7. Safety checklist

- [ ] `--tools ""` by default. Grant only `WebSearch`/`WebFetch`, or `Read` restricted to one folder. Never `Bash`/`Edit` unless the process itself runs in a container.
- [ ] Code the model writes runs in a sandbox (Docker, `--network none`, CPU/RAM limits, no secrets mounted).
- [ ] `--setting-sources ""` + `--strict-mcp-config` on **every** call.
- [ ] Never put secrets in prompts. The model gets data and results, not credentials.
- [ ] A password on any dashboard reachable from your network or phone.
- [ ] Timeouts on every subprocess. Kill the CLI process when the user stops a stream.
- [ ] Logging that can never crash the app (wrap it in `try/except`).

---

## 8. Recipe: a new project in an afternoon

1. `cp docs/examples/claude_cli.py` into the new project and run `python claude_cli.py` (must print `ok`).
2. Write one system prompt per role (planner, worker, reviewer, chat). Give each a persona, a strict output format (e.g. a `SUMMARY:` line, then one code block or JSON) and explicit rules.
3. Build the loop: `plan → act (your code runs it) → measure → feed the result back → re-plan every N steps`. Use `session=` per task so the worker remembers what it tried.
4. Add a reviewer call before any irreversible action (submitting, sending, deploying): a JSON `pass`/`fail` verdict, and a fail feeds its reasons back to the worker.
5. Log every call, show plan usage, gate background work on `plan_ok()`.
6. Add the chat last: `stream()` + your own tools (section 4.4) + a live state snapshot in its system prompt.

## 9. Pitfalls I hit building Podium

| Symptom | Cause | Fix |
|---|---|---|
| Wrong (older) model answering | Old CLI first in the `PATH` | Explicit `CLAUDE_BIN`, check `--version` |
| Personal connectors/instructions in answers | User settings and MCP loaded | `--setting-sources ""` `--strict-mcp-config` |
| `--resume` fails randomly | Different `cwd` between calls | Fixed working directory |
| No plan usage data | `--output-format json` | Use `stream-json --verbose` |
| Fleet keeps pausing on a Max plan | Budget caps counted `total_cost_usd` | Caps count only real billed (API) spend; gate the plan with utilization |
| Two running copies fight over the same jobs and quotas | Same login, two processes | One active instance per account |
| A shutdown script killed my own shell | `pkill -f` pattern matched the shell | Match the exact process and check its `cwd` |

## 10. Where to look in Podium

| File | What to copy from it |
|---|---|
| `docs/examples/claude_cli.py` | The minimal, standalone version of everything above |
| `podium/llm.py` | Production version: roles, LiteLLM providers, conversations, cooldown, fallback, logging |
| `podium/copilot.py` | Chat with your own tools, parallel tool calls, suggestions, resumable streaming |
| `podium/api.py` (`/api/ai/plan`) + Spend page | Plan-usage gauges and per-call spend UI |
| `docs/claude-code-integration.md` | How Podium specifically uses all this |
