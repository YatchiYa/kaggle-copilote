import asyncio
import json
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
import numpy as np
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from . import agents, config, db


@asynccontextmanager
async def lifespan(app):
    db.init()
    task = asyncio.create_task(agents.run_fleet()) if app.state.fleet else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Podium", lifespan=lifespan)
app.state.fleet = True
STATIC = config.ROOT / "podium" / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def best(slug, hib):
    agg = "MAX" if hib else "MIN"
    return db.one(f"SELECT {agg}(e.cv_mean) AS cv FROM experiments e JOIN runs r ON e.run_id=r.id "
                  f"WHERE r.competition_slug=? AND e.critic_flags='[]'", slug)["cv"]


@app.get("/api/fleet")
def fleet():
    comps = db.q("SELECT * FROM competitions ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'project' THEN 1 "
                 "WHEN 'needs_rules' THEN 2 WHEN 'done' THEN 3 WHEN 'scouted' THEN 4 WHEN 'stopped' THEN 5 ELSE 6 END, "
                 "interest_score DESC, deadline")
    for c in comps:
        c["best_cv"] = best(c["slug"], c["higher_is_better"])
        lb = db.one("SELECT lb_public FROM submissions WHERE competition_slug=? AND lb_public IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 1", c["slug"])
        c["lb_public"] = lb and lb["lb_public"]
        run = db.one("SELECT engine FROM runs WHERE competition_slug=? ORDER BY started_at DESC LIMIT 1", c["slug"])
        c["engine"] = run and run["engine"]
        c["submissions_today"] = agents.today_count(c["slug"])
        c.update(agents.cv_lb_gap(c["slug"]))
        c["quota"] = agents.quota_for(c)
        c["spend_usd"] = agents.spend(c["slug"])["usd"]
        c["strategy_version"] = db.setting(f"strategy:{c['slug']}", {}).get("version")
        c["experiments"] = db.one("SELECT COUNT(*) n FROM experiments e JOIN runs r ON e.run_id=r.id "
                                  "WHERE r.competition_slug=?", c["slug"])["n"]
        c["running"] = bool(agents._running.get(c["slug"]) and agents._running[c["slug"]].is_alive())
        if c["state"] == "project":
            p = db.setting(f"proj:{c['slug']}", {})
            c["project"] = {k: p.get(k) for k in ("count", "status", "best", "latest")}
            c["lb_public"] = c["lb_public"] if c["lb_public"] is not None else p.get("best")
    day = db.now()[:10]
    since = db.one("SELECT datetime('now','-1 day') d")["d"].replace(" ", "T")
    latest = db.q("SELECT agent, type, competition, ts, payload FROM events WHERE id IN "
                  "(SELECT MAX(id) FROM events GROUP BY agent)")
    for e in latest:
        e["payload"] = json.loads(e["payload"])
        e["text"] = db.describe(e["type"], e["payload"], e["competition"])
    return {"competitions": comps, "fleet_paused": db.setting("fleet_paused", False),
            "kpis": {"active": sum(c["state"] == "active" for c in comps),
                     "experiments_today": db.one("SELECT COUNT(*) n FROM experiments WHERE created_at >= ?", day)["n"],
                     "spend_week": agents.spend(days=7)["usd"],
                     "open_tasks": db.one("SELECT COUNT(*) n FROM human_tasks WHERE status='open'")["n"],
                     "errors_24h": db.one("SELECT COUNT(*) n FROM events WHERE type='agent.error' AND ts >= ?", since)["n"],
                     "submissions_today": db.one("SELECT COUNT(*) n FROM submissions WHERE created_at >= ?", day)["n"]},
            "agents": latest, "server_time": db.now(), "loop_seconds": config.LOOP_SECONDS,
            "config": {"model": config.MODEL, "engine": config.ENGINE, "sandbox": config.SANDBOX,
                       "auto_submit": config.AUTO_SUBMIT, "min_chance": config.MIN_CHANCE,
                       "max_active": config.MAX_ACTIVE}}


@app.get("/api/competitions/{slug}")
def competition(slug: str):
    c = db.one("SELECT * FROM competitions WHERE slug=?", slug)
    if not c:
        raise HTTPException(404)
    run = db.one("SELECT * FROM runs WHERE competition_slug=? ORDER BY started_at DESC LIMIT 1", slug)
    exps = db.q("SELECT e.* FROM experiments e JOIN runs r ON e.run_id=r.id WHERE r.competition_slug=? "
                "ORDER BY e.created_at", slug)
    subs = db.q("SELECT s.*, e.cv_mean, e.summary FROM submissions s JOIN experiments e ON s.experiment_id=e.id "
                "WHERE s.competition_slug=? ORDER BY s.created_at DESC", slug)
    costs = {r["exp"]: r for r in db.q(
        "SELECT json_extract(payload,'$.experiment_id') exp, SUM(cost_usd) usd, SUM(tokens) tokens FROM events "
        "WHERE competition=? AND type='experiment.started' GROUP BY exp", slug)}
    for e in exps:
        e["cost_usd"] = costs.get(e["id"], {}).get("usd") or 0
        e["tokens"] = costs.get(e["id"], {}).get("tokens") or 0
    return {"competition": c, "run": run, "experiments": exps, "submissions": subs, "gap": agents.cv_lb_gap(slug),
            "final_manual": bool(db.setting(f"final_manual:{slug}")),
            "budget": {"usd": agents.spend(slug)["usd"], "cap_usd": run and run["budget_usd"],
                       "hours": agents.hours(slug), "cap_hours": run and run["budget_gpu_h"],
                       "subs_today": agents.today_count(slug),
                       "subs_quota": agents.quota_for(c)}}


@app.get("/api/experiments/{exp_id}/code")
def experiment_code(exp_id: str):
    e = db.one("SELECT code_uri FROM experiments WHERE id=?", exp_id)
    if not e:
        raise HTTPException(404)
    return FileResponse(e["code_uri"], media_type="text/plain")


@app.get("/api/stream")
async def stream(request: Request, run_id: str | None = None, after: int = 0):
    async def gen():
        last = after
        while not await request.is_disconnected():
            sql, args = "SELECT * FROM events WHERE id > ?", [last]
            if run_id:
                sql += " AND run_id = ?"; args.append(run_id)
            for e in db.q(sql + " ORDER BY id LIMIT 200", *args):
                if await request.is_disconnected():
                    return
                last = e["id"]
                e["payload"] = json.loads(e["payload"])
                e["text"] = db.describe(e["type"], e["payload"], e["competition"])
                yield f"id: {e['id']}\ndata: {json.dumps(e)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/events")
def events(limit: int = 200):
    rows = db.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", limit)
    for e in rows:
        e["payload"] = json.loads(e["payload"])
        e["text"] = db.describe(e["type"], e["payload"], e["competition"])
    return rows[::-1]


@app.get("/api/spend")
def spend():
    week = db.q("SELECT agent, SUM(cost_usd) usd, SUM(tokens) tokens FROM events WHERE ts >= ? GROUP BY agent HAVING SUM(tokens) > 0 OR SUM(cost_usd) > 0",
                db.one("SELECT datetime('now','-7 days') d")["d"])
    per_comp = db.q("SELECT competition, SUM(cost_usd) usd, SUM(tokens) tokens FROM events "
                    "WHERE competition IS NOT NULL GROUP BY competition HAVING SUM(tokens) > 0 OR SUM(cost_usd) > 0")
    return {"by_agent": week, "by_competition": per_comp, "week_usd": agents.spend(days=7)["usd"],
            "weekly_cap_usd": db.setting("weekly_cap_usd", config.WEEKLY_CAP_USD)}


@app.get("/api/human-tasks")
def human_tasks():
    return db.q("SELECT * FROM human_tasks ORDER BY status='done', created_at DESC LIMIT 100")


@app.post("/api/human-tasks/{task_id}/done")
def task_done(task_id: str):
    t = db.one("SELECT * FROM human_tasks WHERE id=?", task_id)
    if not t:
        raise HTTPException(404)
    db.x("UPDATE human_tasks SET status='done' WHERE id=?", task_id)
    db.emit("Human", "human_task.done", {"kind": t["kind"]}, competition=t["competition_slug"])
    if t["kind"] == "accept_rules":
        # Opt-in: the Gatekeeper starts it on its next pass (within a minute) once Kaggle confirms the join.
        db.x("UPDATE competitions SET state='needs_rules', opt_in=1 WHERE slug=? AND state IN ('scouted','needs_rules')",
             t["competition_slug"])
    return {"ok": True}


@app.post("/api/competitions/{slug}/pause")
def pause(slug: str):
    c = db.one("SELECT paused FROM competitions WHERE slug=?", slug)
    if not c:
        raise HTTPException(404)
    db.x("UPDATE competitions SET paused=?, note=NULL WHERE slug=?", int(not c["paused"]), slug)
    if not c["paused"]:
        agents.kill_experiments(slug)
    db.emit("Human", "competition.paused" if not c["paused"] else "competition.resumed", {}, competition=slug)
    return {"paused": not c["paused"]}


@app.post("/api/competitions/{slug}/activate")
def activate(slug: str):
    """Ask the Gatekeeper to work on a competition now (also resumes a stopped or archived one)."""
    db.x("UPDATE competitions SET state='needs_rules', opt_in=1, paused=0 WHERE slug=? "
         "AND state IN ('scouted','done','stopped','archived','project')", slug)
    db.x("UPDATE runs SET status='done' WHERE competition_slug=? AND status='running'", slug)
    db.emit("Human", "competition.resumed", {}, competition=slug)
    return {"ok": True}


@app.post("/api/fleet/pause")
def pause_fleet():
    p = not db.setting("fleet_paused", False)
    db.set_setting("fleet_paused", p)
    if p:
        for c in db.q("SELECT slug FROM competitions WHERE state='active'"):
            agents.kill_experiments(c["slug"])
    db.emit("Human", "fleet.paused" if p else "fleet.resumed", {})
    return {"paused": p}


@app.post("/api/submissions/{sub_id}/final")
def final(sub_id: str):
    s = db.one("SELECT * FROM submissions WHERE id=?", sub_id)
    if not s:
        raise HTTPException(404)
    db.set_setting(f"final_manual:{s['competition_slug']}", True)
    db.x("UPDATE submissions SET is_final_pick=? WHERE id=?", int(not s["is_final_pick"]), sub_id)
    return {"is_final_pick": not s["is_final_pick"]}


@app.post("/api/competitions/{slug}/finals/auto")
def finals_auto(slug: str):
    """Hand final-pick selection back to the fleet (best CV + best public LB)."""
    db.set_setting(f"final_manual:{slug}", False)
    c = db.one("SELECT * FROM competitions WHERE slug=?", slug)
    if c:
        agents.pick_finals(c)
    return {"ok": True}


class Budgets(BaseModel):
    weekly_cap_usd: float | None = None
    competition: str | None = None
    cap_usd: float | None = None
    cap_hours: float | None = None


@app.put("/api/budgets")
def budgets(b: Budgets):
    if b.weekly_cap_usd is not None:
        db.set_setting("weekly_cap_usd", b.weekly_cap_usd)
        db.set_setting("fleet_paused", False)
    if b.competition:
        for col in ("cap_usd", "cap_hours"):
            v = getattr(b, col)
            if v is not None:
                db.x(f"UPDATE competitions SET {col}=? WHERE slug=?", v, b.competition)
                db.x(f"UPDATE runs SET {'budget_usd' if col == 'cap_usd' else 'budget_gpu_h'}=? "
                     f"WHERE competition_slug=? AND status='running'", v, b.competition)
        db.x("UPDATE competitions SET paused=0, note=NULL WHERE slug=? AND note='cap reached'", b.competition)
    db.x("UPDATE human_tasks SET status='done' WHERE kind='approve' AND detail LIKE '%cap%'")
    return {"ok": True}


# ---------------------------------------------------------------- dashboard detail endpoints
_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def username():
    return cached("username", 86400, lambda: agents.kaggle().get_config_value("username") or "")


@app.get("/api/me")
def me():
    return {"username": username(), "model": config.MODEL, "engine": config.ENGINE}


@app.get("/api/competitions/{slug}/leaderboard")
def leaderboard(slug: str):
    import pandas as pd
    c = db.one("SELECT * FROM competitions WHERE slug=?", slug)
    if not c:
        raise HTTPException(404)
    agents.leaderboard(slug, hib=bool(c["higher_is_better"]))  # refresh cache if stale
    f = agents.comp_dir(slug) / "leaderboard.csv"
    if not f.exists():
        return {"teams": 0, "rows": [], "me": None}
    df = pd.read_csv(f).rename(columns=str.strip)
    df = df.sort_values("Score", ascending=not c["higher_is_better"]).reset_index(drop=True)
    df["Rank"] = df.index + 1
    user = username().lower()
    mine = df[df["TeamMemberUserNames"].fillna("").astype(str).str.lower().str.split(",").apply(lambda xs: user in [x.strip() for x in xs])] \
        if "TeamMemberUserNames" in df else df.iloc[0:0]
    me_row = mine.iloc[0].to_dict() if len(mine) else None
    cols = [x for x in ("Rank", "TeamName", "Score", "SubmissionCount", "LastSubmissionDate") if x in df]
    pos = int(me_row["Rank"]) - 1 if me_row else None
    around = df.iloc[max(0, pos - 7): pos + 8][cols] if pos is not None else df.iloc[0:0]
    scores = df["Score"].dropna()
    # Show the competitive 90% of the board (drop the long tail of broken submissions).
    lo, hi = (scores.quantile(0.10), scores.max()) if c["higher_is_better"] else (scores.min(), scores.quantile(0.90))
    counts, edges = np.histogram(scores.clip(lo, hi), bins=30) if hi > lo else ([len(scores)], [lo, hi])
    return {"teams": len(df), "higher_is_better": bool(c["higher_is_better"]), "me": me_row and {k: me_row[k] for k in cols},
            "top": df.head(20)[cols].to_dict("records"), "around": around.to_dict("records"),
            "p10": float(df["Score"].iloc[max(0, len(df) // 10 - 1)]), "median": float(df["Score"].iloc[len(df) // 2]),
            "histogram": {"counts": [int(x) for x in counts], "edges": [float(x) for x in edges]},
            "updated": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}


@app.get("/api/competitions/{slug}/kaggle-submissions")
def kaggle_submissions(slug: str):
    def fetch():
        rows = []
        for s in agents.kaggle().competition_submissions(slug) or []:
            if s:
                d = s.to_dict()
                desc = d.get("description") or ""
                d["experiment_id"] = next((w for w in desc.split() if w.startswith("e_")), None)
                rows.append(d)
        return rows
    try:
        return cached(f"subs:{slug}", 60, fetch)
    except Exception as e:
        return JSONResponse({"error": str(e)[:300]}, status_code=502)


@app.get("/api/competitions/{slug}/files")
def files(slug: str):
    root = agents.comp_dir(slug)
    if not root.exists():
        return {"data": [], "experiments": [], "submissions": []}
    ls = lambda d: [{"name": p.name, "size": p.stat().st_size, "path": str(p.relative_to(root))}
                    for p in sorted(d.iterdir()) if p.is_file()] if d.exists() else []
    exps = sorted((p for p in (root / "experiments").iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime,
                  reverse=True) if (root / "experiments").exists() else []
    return {"root": str(root), "data": ls(root / "data"), "submissions": ls(root / "submissions")[::-1],
            "experiments": [{"id": p.name, "files": ls(p)} for p in exps]}


@app.get("/api/files/{slug}/{path:path}")
def file(slug: str, path: str, head: int = 0):
    root = agents.comp_dir(slug).resolve()
    p = (root / path).resolve()
    if not p.is_relative_to(root) or not p.is_file():  # no path traversal outside the competition folder
        raise HTTPException(404)
    if head:
        with open(p, errors="replace") as fh:
            return PlainTextResponse("".join(line for _, line in zip(range(head), fh)))
    return FileResponse(p, filename=p.name)


ALERT_TYPES = ("agent.error", "experiment.flagged", "cv_lb.diverged", "human_task.opened", "rank.updated",
               "submission.scored", "competition.recommended", "experiment.reviewed", "strategy.updated",
               "competition.unblocked")


@app.get("/api/alerts")
def alerts(limit: int = 100):
    rows = db.q(f"SELECT * FROM events WHERE type IN ({','.join('?' * len(ALERT_TYPES))}) ORDER BY id DESC LIMIT ?",
                *ALERT_TYPES, limit)
    for e in rows:
        e["payload"] = json.loads(e["payload"])
        e["text"] = db.describe(e["type"], e["payload"], e["competition"])
    return rows


@app.get("/api/submissions")
def all_submissions():
    return db.q("SELECT s.*, e.cv_mean, e.cv_std, e.summary FROM submissions s JOIN experiments e "
                "ON s.experiment_id=e.id ORDER BY s.created_at DESC LIMIT 500")


@app.post("/api/competitions/{slug}/stop")
def stop(slug: str):
    """Decision: stop working on a competition. It stays stopped (never auto-restarted) until resumed."""
    db.x("UPDATE competitions SET state='stopped', opt_in=0 WHERE slug=?", slug)
    agents.kill_experiments(slug)
    db.x("UPDATE runs SET status='done' WHERE competition_slug=? AND status='running'", slug)
    db.x("UPDATE human_tasks SET status='done' WHERE competition_slug=? AND status='open'", slug)
    db.emit("Human", "competition.stopped", {}, competition=slug)
    return {"ok": True}


@app.post("/api/competitions/{slug}/archive")
def archive(slug: str):
    """Decision: remove a competition from every list and recommendation (files and history are kept)."""
    db.x("UPDATE competitions SET state='archived', opt_in=0 WHERE slug=?", slug)
    agents.kill_experiments(slug)
    db.x("UPDATE runs SET status='done' WHERE competition_slug=? AND status='running'", slug)
    db.x("UPDATE human_tasks SET status='done' WHERE competition_slug=? AND status='open'", slug)
    db.emit("Human", "competition.archived", {}, competition=slug)
    return {"ok": True}


# ---------------------------------------------------------------- auth + settings
import base64
import os
import secrets

from fastapi.responses import Response


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """HTTP Basic auth when PODIUM_PASSWORD is set (any username). Native browser prompt, works on phones."""
    if config.PASSWORD:
        ok = False
        h = request.headers.get("authorization", "")
        if h.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, config.PASSWORD)
            except Exception:
                pass
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Podium"'})
    return await call_next(request)


# env var -> (config attribute, type). Only these are editable from the dashboard.
EDITABLE = {
    "PODIUM_MODEL": ("MODEL", str), "PODIUM_CLAUDE_CODE_MODEL": ("CLAUDE_CODE_MODEL", str),
    "PODIUM_STRATEGY_MODEL": ("STRATEGY_MODEL", str),
    "PODIUM_MODEL_STRATEGY": ("MODEL_STRATEGY", str), "PODIUM_MODEL_EXPERIMENT": ("MODEL_EXPERIMENT", str),
    "PODIUM_MODEL_REVIEW": ("MODEL_REVIEW", str), "PODIUM_MODEL_COPILOT": ("MODEL_COPILOT", str),
    "PODIUM_RESEARCH": ("RESEARCH", bool), "PODIUM_PLAN_RESERVE": ("PLAN_RESERVE", float),
    "PODIUM_FALLBACK_MODEL": ("FALLBACK_MODEL", str), "PODIUM_EXECUTOR": ("EXECUTOR", str),
    "PODIUM_KAGGLE_ACCELERATOR": ("KAGGLE_ACCELERATOR", str), "PODIUM_KINDS": ("KINDS", str),
    "PODIUM_NOTEBOOK_TIMEOUT_S": ("NOTEBOOK_TIMEOUT_S", int), "PODIUM_CONVERSATIONS": ("CONVERSATIONS", bool),
    "PODIUM_CONVERSATION_MAX_TURNS": ("CONVERSATION_MAX_TURNS", int),
    "PODIUM_MAX_ACTIVE": ("MAX_ACTIVE", int), "PODIUM_MIN_CHANCE": ("MIN_CHANCE", int),
    "PODIUM_MAX_EXPERIMENTS": ("MAX_EXPERIMENTS", int), "PODIUM_REFLECT_EVERY": ("REFLECT_EVERY", int),
    "PODIUM_AUTO_SUBMIT": ("AUTO_SUBMIT", bool), "PODIUM_REVIEW": ("REVIEW", bool),
    "PODIUM_DAILY_SUBMISSIONS": ("DAILY_SUBMISSIONS", int), "PODIUM_SCOUT_SECONDS": ("SCOUT_SECONDS", int),
    "PODIUM_LOOP_SECONDS": ("LOOP_SECONDS", int), "PODIUM_COMP_CAP_USD": ("COMP_CAP_USD", float),
    "PODIUM_COMP_CAP_HOURS": ("COMP_CAP_HOURS", float), "PODIUM_WEEKLY_CAP_USD": ("WEEKLY_CAP_USD", float),
    "PODIUM_NOTIFY_EMAIL": ("NOTIFY_EMAIL", str), "PODIUM_NOTIFY_URL": ("NOTIFY_URL", str),
    "PODIUM_PUBLIC_URL": ("PUBLIC_URL", str), "PODIUM_EXPERIMENT_TIMEOUT_S": ("EXPERIMENT_TIMEOUT_S", int),
}


def write_env(key, value):
    """Update or append KEY=value in .env (keeps comments and other keys)."""
    path = config.ROOT / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i], found = f"{key}={value}", True
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def mask(v):
    return "" if not v else (v[:4] + "…" + v[-2:] if len(v) > 8 else "set")


@app.get("/api/settings")
def get_settings():
    vals = {k: getattr(config, attr) for k, (attr, _) in EDITABLE.items()}
    vals["PODIUM_KINDS"] = ",".join(sorted(config.KINDS))
    vals["PODIUM_WEEKLY_CAP_USD"] = db.setting("weekly_cap_usd", config.WEEKLY_CAP_USD)
    try:
        user = username()
    except Exception as e:
        user = f"(auth failed: {str(e)[:80]})"
    return {"editable": vals,
            "kaggle": {"username": user, "token": mask(os.environ.get("KAGGLE_API_TOKEN", "")),
                       "method": "token" if os.environ.get("KAGGLE_API_TOKEN") else "kaggle.json"},
            "smtp": {"host": config.SMTP_HOST, "port": config.SMTP_PORT, "user": mask(config.SMTP_USER),
                     "from": config.FROM_EMAIL, "password": "set" if config.SMTP_PASSWORD else ""},
            "readonly": {"engine": config.ENGINE, "sandbox": config.SANDBOX, "kinds": sorted(config.KINDS),
                         "data_dir": str(config.DATA_DIR), "host": config.HOST, "port": config.PORT,
                         "password_protected": bool(config.PASSWORD)}}


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    changed = {}
    for k, v in body.items():
        if k not in EDITABLE:
            raise HTTPException(400, f"{k} is not editable")
        attr, typ = EDITABLE[k]
        val = (str(v).lower() in ("1", "true", "yes", "on")) if typ is bool else typ(v)
        if k == "PODIUM_KINDS":
            val = ",".join(x.strip() for x in str(v).split(",") if x.strip()) or "tabular"
            config.KINDS = set(val.split(","))
        else:
            setattr(config, attr, val)
        write_env(k, int(val) if typ is bool else val)
        changed[k] = val
        if k in ("PODIUM_MODEL", "PODIUM_MODEL_EXPERIMENT"):
            config.ENGINE = "baseline" if val == "none" else "llm"
        if k == "PODIUM_WEEKLY_CAP_USD":
            db.set_setting("weekly_cap_usd", val)
    db.emit("Human", "settings.changed", changed)
    return {"ok": True, "changed": changed}


class KaggleCreds(BaseModel):
    token: str


@app.post("/api/settings/kaggle")
def set_kaggle(c: KaggleCreds):
    """Switch Kaggle account: validate the new token against the API first, then persist to .env."""
    token = c.token.strip()
    if not token:
        raise HTTPException(400, "empty token")
    old = os.environ.get("KAGGLE_API_TOKEN")
    os.environ["KAGGLE_API_TOKEN"] = token
    agents._api = None
    try:
        user = agents.kaggle().get_config_value("username")
        agents.kaggle().competitions_list()
    except Exception as e:
        if old is None:
            os.environ.pop("KAGGLE_API_TOKEN", None)
        else:
            os.environ["KAGGLE_API_TOKEN"] = old
        agents._api = None
        raise HTTPException(400, f"Kaggle rejected this token: {str(e)[:200]}")
    write_env("KAGGLE_API_TOKEN", token)
    _cache.pop("username", None)
    db.emit("Human", "settings.kaggle_account", {"username": user})
    return {"ok": True, "username": user}


@app.post("/api/settings/test-notification")
def test_notification():
    errors = db.notify("[Podium] Test notification", "Podium test: notifications reach you here.")
    if not (config.NOTIFY_EMAIL or config.NOTIFY_URL):
        errors.append("no PODIUM_NOTIFY_EMAIL or PODIUM_NOTIFY_URL configured")
    return {"ok": not errors, "errors": errors}


PLATFORMS = [  # researched Oct 2026; fit = suitability for an autonomous fleet (1-10)
    {"name": "Kaggle", "url": "https://www.kaggle.com", "focus": "Everything", "api": "kaggle (official)", "fit": 9, "status": "integrated"},
    {"name": "Numerai", "url": "https://numer.ai", "focus": "Tabular finance, weekly rounds", "api": "numerapi (official)", "fit": 10, "status": "recommended next"},
    {"name": "CrunchDAO", "url": "https://hub.crunchdao.com", "focus": "Quant finance, biotech, time series", "api": "crunch-cli (official, code push)", "fit": 8, "status": "candidate"},
    {"name": "Zindi", "url": "https://zindi.africa", "focus": "Tabular, CV, NLP", "api": "zindi (unofficial)", "fit": 7, "status": "candidate"},
    {"name": "SIGNATE", "url": "https://signate.jp", "focus": "Tabular, CV (Japan)", "api": "signate (official CLI)", "fit": 7, "status": "candidate"},
    {"name": "AIcrowd", "url": "https://www.aicrowd.com", "focus": "RL, CV, NLP, LLM", "api": "aicrowd-cli (official)", "fit": 7, "status": "candidate"},
    {"name": "Allora Network", "url": "https://allora.network", "focus": "Continuous forecasting topics", "api": "allora-sdk", "fit": 7, "status": "candidate"},
    {"name": "EvalAI", "url": "https://eval.ai", "focus": "Academic benchmarks", "api": "evalai (official, ageing)", "fit": 6, "status": "later"},
    {"name": "Hugging Face Competitions", "url": "https://huggingface.co/competitions", "focus": "Community", "api": "huggingface_hub", "fit": 5, "status": "later"},
    {"name": "Codabench / CodaLab", "url": "https://www.codabench.org", "focus": "Academic, code submission", "api": "REST only", "fit": 4, "status": "later"},
    {"name": "DrivenData", "url": "https://www.drivendata.org", "focus": "Social impact CV/tabular", "api": "none (web)", "fit": 4, "status": "manual only"},
    {"name": "Tianchi", "url": "https://tianchi.aliyun.com", "focus": "Tabular, CV, NLP, LLM", "api": "none", "fit": 3, "status": "manual only"},
    {"name": "Solafune", "url": "https://solafune.com", "focus": "Satellite / geospatial", "api": "none", "fit": 3, "status": "manual only"},
    {"name": "Topcoder", "url": "https://www.topcoder.com", "focus": "Marathons, dev", "api": "REST v5", "fit": 3, "status": "manual only"},
]


@app.get("/api/platforms")
def platforms():
    return PLATFORMS


@app.get("/api/competitions/{slug}/strategy")
def get_strategy(slug: str):
    sd = agents.strategy_dir(slug)
    versions = sorted(sd.glob("plan_v*.md"), key=lambda p: int(p.stem.split("_v")[1]), reverse=True) if sd.exists() else []
    return {"meta": db.setting(f"strategy:{slug}", {}), "conversation": db.setting(f"conv:{slug}:solver", {}),
            "memory": agents.fleet_memory(60), "conversation_max_turns": config.CONVERSATION_MAX_TURNS,
            "models": {r: config.model_for(r) for r in config.ROLES},
            "profile": (sd / "profile.md").read_text() if (sd / "profile.md").exists() else "",
            "versions": [{"version": int(p.stem.split("_v")[1]), "markdown": p.read_text(),
                          "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
                         for p in versions]}


class CopilotIn(BaseModel):
    messages: list[dict]
    context: str | None = None


@app.post("/api/copilot")
def copilot_chat(body: CopilotIn):
    from . import copilot
    msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:8000]} for m in body.messages]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(400, "last message must be from the user")
    try:
        return copilot.chat(msgs, body.context)
    except Exception as e:
        raise HTTPException(502, f"Copilot failed: {str(e)[:300]}")


# ---------------------------------------------------------------- AI providers & detailed spend
from . import llm as _llm

# Secrets editable from Settings: stored in .env (mode 600) and os.environ; never returned unmasked.
SECRETS = ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
PLAIN = ["AWS_REGION_NAME", "AWS_PROFILE", "OLLAMA_API_BASE", "PODIUM_CLAUDE_BIN"]


@app.get("/api/ai")
def ai_status():
    """Which AI backend runs, how it is authenticated, and which provider credentials are present."""
    return {"roles": {r: config.model_for(r) for r in config.ROLES}, "research": config.RESEARCH,
            "conversations": config.CONVERSATIONS, "conversation_max_turns": config.CONVERSATION_MAX_TURNS,
            "conversations_active": {k[5:]: v for k, v in ((r["key"], json.loads(r["value"])) for r in
                                     db.q("SELECT * FROM settings WHERE key LIKE 'conv:%:solver'"))},
            "backend": config.MODEL, "engine": config.ENGINE, "work_model": config.CLAUDE_CODE_MODEL if config.MODEL == "claude-code" else config.MODEL,
            "strategy_model": config.STRATEGY_MODEL or (config.CLAUDE_CODE_MODEL if config.MODEL == "claude-code" else config.MODEL),
            "claude_code": {"bin": config.CLAUDE_BIN, **(_llm.claude_code_auth() if config.MODEL == "claude-code" or os.path.exists(config.CLAUDE_BIN) else {})},
            "credentials": {k: mask(os.environ.get(k, "")) for k in SECRETS},
            "plain": {k: os.environ.get(k, "") for k in PLAIN}}


@app.put("/api/ai/credentials")
async def ai_credentials(request: Request):
    body = await request.json()
    changed = []
    for k, v in body.items():
        if k not in SECRETS + PLAIN:
            raise HTTPException(400, f"{k} is not a provider setting")
        v = str(v).strip()
        if k in SECRETS and not v:
            continue  # empty secret field = keep the current value
        os.environ[k] = v
        write_env(k, v)
        if k == "PODIUM_CLAUDE_BIN":
            config.CLAUDE_BIN = v
            _llm._auth_cache.clear()
        changed.append(k)
    db.emit("Human", "settings.changed", {"credentials": changed})
    return {"ok": True, "changed": changed}


@app.post("/api/ai/test")
def ai_test(role: str = "work"):
    """Live round-trip to the configured model (role=work or strategy). Logged like any other call."""
    import time as _t
    t0 = _t.time()
    try:
        text, tokens, cost = _llm.complete("Reply with exactly: ready", "Health check", max_tokens=20,
                                           role=role if role in config.ROLES else "experiment", agent="Settings",
                                           purpose=f"connection test ({role})")
        last = db.one("SELECT model, backend, billing FROM llm_calls ORDER BY id DESC LIMIT 1")
        return {"ok": True, "reply": text.strip()[:60], "seconds": round(_t.time() - t0, 1), "cost_usd": cost, **(last or {})}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


@app.get("/api/ai/plan")
def ai_plan(refresh: bool = False):
    """Real Claude plan utilization (from Claude Code's rate-limit events), reserve and cooldown."""
    pu = _llm.plan_usage()
    if refresh or not pu or time.time() - pu.get("at", 0) > 1800:
        try:
            pu = _llm.probe_plan_usage()
        except Exception as e:
            pu = {**pu, "error": str(e)[:200]}
    return {**pu, "reserve": config.PLAN_RESERVE, "cooldown_until": _llm.cooldown_until(),
            "holding": not _llm.plan_ok("experiment"), "auth": _llm.claude_code_auth()}


@app.get("/api/spend/detail")
def spend_detail(days: int = 30):
    since = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).isoformat()
    agg = lambda col: db.q(f"SELECT {col} AS k, COUNT(*) calls, SUM(cost_usd) usd, SUM(input_tokens) inp, "
                           f"SUM(output_tokens) outp, SUM(cache_tokens) cache, SUM(seconds) secs, SUM(1-ok) errors "
                           f"FROM llm_calls WHERE ts >= ? GROUP BY {col} ORDER BY usd DESC", since)
    daily = db.q("SELECT substr(ts,1,10) d, SUM(cost_usd) usd, COUNT(*) calls FROM llm_calls WHERE ts >= ? "
                 "GROUP BY d ORDER BY d", since)
    totals = db.one("SELECT COUNT(*) calls, COALESCE(SUM(cost_usd),0) usd, COALESCE(SUM(input_tokens+output_tokens+cache_tokens),0) tokens, "
                    "COALESCE(SUM(CASE WHEN billing='plan' THEN cost_usd ELSE 0 END),0) plan_usd, "
                    "COALESCE(SUM(CASE WHEN billing='api' THEN cost_usd ELSE 0 END),0) api_usd, MIN(ts) since "
                    "FROM llm_calls WHERE ts >= ?", since)
    return {"days": days, "totals": totals, "by_model": agg("model"), "by_backend": agg("backend || ' · ' || billing"),
            "by_purpose": agg("purpose"), "by_agent": agg("agent"), "by_competition": agg("COALESCE(competition,'(fleet)')"),
            "daily": daily, "calls": db.q("SELECT * FROM llm_calls ORDER BY id DESC LIMIT 200"),
            "legacy_usd": agents.spend()["usd"]}


class UploadIn(BaseModel):
    name: str
    data: str  # base64


@app.post("/api/copilot/upload")
def copilot_upload(u: UploadIn):
    """Store a Copilot attachment under data/uploads/<id>/<name> (max 15 MB)."""
    import base64
    import re as _re
    import uuid as _uuid
    from . import copilot
    raw = base64.b64decode(u.data.split(",", 1)[-1])
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "file larger than 15 MB")
    name = _re.sub(r"[^A-Za-z0-9._-]", "_", u.name)[-120:] or "file"
    rel = f"{_uuid.uuid4().hex[:12]}/{name}"
    path = copilot.upload_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"id": rel, "name": name, "size": len(raw), "kind": copilot.kind_of_file(name)}


@app.get("/api/copilot/upload/{path:path}")
def copilot_upload_get(path: str):
    from . import copilot
    p = (copilot.upload_dir() / path).resolve()
    if not p.is_relative_to(copilot.upload_dir()) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/copilot/stream")
def copilot_stream(body: CopilotIn):
    """Server-sent events: delta | step | step_done | navigate | done | error."""
    from . import copilot
    msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:8000],
             "attachments": [a for a in (m.get("attachments") or [])[:8] if isinstance(a, dict)]} for m in body.messages]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(400, "last message must be from the user")

    def gen():
        try:
            for ev in copilot.chat_stream(msgs, body.context):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)[:400]})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache",
                                                                           "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- Ongoing: everything in progress and what it waits for
import re as _re
from datetime import timedelta
from pathlib import Path

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _milestones(plan_md, year=2026):
    """Parse the '| Dates | Milestone |' timeline table of a project PLAN.md into dated milestones."""
    out, today = [], datetime.now(timezone.utc).date()
    for line in plan_md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not _re.match(r"\**\s*(\w{3})\s+\d", cells[0].replace("*", "")):
            continue
        txt = cells[0].replace("*", "")
        days = [(m.group(1).lower()[:3], int(m.group(2))) for m in _re.finditer(r"([A-Za-z]{3})\w*\s+(\d{1,2})", txt)]
        nums = [int(n) for n in _re.findall(r"\d{1,2}", txt)]
        if not days:
            continue
        mon = _MONTHS.get(days[0][0])
        if not mon:
            continue
        from datetime import date
        start = date(year, mon, days[0][1])
        end_mon = _MONTHS.get(days[-1][0], mon)
        end = date(year, end_mon, days[-1][1] if len(days) > 1 else (nums[-1] if len(nums) > 1 else days[0][1]))
        done = "✅" in line or "(done)" in line.lower()
        state = "done" if done else "overdue" if end < today else "current" if start <= today <= end else "upcoming"
        out.append({"dates": txt, "start": start.isoformat(), "end": end.isoformat(), "text": cells[1].replace("**", ""),
                    "state": state})
    return out


def _devruns(project_dir):
    runs = []
    for rj in sorted((project_dir / "runs").glob("*/run.json")) if (project_dir / "runs").exists() else []:
        meta = json.loads(rj.read_text())
        tag, key = rj.parent.name, f"devrun:{project_dir.name}:{rj.parent.name}"
        st = db.setting(key, {})
        if time.time() - st.get("at", 0) > 180 and st.get("status") not in ("complete", "error", "cancelacknowledged"):
            try:
                s = agents.kaggle().kernels_status(meta["ref"])
                st = {"status": str(getattr(s, "status", s)).split(".")[-1].lower(), "at": time.time(),
                      "failure": getattr(s, "failure_message", "") or ""}
            except Exception as e:
                st = {"status": "unknown", "at": time.time(), "failure": str(e)[:120]}
            db.set_setting(key, st)
        summ = rj.parent / "output" / "podium_summary.json"
        runs.append({"tag": tag, "ref": meta["ref"], "url": meta.get("url"), "tasks": len(meta.get("tasks", [])),
                     "agent": Path(meta.get("agent", "")).name, "status": st.get("status"), "failure": st.get("failure"),
                     "summary": json.loads(summ.read_text()) if summ.exists() else None,
                     "launched": datetime.fromtimestamp(rj.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return runs


@app.get("/api/ongoing")
def ongoing():
    now = datetime.now(timezone.utc)
    comps = {c["slug"]: c for c in db.q("SELECT * FROM competitions")}
    # running experiments: started without a later finished/cancelled event
    started = db.q("SELECT ts, competition, payload FROM events WHERE type='experiment.started' ORDER BY id DESC LIMIT 200")
    closed = {json.loads(e["payload"]).get("experiment_id") for e in
              db.q("SELECT payload FROM events WHERE type IN ('experiment.finished','experiment.cancelled') ORDER BY id DESC LIMIT 400")}
    running, seen = [], set()
    for e in started:
        p = json.loads(e["payload"])
        slug = e["competition"]
        if p.get("experiment_id") in closed or slug in seen:
            continue
        seen.add(slug)
        alive = bool(agents._running.get(slug) and agents._running[slug].is_alive())
        if not alive:
            continue
        c = comps.get(slug, {})
        running.append({"kind": "experiment", "competition": slug, "title": c.get("title", slug), "id": p.get("experiment_id"),
                        "what": p.get("summary", ""), "since": e["ts"],
                        "where": "Kaggle notebook" if engines_where(c) == "kaggle" else "local sandbox"})
    for slug, t in agents._running.items():
        if t.is_alive() and slug not in seen:
            c = comps.get(slug, {})
            last = db.one("SELECT ts, agent, type FROM events WHERE competition=? AND agent IN ('Strategist','Solver') "
                          "ORDER BY id DESC LIMIT 1", slug)
            running.append({"kind": "ai", "competition": slug, "title": c.get("title", slug),
                            "what": "AI is planning / writing the next experiment", "since": last["ts"] if last else None,
                            "where": "Claude Code" if _llm.spec("experiment")[0] == "claude-code" else _llm.spec("experiment")[1]})
    # waiting on Kaggle
    kaggle_wait = [{"kind": "score", "competition": s["competition_slug"], "title": comps.get(s["competition_slug"], {}).get("title"),
                    "what": f"submission {s['experiment_id']} being scored", "since": s["created_at"]}
                   for s in db.q("SELECT * FROM submissions WHERE lb_public IS NULL ORDER BY created_at DESC LIMIT 20")]
    for slug, c in comps.items():
        if c["state"] == "project":
            pj = db.setting(f"proj:{slug}", {})
            if pj.get("status") in ("pending", "running", "queued"):
                kaggle_wait.append({"kind": "score", "competition": slug, "title": c["title"],
                                    "what": f"project submission scoring: {pj.get('latest', '')[:80]}", "since": None})
    # projects
    projects = []
    for plan in sorted((config.ROOT / "projects").glob("*/PLAN.md")):
        md = plan.read_text()
        slugs = sorted({s for s in comps if s in md and comps[s]["state"] == "project"})
        runs = _devruns(plan.parent)
        for r in runs:
            if r["status"] in ("queued", "running"):
                kaggle_wait.append({"kind": "notebook", "competition": plan.parent.name, "title": f"Dev run {r['tag']}",
                                    "what": f"{r['agent']} on {r['tasks']} tasks ({r['status']})", "since": r["launched"],
                                    "url": r["url"]})
        projects.append({"name": plan.parent.name, "title": md.splitlines()[0].lstrip("# "), "path": str(plan),
                         "milestones": _milestones(md), "runs": runs,
                         "competitions": [{"slug": s, "title": comps[s]["title"], "deadline": comps[s]["deadline"],
                                           "kaggle": db.setting(f"proj:{s}", {})} for s in slugs]})
    # waiting on you
    you = [{"kind": "decision", "competition": t["competition_slug"], "what": t["detail"], "since": t["created_at"], "url": t["url"]}
           for t in db.q("SELECT * FROM human_tasks WHERE status='open' ORDER BY created_at DESC")]
    you += [{"kind": "paused", "competition": s, "title": c["title"], "what": c["note"] or "paused by you: resume to continue"}
            for s, c in comps.items() if c["state"] == "active" and c["paused"]]
    if db.setting("fleet_paused", False):
        you.append({"kind": "paused", "competition": None, "what": "the whole fleet is paused"})
    week = agents.spend(days=7)["usd"]
    cap = db.setting("weekly_cap_usd", config.WEEKLY_CAP_USD)
    if week >= 0.9 * cap:
        you.append({"kind": "budget", "competition": None, "what": f"weekly AI budget ${week:.2f} of ${cap:.0f}: the fleet pauses at the cap (Settings > Budgets)"})
    # coming up
    nxt = []
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    nxt.append({"what": "Kaggle daily submission quotas reset", "at": midnight.isoformat(timespec="seconds")})
    scout_at = db.setting("scout_at", 0)
    if scout_at:
        nxt.append({"what": "Scout looks for new competitions", "at": datetime.fromtimestamp(scout_at + config.SCOUT_SECONDS, timezone.utc).isoformat(timespec="seconds")})
    for slug, c in comps.items():
        if c["state"] == "active" and not c["paused"]:
            meta = db.setting(f"strategy:{slug}", {})
            n = db.one("SELECT COUNT(*) n FROM experiments e JOIN runs r ON e.run_id=r.id WHERE r.competition_slug=? AND r.status='running'", slug)["n"]
            left = max(0, config.REFLECT_EVERY - (n - meta.get("experiments", 0)))
            nxt.append({"what": f"{c['title']}: strategy reflection in {left} experiment(s) (v{meta.get('version', 0) + 1})", "competition": slug})
        if c["state"] in ("active", "project") and c["deadline"]:
            d = (datetime.fromisoformat(c["deadline"]) - now).days
            if d <= 45:
                nxt.append({"what": f"{c['title']}: deadline in {d} days", "at": c["deadline"], "competition": slug})
    for pj in projects:
        for m in pj["milestones"]:
            if m["state"] in ("current", "upcoming", "overdue"):
                nxt.append({"what": f"{pj['name']}: {m['text'][:110]}", "at": m["end"], "state": m["state"]})
                break
    pu = _llm.plan_usage()
    return {"now": now.isoformat(timespec="seconds"), "running": running, "kaggle": kaggle_wait, "you": you, "next": nxt,
            "projects": projects, "fleet": {"paused": db.setting("fleet_paused", False), "plan": pu,
                                            "plan_holding": not _llm.plan_ok("experiment"), "reserve": config.PLAN_RESERVE,
                                            "cooldown_until": _llm.cooldown_until()}}


def engines_where(c):
    from . import engines
    try:
        return engines.executor_for(dict(c))[0]
    except Exception:
        return "docker"
