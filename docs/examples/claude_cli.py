"""claude_cli.py: call Claude through your logged-in Claude Code CLI (your subscription, no API key).

    from claude_cli import ask, stream, plan_usage
    text, meta = ask("You are a terse assistant.", "Say hi")
    text, meta = ask(sys, "Next step?", session="project-42")   # continues the same conversation
    for chunk in stream(sys, "Explain X"): print(chunk, end="")
"""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or (str(Path.home() / ".local/bin/claude")
                                               if (Path.home() / ".local/bin/claude").exists() else shutil.which("claude"))
STATE = Path(os.environ.get("CLAUDE_CLI_STATE", ".claude_cli"))    # sessions + plan usage + call log
WORKDIR = STATE / "cwd"   # fixed cwd: Claude Code stores sessions per directory, --resume needs the same one
STATE.mkdir(exist_ok=True)
WORKDIR.mkdir(exist_ok=True)

# Isolation: ignore your personal CLAUDE.md, hooks, plugins and claude.ai connectors (MCP).
BASE = ["-p", "--verbose", "--setting-sources", "", "--strict-mcp-config"]
SAFE_TOOLS = ("WebSearch", "WebFetch")   # never Bash/Edit/Write unless you sandbox the process


def _load(name, default):
    f = STATE / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else default


def _save(name, value):
    (STATE / f"{name}.json").write_text(json.dumps(value, indent=1))


def auth():
    """{'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max', ...}"""
    p = subprocess.run([CLAUDE_BIN, "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout or "{}")


def _cmd(system, model, tools, session, stream_mode):
    cmd = [CLAUDE_BIN, *BASE, "--output-format", "stream-json", "--system-prompt", system]
    if stream_mode:
        cmd += ["--include-partial-messages"]
    allowed = [t for t in (tools or []) if t in SAFE_TOOLS]
    cmd += ["--tools", *allowed, "--allowedTools", *allowed] if allowed else ["--tools", ""]
    if model:
        cmd += ["--model", model]
    sessions, sid = _load("sessions", {}), None
    if session:
        if session in sessions:
            sid = sessions[session]
            cmd += ["--resume", sid]
        else:
            sid = str(uuid.uuid4())
            cmd += ["--session-id", sid]
    else:
        cmd += ["--no-session-persistence"]
    return cmd, sid


def _on_event(ev, meta):
    """Shared handling of stream-json events: plan usage and the final result."""
    if ev.get("type") == "rate_limit_event":
        info = ev.get("rate_limit_info", {})
        _save("plan_usage", {"status": info.get("status"), "at": time.time(),
                             "windows": {k: {"utilization": v.get("utilization"), "resets_at": v.get("resetsAt")}
                                         for k, v in (info.get("unifiedWindows") or {}).items()}})
    elif ev.get("type") == "result":
        u = ev.get("usage", {})
        meta.update(ok=not ev.get("is_error"), result=ev.get("result"), cost_usd=ev.get("total_cost_usd", 0.0),
                    input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                    cache_tokens=u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0))


def _log(meta):
    with open(STATE / "calls.jsonl", "a") as fh:
        fh.write(json.dumps({k: v for k, v in meta.items() if k != "result"}) + "\n")


def ask(system, prompt, model=None, tools=None, session=None, timeout=1800, _retry=True):
    """One call. Returns (text, meta). meta has tokens, cost_usd (API-equivalent, not billed on a plan), seconds."""
    if cooling_down():
        raise RuntimeError("Claude plan limit reached; cooling down until " + time.ctime(_load("cooldown", 0)))
    cmd, sid = _cmd(system, model, tools, session, False)
    meta, t0 = {"model": model, "session": session, "ts": time.time()}, time.time()
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=WORKDIR)
    for line in p.stdout.splitlines():
        try:
            _on_event(json.loads(line), meta)
        except json.JSONDecodeError:
            pass
    meta["seconds"] = round(time.time() - t0, 1)
    if p.returncode or not meta.get("ok"):
        err = meta.get("result") or p.stderr[-500:]
        if session and _retry and session in _load("sessions", {}):   # stale session: start fresh once
            forget(session)
            return ask(system, prompt, model, tools, session, timeout, _retry=False)
        _note_limit(err)
        meta.update(ok=False, error=str(err)[:500])
        _log(meta)
        raise RuntimeError(f"claude failed: {err}")
    if sid:
        _save("sessions", {**_load("sessions", {}), session: sid})
    _log(meta)
    return meta["result"], meta


def stream(system, prompt, model=None, tools=None):
    """Yield text as it is generated (no conversation state). Closing the generator kills the CLI."""
    cmd, _ = _cmd(system, model, tools, None, True)
    meta, t0 = {"model": model, "ts": time.time()}, time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=WORKDIR)
    p.stdin.write(prompt)
    p.stdin.close()
    seen = False
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "stream_event":
                e = ev.get("event", {})
                if e.get("type") == "message_start" and seen:
                    yield "\n\n"   # a new assistant message after an internal tool call
                if e.get("delta", {}).get("type") == "text_delta":
                    seen = True
                    yield e["delta"]["text"]
            else:
                _on_event(ev, meta)
        if not meta.get("ok"):
            raise RuntimeError(f"claude failed: {meta.get('result') or p.stderr.read()[-500:]}")
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
        meta["seconds"] = round(time.time() - t0, 1)
        _log(meta)


def forget(session):
    s = _load("sessions", {})
    s.pop(session, None)
    _save("sessions", s)


# ------------------------------------------------------------- plan limits
def plan_usage():
    """Latest real utilization of your plan, e.g. {'windows': {'five_hour': {'utilization': 0.31, ...}, 'seven_day': ...}}"""
    return _load("plan_usage", {})


def plan_ok(reserve=0.15):
    """False when background work should wait: limit hit, or the weekly window is past (1 - reserve)."""
    if cooling_down():
        return False
    w = plan_usage().get("windows", {})
    week, five = w.get("seven_day") or {}, w.get("five_hour") or {}
    if (week.get("utilization") or 0) >= 1 - reserve and (week.get("resets_at") or 0) > time.time():
        return False
    return not ((five.get("utilization") or 0) >= 0.97 and (five.get("resets_at") or 0) > time.time())


def cooling_down():
    return _load("cooldown", 0) > time.time()


def _note_limit(err):
    if any(w in str(err).lower() for w in ("usage limit", "rate limit", "429", "overloaded", "limit reached")):
        _save("cooldown", time.time() + 1800)


if __name__ == "__main__":   # self-check: auth, one call, a 2-turn session, streaming, plan usage
    a = auth()
    assert a.get("loggedIn"), f"run `claude` once and log in: {a}"
    print("auth:", a.get("authMethod"), a.get("subscriptionType"))
    text, meta = ask("Reply with exactly one word.", "Capital of France?", model="claude-haiku-4-5")
    assert "paris" in text.lower(), text
    ask("You remember numbers.", "Remember 4217. Reply ok.", model="claude-haiku-4-5", session="selftest")
    text, _ = ask("You remember numbers.", "Which number? Digits only.", model="claude-haiku-4-5", session="selftest")
    assert "4217" in text, text
    forget("selftest")
    assert "".join(stream("Reply with exactly: pong", "ping", model="claude-haiku-4-5")).strip().lower().startswith("pong")
    print("plan usage:", plan_usage().get("windows"))
    print("ok")
