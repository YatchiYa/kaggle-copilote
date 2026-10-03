"""Podium Copilot: a chat assistant that sees the whole system and acts through tools.

Provider-agnostic tool use: the model emits one fenced ```tool {"name": ..., "args": {...}}``` block per step,
we execute it and feed the result back, up to MAX_STEPS, then it answers in markdown.
"""
import json
import re
from datetime import datetime, timezone

from . import agents, config, db, llm

MAX_STEPS = 6

SYSTEM = """You are Podium Copilot, the co-pilot of an autonomous Kaggle competition fleet, with the judgement of a
Kaggle Grandmaster who has 20+ years of competition experience. The user is the fleet's owner. You know the live system
state (snapshot below) and you can act on it with tools.

How to use a tool: reply with exactly one fenced block and nothing else:
```tool
{"name": "<tool>", "args": {...}}
```
You will get the result back, then continue (more tools, or the final answer). To run several tools at once, put a
JSON list of calls in one block. After a tool block, STOP and wait for the results: never write the answer in the
same message as a tool call. Use tools whenever the answer depends
on data you do not see in the snapshot. Never invent numbers, ranks or competitions.

Tools:
- fleet(): all competitions with state, rank, CV, LB, quota, spend; KPIs; open decisions.
- competition(slug): detail: run, experiments (CV, verdicts), submissions, current strategy, data profile.
- leaderboard(slug): our rank, the teams around us, top 5, top-10% cutoff.
- strategy(slug): the full current strategy markdown.
- events(limit=30, competition=None): recent fleet activity (newest last).
- search_kaggle(query="", category="", sort_by="latestDeadline"): live Kaggle competitions (title, prize, deadline,
  teams, code-only, metric, whether the user joined). category: featured|research|playground|gettingStarted|community.
- assess(slug): rank-chance score 0-100 with reasons (data size, leaderboard saturation, teams, time left, category).
- recommend(slug, reason): put a "join this competition" request in the Decisions inbox and email the user.
- start(slug): opt in; the fleet starts it within a minute once the user has joined on Kaggle.
- pause(slug) / stop(slug): pause, or stop a competition (it stays stopped until start; results are kept).
- archive(slug): remove a competition from all lists and recommendations (files and history kept).
- set_setting(key, value): change a setting. Keys: PODIUM_MAX_ACTIVE, PODIUM_MIN_CHANCE, PODIUM_MAX_EXPERIMENTS,
  PODIUM_REFLECT_EVERY, PODIUM_AUTO_SUBMIT, PODIUM_REVIEW, PODIUM_DAILY_SUBMISSIONS, PODIUM_SCOUT_SECONDS,
  PODIUM_WEEKLY_CAP_USD, PODIUM_COMP_CAP_USD, PODIUM_COMP_CAP_HOURS, PODIUM_NOTIFY_EMAIL, PODIUM_MODEL.
- scout_now(): run the competition scout on the next fleet pass instead of waiting for the hourly run.
- kaggle_submissions(slug): the user's real Kaggle submission history for a competition (status, scores, errors),
  including dedicated projects like gemma-4-developer-agent.
- read_file(slug, path, lines=150): a file under data/competitions/<slug>/ (e.g. experiments/<id>/main.py, log.txt).
- resolve_decision(slug): clear the open decision(s) for a competition (e.g. a join request the user declined).
- navigate(path): open a page in the user's dashboard right now. Use it when the user asks to open/show/go to
  something. Paths: overview, competitions, competition/<slug>, competition/<slug>/<tab> (tabs: leaderboard,
  strategy, experiments, submissions, files), submissions, activity, decisions, alerts, spend, platforms, settings.

Linking: whenever you mention a competition or a page, make it a markdown link the user can click, e.g.
[Store Sales](#/competition/store-sales-time-series-forecasting), [its leaderboard](#/competition/<slug>/leaderboard),
[Decisions](#/decisions). External Kaggle links are fine too.

Before a tool call you may write ONE short sentence saying what you are checking (it is shown live).
Finish every final answer with 2-4 follow-ups the user is likely to want next, phrased as the user would type them
(prefer things you can do with your tools), in this exact block at the very end:
```suggest
["first follow-up", "second follow-up"]
```

Facts about the fleet: it only submits through its review gate (you cannot submit directly); joining a competition
must be done by the user on kaggle.com (Kaggle requires a human to accept rules); file-submission competitions are
supported, code-only (notebook) competitions are not yet.
Agent, paper and code-only competitions are not run by the fleet, but they can be done as a dedicated project that
the user builds with their developer (Claude Code session). The user deliberately flagged Google DeepMind's
gemma-4-developer-agent ($65k) as the flagship dedicated project: never suggest clearing that decision; explain
that it is handled outside the fleet. The paper track (gemma-4-developer-agent-paper) is its natural companion
write-up about Podium itself.

Answer style: like a senior colleague in a live conversation: direct, specific, evidence-based, markdown, short
paragraphs and bullets, real numbers. Say what
you did when you used an action tool. If something cannot be done, say so plainly and propose the best alternative."""


# ---------------------------------------------------------------- tools
def _fleet():
    from .api import fleet
    f = fleet()
    keep = ("slug", "title", "state", "paused", "rules_accepted", "interest_score", "best_cv", "lb_public", "lb_rank", "lb_teams", "lb_p10",
            "cv_lb_gap", "diverged", "submissions_today", "quota", "spend_usd", "strategy_version", "experiments",
            "running", "deadline", "metric", "kind", "category")
    return {"kpis": f["kpis"], "fleet_paused": f["fleet_paused"], "config": f["config"],
            "competitions": [{k: c.get(k) for k in keep} for c in f["competitions"]
                             if c["state"] not in ("scouted", "archived", "finished") or c["rules_accepted"]
                             or (c["interest_score"] or 0) >= config.MIN_CHANCE],
            "open_decisions": db.q("SELECT competition_slug, kind, detail FROM human_tasks WHERE status='open'")}


def _competition(slug):
    from .api import competition, get_strategy
    d = competition(slug)
    s = get_strategy(slug)
    return {"competition": {k: v for k, v in d["competition"].items() if k not in ("why",)}, "why": d["competition"]["why"],
            "run": d["run"], "budget": d["budget"], "gap": d["gap"],
            "experiments": [{k: e.get(k) for k in ("id", "parent_id", "summary", "cv_mean", "cv_std", "cv_scheme",
                                                    "critic_flags", "seconds", "cost_usd")} for e in d["experiments"][-15:]],
            "submissions": [{k: x.get(k) for k in ("experiment_id", "cv_mean", "lb_public", "reason", "is_final_pick",
                                                   "created_at")} for x in d["submissions"][:15]],
            "strategy_version": s["meta"].get("version"),
            "strategy_head": (s["versions"][0]["markdown"][:2500] if s["versions"] else None),
            "profile_head": s["profile"][:1500]}


def _leaderboard(slug):
    from .api import leaderboard
    lb = leaderboard(slug)
    return {k: lb.get(k) for k in ("teams", "me", "p10", "median", "updated")} | {"top5": lb.get("top", [])[:5],
                                                                                 "around": lb.get("around", [])}


def _strategy(slug):
    p = agents.strategy_dir(slug) / "plan.md"
    return p.read_text() if p.exists() else "No strategy yet."


def _events(limit=30, competition=None):
    sql, args = "SELECT * FROM events WHERE type != 'scout.done'", []
    if competition:
        sql += " AND competition=?"; args.append(competition)
    rows = db.q(sql + " ORDER BY id DESC LIMIT ?", *args, min(int(limit), 100))[::-1]
    return [f"{e['ts'][5:16]} {e['agent']}: {db.describe(e['type'], json.loads(e['payload']), e['competition'])}"
            for e in rows]


def _search_kaggle(query="", category="", sort_by="latestDeadline"):
    now = datetime.now(timezone.utc)
    out = []
    for page in (1, 2):
        r = agents.kaggle().competitions_list(search=query or None, category=category or None, sort_by=sort_by, page=page)
        for c in r.competitions or []:
            if c.deadline and c.deadline.replace(tzinfo=timezone.utc) > now:
                out.append({"slug": c.ref.rstrip("/").split("/")[-1], "title": c.title, "host": c.organization_name,
                            "prize": c.reward, "category": c.category, "deadline": c.deadline.date().isoformat(),
                            "teams": c.team_count, "code_only": c.is_kernels_submissions_only,
                            "metric": c.evaluation_metric, "joined": c.user_has_entered, "kind": agents.kind_of(c)})
        if len(r.competitions or []) < 20:
            break
    return out[:40]


def _assess(slug):
    now = datetime.now(timezone.utc)
    r = agents.kaggle().competitions_list(search=slug)
    c = next((x for x in r.competitions or [] if x.ref.rstrip("/").split("/")[-1] == slug), None)
    if not c:
        return {"error": f"{slug} not found among live competitions"}
    metric = c.evaluation_metric or ""
    hib = 0 if any(w in metric.lower() for w in agents.LOWER_IS_BETTER) else 1
    try:
        size = sum(f.total_bytes or 0 for f in agents.kaggle().competition_list_files(slug, page_size=200).files)
    except Exception:
        size = None
    lb = agents.leaderboard(slug, max_age_s=86400, hib=bool(hib))
    days = (c.deadline.replace(tzinfo=timezone.utc) - now).days
    score, why = agents.chance(c, agents.kind_of(c), days, size, lb, hib, metric)
    return {"slug": slug, "chance": score, "why": why, "code_only": c.is_kernels_submissions_only,
            "leaderboard_top": lb[0] if lb else None, "teams": len(lb) or c.team_count}


def _recommend(slug, reason=""):
    db.open_task(slug, "accept_rules", f"https://www.kaggle.com/competitions/{slug}/rules",
                 f"Recommended by Copilot: {reason}".strip())
    db.emit("Copilot", "competition.recommended", {"title": slug, "chance": "copilot",
                                                   "url": f"https://www.kaggle.com/competitions/{slug}/rules"},
            competition=slug)
    return {"ok": True, "note": "Added to Decisions and emailed."}


def _start(slug):
    from .api import activate
    activate(slug)
    return {"ok": True, "note": "Opted in; the Gatekeeper starts it within a minute if the user joined on Kaggle."}


def _pause(slug):
    from .api import pause
    return pause(slug)


def _stop(slug):
    from .api import stop
    return stop(slug)


def _archive(slug):
    from .api import archive
    return archive(slug)


def _set_setting(key, value):
    from .api import EDITABLE, write_env
    if key not in EDITABLE:
        return {"error": f"{key} is not editable"}
    attr, typ = EDITABLE[key]
    val = (str(value).lower() in ("1", "true", "yes", "on")) if typ is bool else typ(value)
    setattr(config, attr, val)
    write_env(key, int(val) if typ is bool else val)
    if key == "PODIUM_WEEKLY_CAP_USD":
        db.set_setting("weekly_cap_usd", val)
    db.emit("Copilot", "settings.changed", {key: val})
    return {"ok": True, key: val}


def _kaggle_submissions(slug):
    from .api import kaggle_submissions
    rows = kaggle_submissions(slug)
    if not isinstance(rows, list):
        return {"error": "Kaggle submission history unavailable"}
    return [{k: r.get(k) for k in ("date", "description", "status", "publicScore", "privateScore", "errorDescription")}
            for r in rows[:15]]


def _scout_now():
    db.set_setting("scout_at", 0)
    return {"ok": True, "note": "Scout runs on the next fleet pass (within a minute)."}


def _read_file(slug, path, lines=150):
    root = agents.comp_dir(slug).resolve()
    p = (root / path).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        return {"error": "file not found"}
    with open(p, errors="replace") as fh:
        return "".join(line for _, line in zip(range(min(int(lines), 400)), fh))


def _resolve_decision(slug):
    n = db.one("SELECT COUNT(*) n FROM human_tasks WHERE competition_slug=? AND status='open'", slug)["n"]
    db.x("UPDATE human_tasks SET status='done' WHERE competition_slug=? AND status='open'", slug)
    db.emit("Copilot", "human_task.done", {"kind": "resolved by copilot"}, competition=slug)
    return {"ok": True, "cleared": n}


PAGES = ("overview", "competitions", "competition", "submissions", "activity", "decisions", "alerts", "spend",
         "platforms", "settings")


def _navigate(path):
    path = str(path).strip().lstrip("#/")
    if path.split("/")[0] not in PAGES:
        return {"error": f"unknown page {path}"}
    return {"ok": True, "path": path}


TOOLS = {"fleet": _fleet, "competition": _competition, "leaderboard": _leaderboard, "strategy": _strategy,
         "events": _events, "search_kaggle": _search_kaggle, "assess": _assess, "recommend": _recommend,
         "start": _start, "pause": _pause, "stop": _stop, "archive": _archive, "set_setting": _set_setting, "scout_now": _scout_now,
         "read_file": _read_file, "resolve_decision": _resolve_decision, "kaggle_submissions": _kaggle_submissions, "navigate": _navigate}
ACTIONS = {"recommend", "start", "pause", "stop", "archive", "set_setting", "scout_now", "resolve_decision", "navigate"}


def snapshot(context=None):
    f = _fleet()
    lines = [f"Now: {db.now()} UTC. Model: {config.MODEL}. Fleet {'PAUSED' if f['fleet_paused'] else 'running'}.",
             f"KPIs: {json.dumps(f['kpis'])}",
             "Competitions in play / recommended:"]
    for c in f["competitions"]:
        rank = f"#{c['lb_rank']}/{c['lb_teams']}" if c["lb_rank"] else "unranked"
        lines.append(f"- {c['slug']}: {c['state']}{' (paused)' if c['paused'] else ''}, "
                     f"{'JOINED on Kaggle' if c['rules_accepted'] else 'not joined'}, chance {c['interest_score']}, "
                     f"rank {rank}, best CV {c['best_cv']}, LB {c['lb_public']}, CV-LB gap {c['cv_lb_gap']}, "
                     f"{c['experiments']} experiments, strategy v{c['strategy_version']}, spend ${c['spend_usd']:.2f}, "
                     f"subs today {c['submissions_today']}/{c['quota']}, deadline {c['deadline'][:10]}")
    for plan in sorted((config.ROOT / "projects").glob("*/PLAN.md")):
        head = plan.read_text().splitlines()
        lines.append(f"Dedicated project '{plan.parent.name}' ({plan}): {head[0].lstrip('# ')}; "
                     + " ".join(l for l in head[1:8] if l.startswith("**"))[:600])
    if f["open_decisions"]:
        lines.append("Open decisions: " + "; ".join(f"{d['competition_slug']} ({d['kind']})" for d in f["open_decisions"]))
    lines.append("Recent activity:\n" + "\n".join(_events(15)))
    if context:
        lines.append(f"The user is currently viewing: {context}")
    return "\n".join(lines)


def run_tool(name, args):
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    try:
        return TOOLS[name](**(args or {}))
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
    except Exception as e:
        return {"error": str(e)[:400]}


HIDDEN = re.compile(r"```(?:tool|suggest)\b.*?(?:```|$)", re.S)
PARTIAL_FENCE = re.compile(r"`{1,3}(?:t(?:o(?:o(?:l)?)?)?|s(?:u(?:g(?:g(?:e(?:s(?:t)?)?)?)?)?)?)?$")


def visible(text):
    """Text the user should see: tool/suggest blocks removed, a possibly-starting fence held back."""
    v = HIDDEN.sub("", text)
    m = PARTIAL_FENCE.search(v)
    return v[:m.start()] if m else v


def summarize(name, result):
    """One readable line for the step timeline."""
    if isinstance(result, dict) and "error" in result:
        return f"error: {result['error'][:120]}"
    try:
        if name == "competition":
            c = result["competition"]
            rank = f"rank #{c['lb_rank']}/{c['lb_teams']}" if c.get("lb_rank") else "unranked"
            return f"{c['state']} · {rank} · {len(result['experiments'])} recent experiments · strategy v{result.get('strategy_version')}"
        if name == "leaderboard":
            me = result.get("me") or {}
            return f"{result.get('teams')} teams · you #{me.get('Rank', '—')} · top-10% cutoff {result.get('p10')}"
        if name == "fleet":
            return f"{len(result['competitions'])} competitions · {len(result['open_decisions'])} open decisions"
        if name == "assess":
            return f"chance {result['chance']}/100 · {'code-only' if result.get('code_only') else 'file submissions'}"
        if name in ("search_kaggle", "events", "kaggle_submissions"):
            return f"{len(result)} results"
    except Exception:
        pass
    if isinstance(result, list):
        return f"{len(result)} items"
    if isinstance(result, str):
        return f"{len(result.splitlines())} lines"
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in list(result.items())[:3]) if isinstance(result, dict) else ""


TEXT_EXT = {".txt", ".md", ".py", ".json", ".csv", ".tsv", ".yaml", ".yml", ".log", ".ipynb", ".html", ".js", ".ts",
            ".sql", ".toml", ".ini", ".cfg", ".sh", ".xml", ".env.example"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def upload_dir():
    d = config.DATA_DIR / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def kind_of_file(name):
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    return "image" if ext in IMAGE_EXT else "pdf" if ext == ".pdf" else "text" if ext in TEXT_EXT else "other"


def attachment_context(attachments):
    """Inline text files; list images/PDFs for the restricted Read tool. Returns (text, image_paths)."""
    parts, images = [], []
    for a in attachments or []:
        p = (upload_dir() / a["id"]).resolve()
        if not p.is_relative_to(upload_dir()) or not p.is_file():
            continue
        k = kind_of_file(p.name)
        if k == "text":
            body = p.read_text(errors="replace")
            body = "\n".join(body.splitlines()[:300]) if p.suffix.lower() in (".csv", ".tsv") else body[:40000]
            parts.append(f"--- attached file: {p.name} ---\n{body}\n--- end of {p.name} ---")
        elif k in ("image", "pdf"):
            images.append(str(p))
            parts.append(f"--- attached {k}: {p.name} at {p} (open it with the Read tool) ---")
        else:
            parts.append(f"--- attached file {p.name} ({p.stat().st_size} bytes, binary: not readable) ---")
    return "\n".join(parts), images


def chat_stream(messages, context=None):
    """Yields events: delta, step, step_done, navigate, done, error."""
    att_text, att_images = attachment_context(messages[-1].get("attachments"))
    lines = []
    for i, m in enumerate(messages[-12:]):
        names = ", ".join(a.get("name", "") for a in m.get("attachments") or [])
        lines.append(f"{m['role'].upper()}: {m['content']}" + (f"\n[attached: {names}]" if names else ""))
    convo = "\n\n".join(lines) + (f"\n\n# Attachments of the last message\n{att_text}" if att_text else "")
    system = SYSTEM + "\n\n# Live system snapshot\n" + snapshot(context)
    transcript, steps, full = convo, [], ""
    for step in range(MAX_STEPS + 1):
        last = step == MAX_STEPS
        prompt = transcript + ("\n\nASSISTANT (final answer now, no more tools):" if last else "\n\nASSISTANT:")
        full, sent = "", ""
        for d in llm.stream(system, prompt, agent="Copilot", purpose="chat",
                            read_dir=str(upload_dir()) if att_images else None, images=att_images or None):
            full += d
            v = visible(full)
            if v.startswith(sent) and len(v) > len(sent):
                yield {"type": "delta", "text": v[len(sent):]}
                sent = v
        blocks = [] if last else re.findall(r"```tool\s*(.*?)```", full, re.S)
        if not blocks:
            break
        calls, bad = [], False
        for b in blocks:  # one call per block, or a JSON list of calls (parallel tool use)
            try:
                parsed = json.loads(b.strip())
                calls += parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                bad = True
        if bad and not calls:
            transcript += f"\n\nASSISTANT: {full}\n\nTOOL ERROR: the tool block was not valid JSON."
            continue
        note = visible(full).strip()
        results = []
        for i, call in enumerate(calls[:6]):
            name, args = call.get("name"), call.get("args") or {}
            yield {"type": "step", "tool": name, "args": args, "action": name in ACTIONS, "note": note if i == 0 else ""}
            result = run_tool(name, args)
            ok = not (isinstance(result, dict) and "error" in result)
            steps.append({"tool": name, "args": args, "action": name in ACTIONS, "ok": ok})
            yield {"type": "step_done", "tool": name, "ok": ok, "summary": summarize(name, result)}
            if name == "navigate" and ok:
                yield {"type": "navigate", "path": result["path"]}
            body = result if isinstance(result, str) else json.dumps(result, default=str)
            results.append(f"TOOL RESULT ({name} {json.dumps(args)}):\n{body[:6000]}")
        transcript += f"\n\nASSISTANT: {full}\n\n" + "\n\n".join(results)
    sug = re.search(r"```suggest\s*(\[.*?\])\s*```", full, re.S)
    try:
        suggestions = [str(x)[:140] for x in json.loads(sug.group(1))][:4] if sug else []
    except json.JSONDecodeError:
        suggestions = []
    reply = visible(full).strip()
    db.emit("Copilot", "copilot.answered", {"question": messages[-1]["content"][:200], "tools": [x["tool"] for x in steps]})
    yield {"type": "done", "reply": reply, "suggestions": suggestions, "steps": steps}


def chat(messages, context=None):
    """Non-streaming wrapper: returns the final {reply, suggestions, steps}."""
    out = {}
    for ev in chat_stream(messages, context):
        if ev["type"] == "done":
            out = ev
    return out


if __name__ == "__main__":  # offline check of the tool-call parser and executor
    assert run_tool("nope", {})["error"].startswith("unknown")
    assert "error" in run_tool("read_file", {"slug": "titanic", "path": "../../podium.db"})
    assert visible("Checking.\n```tool\n{\"name\": \"fleet\"}\n```") == "Checking.\n"
    assert visible("Answer\n``") == "Answer\n" and visible("Done.\n```sugg") == "Done.\n"
    assert visible("x ```suggest\n[\"a\"]\n```") == "x "
    assert run_tool("navigate", {"path": "#/competition/titanic/leaderboard"})["path"] == "competition/titanic/leaderboard"
    print("ok")
