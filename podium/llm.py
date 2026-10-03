"""Every AI call goes through here: complete() / stream(), per-role models, conversations, logging.

Model per role (config.model_for):  "claude-code:<model>"  -> your logged-in `claude` CLI (no API key)
                                     "<provider>/<model>"   -> LiteLLM (anthropic/ bedrock/ gemini/ openai/ ollama_chat/ ...)
Conversations (session=key): the same key continues the same conversation across calls, e.g. one per
competition for the Solver. Claude Code keeps real sessions (--session-id / --resume, cached and auto-compacted);
LiteLLM providers replay a trimmed history stored under data/conversations/.
"""
import json
import subprocess
import time
import uuid

from . import config

_auth_cache = {}


def spec(role):
    """('claude-code' | 'litellm' | 'none', model) for a role."""
    m = config.model_for(role)
    if not m or m == "none":
        return "none", ""
    if m.startswith("claude-code"):
        return "claude-code", m.split(":", 1)[1] if ":" in m else ""
    return "litellm", m


def claude_code_auth():
    """`claude auth status` (cached 10 min): login method and subscription, e.g. claude.ai / max."""
    hit = _auth_cache.get("v")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        p = subprocess.run([config.CLAUDE_BIN, "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
        d = json.loads(p.stdout)
        v = {k: d.get(k) for k in ("loggedIn", "authMethod", "apiProvider", "email", "subscriptionType", "orgName")}
    except Exception as e:
        v = {"loggedIn": False, "error": str(e)[:200]}
    _auth_cache["v"] = (time.time(), v)
    return v


# ---------------------------------------------------------------- conversations
def _db():
    from . import db
    return db


def session_state(key, role="experiment"):
    """How many turns the NEXT call on this key continues (0 = it will start a fresh conversation)."""
    if not key or not config.CONVERSATIONS:
        return 0
    s = _db().setting(f"conv:{key}", {})
    if s.get("spec") != config.model_for(role) or s.get("turns", 0) >= config.CONVERSATION_MAX_TURNS:
        return 0
    return s.get("turns", 0)


def reset_session(key):
    _db().set_setting(f"conv:{key}", {})
    f = config.DATA_DIR / "conversations" / f"{key.replace(':', '__')}.jsonl"
    if f.exists():
        f.unlink()


def _history_file(key):
    d = config.DATA_DIR / "conversations"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key.replace(':', '__')}.jsonl"


# ---------------------------------------------------------------- calls
LIMIT_WORDS = ("usage limit", "rate limit", "rate_limit", "429", "overloaded", "limit reached", "quota", "credit balance")


def cooldown_until():
    return _db().setting("ai_cooldown_until", 0)


def _note_limit(err):
    """Claude plan/rate limit hit: pause Claude Code calls for 30 min (or until the stated reset) and alert once."""
    msg = str(err).lower()
    if not any(w in msg for w in LIMIT_WORDS):
        return
    until = time.time() + 1800
    if cooldown_until() < time.time():
        _db().emit("Fleet", "agent.error", {"error": f"AI usage/rate limit reached; Claude Code calls paused 30 min"
                                                     f"{' (using fallback ' + config.FALLBACK_MODEL + ')' if config.FALLBACK_MODEL else ''}. {str(err)[:200]}"})
    _db().set_setting("ai_cooldown_until", until)


def complete(system, prompt, max_tokens=16000, role="experiment", agent=None, competition=None, purpose=None,
             session=None, tools=None):
    """One AI call, logged to llm_calls. Returns (text, tokens, cost_usd)."""
    backend, model = spec(role)
    if backend == "claude-code" and cooldown_until() > time.time():
        if not config.FALLBACK_MODEL:
            raise RuntimeError(f"Claude Code is cooling down after a usage limit until "
                               f"{time.strftime('%H:%M', time.localtime(cooldown_until()))} (set PODIUM_FALLBACK_MODEL to keep going)")
        fb = config.FALLBACK_MODEL
        backend, model = ("claude-code", fb.split(":", 1)[1]) if fb.startswith("claude-code") else ("litellm", fb)
        session = None  # fallback calls don't continue the Claude Code conversation
    if backend == "none":
        raise RuntimeError(f"no AI model configured for role '{role}'")
    rec = {"agent": agent, "competition": competition, "purpose": purpose or role, "ok": 1, "error": None,
           "model": model or "default", "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "cost_usd": 0.0}
    continuing = session_state(session, role) if session else 0
    t0 = time.time()
    try:
        if backend == "claude-code":
            rec.update(backend="claude-code", billing="plan" if claude_code_auth().get("authMethod") == "claude.ai" else "api")
            text = _claude_code(system, prompt, model, rec, session, continuing, role, tools)
        else:
            rec.update(backend="litellm:" + model.split("/")[0], billing="local" if "ollama" in model else "api")
            text = _litellm(system, prompt, model, max_tokens, rec, session, continuing, role)
        return text, rec["input_tokens"] + rec["output_tokens"] + rec["cache_tokens"], rec["cost_usd"]
    except Exception as e:
        rec.update(ok=0, error=str(e)[:500])
        if backend == "claude-code":
            _note_limit(e)
        raise
    finally:
        _log(rec, t0)


def record_plan_usage(info):
    """Store Claude Code's rate_limit_event (real plan utilization per window, reset times)."""
    w = info.get("unifiedWindows") or {}
    _db().set_setting("plan_usage", {"status": info.get("status"), "type": info.get("rateLimitType"),
                                     "windows": {k: {"utilization": v.get("utilization"), "resets_at": v.get("resetsAt")}
                                                 for k, v in w.items()}, "at": time.time()})
    if info.get("status") not in (None, "allowed", "allowed_warning"):
        until = max([v.get("resetsAt") or 0 for v in w.values()] + [info.get("resetsAt") or 0]) or time.time() + 1800
        _db().set_setting("ai_cooldown_until", until)


def plan_usage():
    return _db().setting("plan_usage", {})


def plan_ok(role="experiment"):
    """False when the fleet should hold its Claude Code work: limit reached, or the weekly reserve is reached.
    The Copilot (you talking) is never held back by the reserve."""
    if spec(role)[0] != "claude-code":
        return True
    if cooldown_until() > time.time():
        return False
    w = plan_usage().get("windows", {})
    if role == "copilot":
        return True
    week, session = (w.get("seven_day") or {}), (w.get("five_hour") or {})
    if week.get("utilization") is not None and week["utilization"] >= 1 - config.PLAN_RESERVE \
            and (week.get("resets_at") or 0) > time.time():
        return False
    if session.get("utilization") is not None and session["utilization"] >= 0.97 and (session.get("resets_at") or 0) > time.time():
        return False
    return True


def probe_plan_usage():
    """Tiny call (Haiku) just to read the current plan utilization."""
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose", "--tools", "", "--model",
           "claude-haiku-4-5", "--system-prompt", "Reply with: ok", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    p = subprocess.run(cmd, input="ok", capture_output=True, text=True, timeout=120, cwd=config.DATA_DIR)
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "rate_limit_event":
            record_plan_usage(d.get("rate_limit_info", {}))
    return plan_usage()


def _claude_code(system, prompt, model, rec, session, continuing, role, tools):
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose", "--system-prompt", system,
           "--setting-sources", "", "--strict-mcp-config"]
    allowed = [t for t in (tools or []) if t in ("WebSearch", "WebFetch")]  # research only; never file/shell tools
    cmd += ["--tools", *allowed, "--allowedTools", *allowed] if allowed else ["--tools", ""]
    if model:
        cmd += ["--model", model]
    sid = None
    if session and config.CONVERSATIONS:
        if continuing:
            sid = _db().setting(f"conv:{session}", {})["id"]
            cmd += ["--resume", sid]
        else:
            sid = str(uuid.uuid4())
            cmd += ["--session-id", sid]
    else:
        cmd += ["--no-session-persistence"]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=2400, cwd=config.DATA_DIR)
    d = {}
    for line in p.stdout.splitlines():  # stream-json: keep the final result, record the plan usage event
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            d = ev
        elif ev.get("type") == "rate_limit_event":
            record_plan_usage(ev.get("rate_limit_info", {}))
    if p.returncode or d.get("is_error") or "result" not in d:
        if continuing and session:  # the stored session is gone or broken: start a fresh one once
            reset_session(session)
            return _claude_code(system, prompt, model, rec, session, 0, role, tools)
        raise RuntimeError(f"claude CLI failed: {d.get('result') or p.stderr[-500:]}")
    if sid:
        _db().set_setting(f"conv:{session}", {"id": sid, "spec": config.model_for(role), "turns": continuing + 1,
                                              "at": _db().now()})
    u = d.get("usage", {})
    rec.update(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
               cache_tokens=u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
               cost_usd=d.get("total_cost_usd", 0.0))
    return d["result"]


def _litellm(system, prompt, model, max_tokens, rec, session, continuing, role):
    import litellm  # imported lazily: slow import, unneeded for claude-code

    history = []
    if session and config.CONVERSATIONS:
        f = _history_file(session)
        if continuing and f.exists():
            history = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
            history = history[:2] + history[2:][-20:]  # keep the briefing + the last 10 exchanges
        elif f.exists():
            f.unlink()
    msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": prompt}]
    r = litellm.completion(model=model, max_tokens=max_tokens, timeout=900, messages=msgs)
    try:
        cost = litellm.completion_cost(completion_response=r) or 0.0
    except Exception:
        cost = 0.0  # local / unpriced models (Ollama)
    text = r.choices[0].message.content or ""
    if session and config.CONVERSATIONS:
        with open(_history_file(session), "a") as fh:
            fh.write(json.dumps({"role": "user", "content": prompt}) + "\n")
            fh.write(json.dumps({"role": "assistant", "content": text}) + "\n")
        _db().set_setting(f"conv:{session}", {"spec": config.model_for(role), "turns": continuing + 1, "at": _db().now()})
    rec.update(input_tokens=r.usage.prompt_tokens or 0, output_tokens=r.usage.completion_tokens or 0, cost_usd=cost)
    return text


def _log(rec, t0):
    rec["seconds"] = round(time.time() - t0, 1)
    try:
        db = _db()
        db.x("INSERT INTO llm_calls (ts, agent, competition, purpose, backend, model, billing, input_tokens, "
             "output_tokens, cache_tokens, cost_usd, seconds, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             db.now(), rec["agent"], rec["competition"], rec["purpose"], rec.get("backend", ""), rec.get("model", ""),
             rec.get("billing", ""), rec["input_tokens"], rec["output_tokens"], rec["cache_tokens"], rec["cost_usd"],
             rec["seconds"], rec["ok"], rec["error"])
    except Exception:
        pass  # logging must never break the fleet


def stream(system, prompt, max_tokens=8000, role="copilot", agent=None, competition=None, purpose=None,
           read_dir=None, images=None):
    """Like complete(), but yields text deltas as they arrive (no conversation state). Logged at the end.
    read_dir: Claude Code may use its Read tool ONLY inside this directory (attached images/PDFs).
    images: file paths sent as image parts to LiteLLM vision models."""
    backend, model = spec(role)
    if backend == "none":
        raise RuntimeError(f"no AI model configured for role '{role}'")
    rec = {"agent": agent, "competition": competition, "purpose": purpose or role, "ok": 1, "error": None,
           "model": model or "default", "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "cost_usd": 0.0}
    t0 = time.time()
    try:
        if backend == "claude-code":
            rec.update(backend="claude-code", billing="plan" if claude_code_auth().get("authMethod") == "claude.ai" else "api")
            cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                   "--system-prompt", system, "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
            cmd += ["--tools", "Read", "--allowedTools", f"Read(/{read_dir}/**)"] if read_dir else ["--tools", ""]
            if model:
                cmd += ["--model", model]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 cwd=config.DATA_DIR)
            p.stdin.write(prompt)
            p.stdin.close()
            seen_text = False
            try:
                for line in p.stdout:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "stream_event":
                        ev = d.get("event", {})
                        if ev.get("type") == "message_start" and seen_text:
                            yield "\n\n"  # new assistant message after an internal tool call (e.g. reading an image)
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            seen_text = True
                            yield delta.get("text", "")
                    elif d.get("type") == "rate_limit_event":
                        record_plan_usage(d.get("rate_limit_info", {}))
                    elif d.get("type") == "result":
                        if d.get("is_error"):
                            raise RuntimeError(f"claude CLI failed: {d.get('result')}")
                        u = d.get("usage", {})
                        rec.update(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                                   cache_tokens=u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
                                   cost_usd=d.get("total_cost_usd", 0.0))
            finally:
                if p.poll() is None:
                    p.kill()  # client stopped the stream
                p.wait()
        else:
            import litellm
            rec.update(backend="litellm:" + model.split("/")[0], billing="local" if "ollama" in model else "api")
            chunks = []
            user = prompt
            if images:
                import base64
                import mimetypes
                user = [{"type": "text", "text": prompt}] + [
                    {"type": "image_url", "image_url": {"url": f"data:{mimetypes.guess_type(p)[0] or 'image/png'};base64,"
                                                              f"{base64.b64encode(open(p, 'rb').read()).decode()}"}}
                    for p in images]
            for ch in litellm.completion(model=model, max_tokens=max_tokens, stream=True, timeout=900,
                                         messages=[{"role": "system", "content": system},
                                                   {"role": "user", "content": user}]):
                chunks.append(ch)
                text = ch.choices[0].delta.content if ch.choices else None
                if text:
                    yield text
            try:
                full = litellm.stream_chunk_builder(chunks)
                rec.update(input_tokens=full.usage.prompt_tokens or 0, output_tokens=full.usage.completion_tokens or 0,
                           cost_usd=litellm.completion_cost(completion_response=full) or 0.0)
            except Exception:
                pass
    except Exception as e:
        rec.update(ok=0, error=str(e)[:500])
        raise
    finally:
        _log(rec, t0)
