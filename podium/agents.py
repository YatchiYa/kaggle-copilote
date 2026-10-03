"""The fleet: Scout -> Gatekeeper -> Solver -> Critic -> Submitter, looped by a Google ADK LoopAgent.

Steps are deterministic Python (no LLM needed to orchestrate); the LLM lives inside the solver engine.
"""
import asyncio
import json
import re
import threading
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from . import config, db, engines, llm

LOWER_IS_BETTER = ("rmse", "mae", "mse", "loss", "error", "rmsle", "mape", "distance", "wrmse")
_api = None


def kaggle():
    global _api
    if _api is None:
        from kaggle.api.kaggle_api_extended import KaggleApi
        _api = KaggleApi()
        _api.authenticate()
    return _api


def maintenance():
    """Daily: consistent SQLite backup (+ fleet memory) into data/backups/, keeping the newest BACKUP_KEEP."""
    import shutil
    import sqlite3
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if db.setting("backup_day") == day:
        return
    out = config.DATA_DIR / "backups"
    out.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as src, sqlite3.connect(out / f"podium-{day}.db") as dst:
        src.backup(dst)
    if memory_file().exists():
        shutil.copy(memory_file(), out / f"lessons-{day}.md")
    for old in sorted(out.glob("podium-*.db"))[:-config.BACKUP_KEEP]:
        old.unlink()
        old.with_name(old.name.replace("podium-", "lessons-").replace(".db", ".md")).unlink(missing_ok=True)
    db.set_setting("backup_day", day)
    db.emit("Fleet", "backup.done", {"file": f"backups/podium-{day}.db"})


def fleet_paused():
    return db.setting("fleet_paused", False)


def active_comps():
    return db.q("SELECT * FROM competitions WHERE state='active' AND paused=0")


def comp_dir(slug):
    """data/competitions/<slug>/
         data/                       raw Kaggle files
         experiments/<exp_id>/       main.py, log.txt, submission.csv, oof.csv, meta.json
         submissions/<utc>_<exp_id>.csv  exact files sent to Kaggle (+ .json with the reason)
         leaderboard.csv             latest full public leaderboard"""
    return config.DATA_DIR / "competitions" / slug


def write_meta(exp_id):
    e = db.one("SELECT * FROM experiments WHERE id=?", exp_id)
    if e and e["code_uri"]:
        Path(e["code_uri"]).with_name("meta.json").write_text(json.dumps(e, indent=2))


# ---------------------------------------------------------------- Scout
def kind_of(c):
    if c.is_kernels_submissions_only:
        return "code"
    tags = " ".join(t.name for t in (c.tags or [])).lower()
    for kind, words in (("tabular", ("tabular",)), ("cv", ("image", "computer vision", "video")),
                        ("nlp", ("nlp", "text", "language"))):
        if any(w in tags for w in words):
            return kind
    return "other"


def leaderboard(slug, max_age_s=1800, hib=True):
    """Full public leaderboard scores, best first, cached on disk. [] if unavailable."""
    d = comp_dir(slug)
    f = d / "leaderboard.csv"
    if not f.exists() or time.time() - f.stat().st_mtime > max_age_s:
        d.mkdir(parents=True, exist_ok=True)
        try:
            kaggle().competition_leaderboard_download(slug, str(d))
            for z in d.glob(f"{slug}*.zip"):
                with zipfile.ZipFile(z) as zf:
                    name = next(n for n in zf.namelist() if n.endswith(".csv"))
                    f.write_bytes(zf.read(name))
                z.unlink()
        except Exception:
            return []
    try:
        return sorted(pd.read_csv(f, usecols=["Score"])["Score"].dropna().tolist(), reverse=hib)
    except Exception:
        return []


KNOWN_METRICS = ("auc", "rmse", "rmsle", "mae", "accuracy", "log", "f1", "r2", "mse", "mape")
BOUNDED_METRICS = ("auc", "accuracy", "f1", "map", "dice", "iou")


def chance(c, kind, days, size, lb, hib, metric):
    """0-100 score: how likely the fleet can reach a good rank here. Returns (score, reasons)."""
    s, why = 0, []
    def add(points, reason):
        nonlocal s
        s += points
        why.append(f"{'+' if points >= 0 else ''}{points} {reason}")
    if kind not in config.KINDS:
        add(-100, f"{kind} data (fleet runs {','.join(sorted(config.KINDS))})")
    add({"Playground": 30, "Community": 20, "Featured": 10, "Research": 10, "Getting Started": 0}
        .get(c.category, 8), f"category {c.category}")
    if days < 3:
        add(-40, f"{days} days left")
    elif days < 7:
        add(5, f"{days} days left")
    elif days <= 90:
        add(20, f"{days} days left")
    else:
        add(10, f"{days} days left")
    if size is not None:
        mb = size / 1e6
        add(15 if mb < 300 else 5 if mb < 3000 else -15, f"data {mb:.0f} MB")
    if any(k in metric.lower() for k in KNOWN_METRICS):
        add(10, f"standard metric ({metric})")
    if (c.max_daily_submissions or 0) >= 5:
        add(5, f"{c.max_daily_submissions} subs/day")
    if lb:
        top, teams = lb[0], len(lb)
        perfect = hib and any(k in metric.lower() for k in BOUNDED_METRICS) and top >= 0.9999 or (not hib and top <= 1e-9)
        if perfect:
            add(-30, "leaderboard saturated by perfect (leaked) scores")
        add(10 if teams < 1000 else 5 if teams < 5000 else 0, f"{teams} teams")
    return max(0, min(100, s)), why


def upsert_competition(c, now=None):
    """Insert/refresh one Kaggle competition (and score it once a day). Returns 1 if it was new."""
    now = now or datetime.now(timezone.utc)
    slug = c.ref.rstrip("/").split("/")[-1]
    deadline = c.deadline.replace(tzinfo=timezone.utc)
    kind, days, metric = kind_of(c), (deadline - now).days, c.evaluation_metric or ""
    hib = 0 if any(w in metric.lower() for w in LOWER_IS_BETTER) else 1
    old = db.one("SELECT * FROM competitions WHERE slug=?", slug)
    db.x("""INSERT INTO competitions (slug, title, metric, kind, deadline, rules_accepted, higher_is_better,
              max_daily_submissions, team_count, category) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET title=excluded.title, metric=excluded.metric, deadline=excluded.deadline,
              rules_accepted=MAX(rules_accepted, excluded.rules_accepted), category=excluded.category,
              max_daily_submissions=excluded.max_daily_submissions, team_count=excluded.team_count""",
         slug, c.title, metric, kind, deadline.isoformat(), int(bool(c.user_has_entered)), hib,
         c.max_daily_submissions or 5, c.team_count, c.category)
    # Deep look (file sizes + full leaderboard) once a day per competition, only for kinds we can work on.
    stale = not old or not old["scored_at"] or old["scored_at"] < (now - timedelta(days=1)).isoformat()
    if not stale:
        return 0
    size, lb = None, []
    if kind in config.KINDS:
        try:
            size = sum(f.total_bytes or 0 for f in kaggle().competition_list_files(slug, page_size=200).files)
        except Exception:
            pass
        lb = leaderboard(slug, max_age_s=86400, hib=bool(hib))
    score, why = chance(c, kind, days, size, lb, hib, metric)
    db.x("UPDATE competitions SET interest_score=?, why=?, data_bytes=?, lb_top=?, lb_p10=?, lb_teams=?, scored_at=? "
         "WHERE slug=?", score, "; ".join(why), size, lb[0] if lb else None,
         lb[max(0, len(lb) // 10 - 1)] if lb else None, len(lb) or c.team_count, db.now(), slug)
    if not old:
        db.emit("Scout", "competition.found", {"title": c.title, "kind": kind, "chance": score, "why": why},
                competition=slug)
        if score >= config.MIN_CHANCE and not c.user_has_entered:
            db.emit("Scout", "competition.recommended", {"title": c.title, "chance": score,
                                                         "url": f"https://www.kaggle.com/competitions/{slug}/rules"},
                    competition=slug)
            db.open_task(slug, "accept_rules", f"https://www.kaggle.com/competitions/{slug}/rules",
                         f"New competition worth joining (chance score {score:.0f}/100): {c.title}. Join it on "
                         "Kaggle and click Done; the fleet takes it from there.")
    return 0 if old else 1


def download_small_files(slug, d, max_bytes=200e6, max_files=40):
    """For notebook-executed competitions: CSV/metadata files only (sample submission, labels, ids)."""
    tok, n = None, 0
    while n < max_files:
        r = kaggle().competition_list_files(slug, page_size=200, page_token=tok)
        for f in r.files:
            if (f.total_bytes or 0) < max_bytes and f.name.endswith((".csv", ".json", ".txt", ".md", ".parquet")):
                kaggle().competition_download_file(slug, f.name, path=str(d / Path(f.name).parent), quiet=True)
                n += 1
                if n >= max_files:
                    break
        tok = r.next_page_token
        if not tok:
            break
    for z in d.rglob("*.zip"):
        zipfile.ZipFile(z).extractall(z.parent)
        z.unlink()


def scout():
    if time.time() - db.setting("scout_at", 0) < config.SCOUT_SECONDS:
        return
    db.set_setting("scout_at", time.time())
    seen, now = {}, datetime.now(timezone.utc)
    for page in range(1, 11):  # 20 per page, latest deadline first; stop once a page has only ended ones
        r = kaggle().competitions_list(sort_by="latestDeadline", page=page)
        live = [c for c in (r.competitions or []) if c.deadline and c.deadline.replace(tzinfo=timezone.utc) > now]
        for c in live:
            seen[c.ref.rstrip("/").split("/")[-1]] = c
        if not live:
            break
    new = 0
    for slug, c in seen.items():
        if c.submissions_disabled:
            continue
        new += upsert_competition(c, now)
    db.x("UPDATE competitions SET state='finished' WHERE deadline < ? AND state != 'finished' AND COALESCE(practice,0)=0",
         now.isoformat())
    db.emit("Scout", "scout.done", {"listed": len(seen), "new": new})


# ---------------------------------------------------------------- Gatekeeper
def gatekeeper():
    """Work on the best-chance competitions. Joined ones start at once; others queue an accept-rules task."""
    # Re-scoring can drop a queued competition below the bar: withdraw its join request.
    for c in db.q("SELECT slug FROM competitions WHERE state='needs_rules' AND interest_score < ? AND opt_in=0",
                  config.MIN_CHANCE):
        db.x("UPDATE competitions SET state='scouted' WHERE slug=?", c["slug"])
        db.x("UPDATE human_tasks SET status='done' WHERE competition_slug=? AND kind='accept_rules'", c["slug"])
    # Joins made on kaggle.com are picked up on every pass (one cheap API call).
    try:
        now = datetime.now(timezone.utc)
        for c in kaggle().competitions_list(group="entered").competitions or []:
            slug = c.ref.rstrip("/").split("/")[-1]
            if not c.deadline or c.deadline.replace(tzinfo=timezone.utc) < now:
                continue
            before = db.one("SELECT rules_accepted, state FROM competitions WHERE slug=?", slug)
            if not before:
                upsert_competition(c, now)  # joined before the Scout ever saw it
            db.x("UPDATE competitions SET rules_accepted=1 WHERE slug=?", slug)
            db.x("UPDATE human_tasks SET status='done' WHERE competition_slug=? AND kind='accept_rules' AND status='open'", slug)
            if not before or (not before["rules_accepted"] and before["state"] in ("scouted", "needs_rules")):
                # You joined it yourself: that is an explicit request to work on it.
                db.x("UPDATE competitions SET opt_in=1 WHERE slug=?", slug)
                db.emit("Gatekeeper", "competition.joined", {"title": c.title}, competition=slug)
    except Exception:
        pass
    # Joined competitions the fleet cannot run (agent, paper, code-only...) are dedicated projects, not "join needed".
    kinds_sql = ",".join("?" * len(config.KINDS))
    for c in db.q(f"SELECT slug FROM competitions WHERE rules_accepted=1 AND state IN ('scouted','needs_rules') "
                  f"AND kind NOT IN ({kinds_sql}) AND manual=0 AND (opt_in=1 OR state='needs_rules')", *config.KINDS):
        db.x("UPDATE competitions SET state='project' WHERE slug=?", c["slug"])
        db.emit("Gatekeeper", "competition.project", {"note": "joined; handled as a dedicated project outside the fleet"},
                competition=c["slug"])
    active = db.one("SELECT COUNT(*) n FROM competitions WHERE state='active'")["n"]
    kinds = ",".join("?" * len(config.KINDS))
    # Opted-in (Done in the Inbox / "Work on it") or good-chance competitions; joined ones first.
    cands = db.q(f"SELECT * FROM competitions WHERE state IN ('scouted','needs_rules') "
                 f"AND ((kind IN ({kinds}) AND (interest_score >= ? OR opt_in=1)) OR manual=1) "
                 f"ORDER BY manual DESC, opt_in DESC, rules_accepted DESC, interest_score DESC, deadline LIMIT ?",
                 *config.KINDS, config.MIN_CHANCE, config.MAX_ACTIVE * 3 + 20)  # room for manual runs
    for c in cands:
        if active >= config.MAX_ACTIVE and not c["manual"]:  # Run now (you) always gets a slot
            continue
        slug, d = c["slug"], comp_dir(c["slug"]) / "data"
        try:
            d.mkdir(parents=True, exist_ok=True)
            if engines.executor_for(c)[0] == "kaggle":  # data stays on Kaggle; fetch only the small files locally
                kaggle().competition_list_files(slug)  # raises 403 if rules are not accepted
                download_small_files(slug, d)
            else:
                kaggle().competition_download_files(slug, path=str(d), quiet=True)
        except Exception as e:
            if "403" in str(e):
                db.x("UPDATE competitions SET state='needs_rules' WHERE slug=?", slug)
                db.open_task(slug, "accept_rules", f"https://www.kaggle.com/competitions/{slug}/rules",
                             f"Join this competition (chance score {c['interest_score']:.0f}/100): click 'Join' / "
                             "accept the rules on Kaggle. Only a human may do this; the fleet starts by itself after.")
                continue
            raise
        for z in d.glob("*.zip"):
            zipfile.ZipFile(z).extractall(d)
            z.unlink()
        active += 1
        db.x("UPDATE competitions SET state='active', rules_accepted=1 WHERE slug=?", slug)
        db.x("UPDATE human_tasks SET status='done' WHERE competition_slug=? AND kind='accept_rules'", slug)
        run_id = db.new_id("r")
        db.x("INSERT INTO runs (id, competition_slug, engine, status, budget_usd, budget_gpu_h, started_at) "
             "VALUES (?,?,?,?,?,?,?)", run_id, slug, config.ENGINE, "running",
             c["cap_usd"] or config.COMP_CAP_USD, c["cap_hours"] or config.COMP_CAP_HOURS, db.now())
        db.emit("Gatekeeper", "competition.unblocked", {"files": sorted(p.name for p in d.iterdir()),
                                                        "chance": c["interest_score"]}, run_id=run_id, competition=slug)


# ---------------------------------------------------------------- Budgets
def spend(slug=None, days=None):
    sql, args = "SELECT COALESCE(SUM(cost_usd),0) AS usd, COALESCE(SUM(tokens),0) AS tokens FROM events WHERE 1=1", []
    if slug:
        sql += " AND competition=?"; args.append(slug)
    if days:
        sql += " AND ts >= ?"; args.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    return db.one(sql, *args)


def billed_spend(slug=None, days=None):
    """Money actually billed per call (API-key providers). Calls covered by a Claude subscription are excluded:
    those are governed by the real plan usage and the plan reserve, not by dollar caps."""
    sql, args = "SELECT COALESCE(SUM(cost_usd),0) usd FROM llm_calls WHERE billing='api'", []
    if slug:
        sql += " AND competition=?"; args.append(slug)
    if days:
        sql += " AND ts >= ?"; args.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    return db.one(sql, *args)["usd"]


def hours(slug):
    return db.one("SELECT COALESCE(SUM(e.seconds),0)/3600.0 AS h FROM experiments e JOIN runs r ON e.run_id=r.id "
                  "WHERE r.competition_slug=?", slug)["h"]


def over_budget(c, run):
    weekly = db.setting("weekly_cap_usd", config.WEEKLY_CAP_USD)
    if billed_spend(days=7) >= weekly:
        db.set_setting("fleet_paused", True)
        db.open_task("*", "approve", "", f"Weekly cap ${weekly} reached. Fleet paused; raise the cap to resume.")
        return True
    if billed_spend(c["slug"]) >= run["budget_usd"] or hours(c["slug"]) >= run["budget_gpu_h"]:
        db.x("UPDATE competitions SET paused=1, note='cap reached' WHERE slug=?", c["slug"])
        db.open_task(c["slug"], "approve", "", "Competition budget cap reached. Raise its cap to resume.")
        return True
    return False


# ---------------------------------------------------------------- Solver + Critic
def task_for(c, with_strategy=True):
    d = comp_dir(c["slug"]) / "data"
    lines = [f"# Kaggle competition: {c['title']} ({c['slug']})", f"Metric: {c['metric']} "
             f"({'higher' if c['higher_is_better'] else 'lower'} is better)", "Files: " +
             ", ".join(f"{p.name} ({p.stat().st_size // 1024} KB)" for p in sorted(d.iterdir()) if p.is_file())]
    for p in [d / "train.csv", d / "test.csv", sample_file(d)]:
        if p and p.exists():
            df = pd.read_csv(p, nrows=5)
            lines.append(f"\n## {p.name}{' (submission template)' if p == sample_file(d) else ''}\n"
                         f"columns/dtypes: {dict(df.dtypes.astype(str))}\n{df.to_string()}")
    lines.append("\n## Goal\nMaximise the final (private) leaderboard rank. Trust robust CV over the public board.")
    fb = []  # the live situation: also sent alone on every conversation turn
    days = (datetime.fromisoformat(c["deadline"]) - datetime.now(timezone.utc)).days
    if c["practice"]:
        fb.append("PRACTICE (late submissions after the deadline): scored but not ranked; optimise for a strong score.")
    fb.append(f"Deadline in {days} days. Submissions today: {max(today_count(c['slug']), 0)}/{quota_for(c)} (Kaggle limit).")
    if c["lb_top"] is not None:
        fb.append(f"Leaderboard: {c['lb_teams']} teams, top public score {c['lb_top']}, top-10% cutoff {c['lb_p10']}.")
    if c["lb_rank"]:
        fb.append(f"Our current public rank: {c['lb_rank']}/{c['lb_teams']} (top {100 * c['lb_rank'] / c['lb_teams']:.1f}%).")
    fb += score_targets(c)
    for idea in db.setting(f"idea:{c['slug']}", []):
        fb.append(f"USER REQUEST (do this in the NEXT experiment, before the hypothesis queue): {idea}")
    for s in db.q("SELECT s.experiment_id, e.cv_mean, e.summary, s.lb_public FROM submissions s JOIN experiments e "
                  "ON s.experiment_id=e.id WHERE s.competition_slug=? AND s.lb_public IS NOT NULL", c["slug"]):
        fb.append(f"Submitted {s['experiment_id']} '{s['summary'][:80]}': CV {s['cv_mean']:.5f} -> public LB {s['lb_public']:.5f}")
    pairs = db.q("SELECT e.cv_mean cv, s.lb_public lb FROM submissions s JOIN experiments e ON s.experiment_id=e.id "
                 "WHERE s.competition_slug=? AND s.lb_public IS NOT NULL", c["slug"])
    if len(pairs) >= 3:
        import numpy as np
        r = np.corrcoef([p["cv"] for p in pairs], [p["lb"] for p in pairs])[0, 1]
        fb.append(f"CV<->public LB correlation over {len(pairs)} submissions: {r:+.2f} "
                     + ("(CV is a reliable guide)" if r > 0.7 else "(CV does NOT track the LB well: fix validation first)"))
    g = cv_lb_gap(c["slug"])
    if g["diverged"]:
        fb.append(f"WARNING: public LB differs from CV by {g['cv_lb_gap']:+.4f}. The validation is optimistic: prefer "
                  "simpler/regularised models, check for leakage and train/test shift, use repeated CV.")
    lines += fb
    ext = comp_dir(c["slug"]) / "external"
    if ext.exists():
        files = [str(p.relative_to(ext)) for p in sorted(ext.rglob("*")) if p.is_file()][:30]
        lines.append("\n## External data available under PODIUM_EXTERNAL\n" + "\n".join(f"- {f}" for f in files))
    plan = strategy_dir(c["slug"]) / "plan.md"
    v = db.setting(f"strategy:{c['slug']}", {}).get("version")
    plan_md = plan.read_text() if plan.exists() else ""
    if with_strategy and plan_md:
        lines.append(f"\n# Team strategy (v{v}), follow it\n{plan_md}")
    changed = bool(plan_md) and db.setting(f"conv:{c['slug']}:solver:plan_seen") != v
    return {"slug": c["slug"], "metric": c["metric"], "higher_is_better": bool(c["higher_is_better"]),
            "dir": d, "description": "\n".join(lines), "diverged": g["diverged"], "feedback": "\n".join(fb),
            "days_left": None if c["practice"] else days,
            "plan": plan_md, "plan_version": v, "plan_changed": changed, "memory": fleet_memory()}


# ---------------------------------------------------------------- fleet memory (learning across competitions)
def memory_file():
    return config.DATA_DIR / "memory" / "lessons.md"


def fleet_memory(limit=40):
    f = memory_file()
    return "\n".join(f.read_text().splitlines()[-limit:]) if f.exists() else ""


def remember_lessons(slug, plan_md):
    """Append the reflector's transferable lessons to the fleet-wide memory (deduplicated)."""
    if "## Transferable lessons" not in plan_md:
        return 0
    section = plan_md.split("## Transferable lessons", 1)[1].split("\n## ", 1)[0]
    new = [l.strip(" -*\t").replace("**", "") for l in section.splitlines()
           if l.strip().startswith(("-", "*")) and len(l.strip()) > 12]
    f = memory_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    known = f.read_text().lower() if f.exists() else ""
    added = [l for l in new if l.lower()[:80] not in known]
    with open(f, "a") as fh:
        for l in added:
            fh.write(f"- {l} (from {slug}, {db.now()[:10]})\n")
    if added:
        db.emit("Strategist", "memory.learned", {"lessons": added[:5]}, competition=slug)
    return len(added)


def score_targets(c):
    """What public score it takes to reach the next tiers, from the real leaderboard."""
    try:
        lb = leaderboard(c["slug"], hib=bool(c["higher_is_better"]))
    except Exception:
        lb = []
    if len(lb) < 20:
        return []
    best = db.one(f"SELECT {'MAX' if c['higher_is_better'] else 'MIN'}(lb_public) v FROM submissions WHERE competition_slug=?",
                  c["slug"])["v"]
    out = []
    for label, frac in (("top 25%", 0.25), ("top 10%", 0.10), ("top 5%", 0.05), ("top 1%", 0.01)):
        cut = lb[max(0, int(len(lb) * frac) - 1)]
        gap = f" (gap {abs(best - cut):.5f})" if best is not None else ""
        out.append(f"{label}: {'>=' if c['higher_is_better'] else '<='} {cut}{gap}")
    return ["Score targets on the public LB: " + "; ".join(out) + f"; leader {lb[0]}."]


def since_best(history, hib):
    """Experiments since the last new best CV (stagnation counter)."""
    best, n = None, 0
    for h in history:
        if h["cv_mean"] is None or h["critic_flags"] != "[]":
            n += 1
            continue
        if best is None or (h["cv_mean"] > best if hib else h["cv_mean"] < best):
            best, n = h["cv_mean"], 0
        else:
            n += 1
    return n


def public_notebooks(c, limit=5, pull=2):
    """Kaggle immersion: the competition's top public notebooks (titles, votes) and the code of the best ones,
    cached daily. Public, shareable ideas only; the strategist distils them, it never copies blindly."""
    sd = strategy_dir(c["slug"])
    cache = sd / "public_notebooks.md"
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        return cache.read_text()
    ks = []
    for sort in ("scoreDescending" if c["higher_is_better"] else "scoreAscending", "voteCount"):
        try:
            got = kaggle().kernels_list(competition=c["slug"], sort_by=sort, page_size=20) or []
        except Exception:
            continue
        got = [k for k in got if not re.match(r"(exercise|tutorial)\b", (k.title or "").lower())]
        ks += [k for k in got if k.ref not in {x.ref for x in ks}]
        if len(ks) >= limit:
            break
    ks = ks[:limit]
    if not ks:
        return ""
    parts = [f"- {k.title} ({getattr(k, 'total_votes', '?')} votes) {k.ref}" for k in ks]
    import tempfile
    for k in ks[:pull]:
        try:
            d = Path(tempfile.mkdtemp())
            kaggle().kernels_pull(k.ref, str(d))
            f = next(iter(sorted(d.glob("*"))), None)
            if f and f.suffix == ".ipynb":
                nb = json.loads(f.read_text())
                code = "\n\n".join("".join(x["source"]) for x in nb.get("cells", []) if x.get("cell_type") == "code")
            else:
                code = f.read_text() if f else ""
            parts.append(f"\n### Code of '{k.title}' ({getattr(k, 'total_votes', '?')} votes), truncated\n```python\n{code[:7000]}\n```")
        except Exception:
            continue
    md = "\n".join(parts)
    sd.mkdir(parents=True, exist_ok=True)
    cache.write_text(md)
    return md


def strategy_dir(slug):
    return comp_dir(slug) / "strategy"


def evidence(c, history):
    """Experiment log + leaderboard facts for the reflector."""
    lb = {s["experiment_id"]: s["lb_public"] for s in db.q("SELECT experiment_id, lb_public FROM submissions "
                                                            "WHERE competition_slug=?", c["slug"])}
    rows = ["| id | parent | summary | CV | ± | public LB | critic/review |", "|---|---|---|---|---|---|---|"]
    fmt = lambda v: "" if v is None else f"{v:.5f}"
    for h in history[-25:]:
        rows.append(f"| {h['id']} | {h['parent_id'] or ''} | {h['summary'][:90]} | {fmt(h['cv_mean'])} | "
                    f"{fmt(h['cv_std'])} | {fmt(lb.get(h['id']))} | {h['critic_flags'][:160]} |")
    days = (datetime.fromisoformat(c["deadline"]) - datetime.now(timezone.utc)).days
    rank = (f"Rank {c['lb_rank']}/{c['lb_teams']}, top public {c['lb_top']}, top-10% cutoff {c['lb_p10']}"
            if c["lb_rank"] else "Not ranked yet.") + f" Deadline in {days} days; daily submission limit {quota_for(c)}."
    stuck = since_best(history, bool(c["higher_is_better"]))
    extra = [f"Experiments since the last new best CV: {stuck}"
             + (" -> STAGNATION: change approach (research what top solutions do, new model families, ensembling)."
                if stuck >= config.STAGNATION else "")] + score_targets(c)
    return "\n".join(rows) + "\n\n" + rank + "\n" + "\n".join(extra)


EXTERNAL_OK = ("Playground", "Getting Started")  # categories whose rules allow public external data


def external_candidates(c):
    """Public Kaggle datasets related to the competition (only where external data is allowed)."""
    if c["category"] not in EXTERNAL_OK:
        return []
    words = re.sub(r"[^a-z0-9 ]", " ", c["title"].lower()).replace("predicting", "").strip()
    try:
        ds = kaggle().dataset_list(search=words, sort_by="votes") or []
    except Exception:
        return []
    return [{"ref": d.ref, "title": d.title, "mb": round((d.total_bytes or 0) / 1e6, 1), "votes": d.vote_count}
            for d in ds[:8] if d and (d.total_bytes or 0) < 500e6]


def fetch_external(c, plan_md, candidates):
    """Download datasets the strategy chose (only from the permitted candidate list)."""
    section = plan_md.split("## External data", 1)[1] if "## External data" in plan_md else ""
    section = section.split("\n## ", 1)[0]
    chosen = [d["ref"] for d in candidates if d["ref"] in section]  # any permitted dataset the strategy names
    for ref in chosen:
        dest = comp_dir(c["slug"]) / "external" / ref.replace("/", "__")
        if dest.exists():
            continue
        dest.mkdir(parents=True)
        kaggle().dataset_download_files(ref, path=str(dest), quiet=True, unzip=True)
        db.emit("Strategist", "external.downloaded", {"dataset": ref, "files": sorted(p.name for p in dest.rglob("*")
                                                                                       if p.is_file())[:20]},
                competition=c["slug"])
    return chosen


def ensure_strategy(c, run, history):
    """Profile once, write the first strategy, then reflect every REFLECT_EVERY experiments or on new LB scores."""
    sd, key = strategy_dir(c["slug"]), f"strategy:{c['slug']}"
    sd.mkdir(parents=True, exist_ok=True)
    task = task_for(c, with_strategy=False)
    prof = sd / "profile.md"
    if not prof.exists():
        md = engines.profile(task, sd / "profile_run", c=dict(c))
        prof.write_text(md)
        db.emit("Strategist", "profile.done", {"chars": len(md), "head": md[:300]}, run_id=run["id"], competition=c["slug"])
    meta = db.setting(key, {})
    scored = db.one("SELECT COUNT(*) n FROM submissions WHERE competition_slug=? AND lb_public IS NOT NULL", c["slug"])["n"]
    plan = sd / "plan.md"
    stuck = since_best(history, bool(c["higher_is_better"]))
    due = (not plan.exists() or len(history) - meta.get("experiments", 0) >= config.REFLECT_EVERY
           or (scored > meta.get("scored", 0) and len(history) > meta.get("experiments", 0))
           or (stuck >= config.STAGNATION and len(history) - meta.get("experiments", 0) >= 2))
    if due and stuck >= config.STAGNATION:
        db.emit("Strategist", "strategy.stagnation", {"since_best": stuck}, run_id=run["id"], competition=c["slug"])
    if not due:
        return
    version = meta.get("version", 0) + 1
    cands = external_candidates(c)
    pub = public_notebooks(c)
    if pub:
        task = {**task, "description": task["description"] + "\n\n# Top public notebooks of this competition (community "
                "knowledge: distil ideas, verify with our own CV, never copy blindly)\n" + pub}
    md, tokens, cost = engines.strategy(task, prof.read_text(), plan.read_text() if plan.exists() else None,
                                        evidence(c, history) if plan.exists() else None,
                                        book=engines.playbook(c["category"], c["kind"], prof.read_text()),
                                        external="\n".join(f"- {d['ref']}: {d['title']} ({d['mb']} MB, {d['votes']} votes)"
                                                           for d in cands))
    (sd / f"plan_v{version}.md").write_text(md)
    plan.write_text(md)
    db.set_setting(key, {"version": version, "experiments": len(history), "scored": scored, "at": db.now()})
    remember_lessons(c["slug"], md)
    if cands:
        fetch_external(c, md, cands)
    m = re.search(r"## Headline[^\n]*\n+\s*(.+)", md) or re.search(r"## Lessons learned[^\n]*\n+\s*[-*]?\s*(.+)", md)
    headline = m.group(1).strip() if m else md.strip().splitlines()[0].strip("# ")
    db.emit("Strategist", "strategy.updated", {"version": version, "headline": headline[:300],
                                               "reflection": version > 1}, run_id=run["id"], competition=c["slug"],
            tokens=tokens, cost_usd=cost)


def sample_file(d):
    """The submission template: sample_submission.csv, gender_submission.csv, ..."""
    return next(iter(sorted(d.glob("*submission*.csv"))), None)


def critic(exp_dir, task, cv_mean):
    """Deterministic validation: catches broken submissions and the classic ways CV lies."""
    flags = []
    if cv_mean is None:
        return ["run_failed"]
    if cv_mean != cv_mean:  # NaN
        return ["cv_is_nan"]
    sub, sample = exp_dir / "submission.csv", sample_file(task["dir"])
    if not sub.exists():
        return ["no_submission_file"]
    m = (task["metric"] or "").lower()
    if sample:
        s, ref = pd.read_csv(sub), pd.read_csv(sample)
        if list(s.columns) != list(ref.columns) or len(s) != len(ref):
            return ["bad_submission_format"]
        if s.isna().any().any():
            flags.append("nan_in_submission")
        idc, targets = ref.columns[0], list(ref.columns[1:])
        if set(s[idc].astype(str)) != set(ref[idc].astype(str)):
            flags.append("ids_do_not_match_template")
        train = task["dir"] / "train.csv"
        tr = pd.read_csv(train, usecols=lambda col: col in targets) if train.exists() else pd.DataFrame()
        for t in targets:
            col = s[t]
            if col.nunique() <= 1 and len(col) > 1:
                flags.append(f"constant_predictions:{t}")
            numeric = pd.api.types.is_numeric_dtype(col)
            if ("auc" in m or ("log" in m and "loss" in m)) and numeric and (col.min() < 0 or col.max() > 1):
                flags.append(f"probabilities_out_of_range:{t}")
            if "rmsle" in m and numeric and col.min() < 0:
                flags.append(f"negative_predictions_for_rmsle:{t}")
            if ("accuracy" in m or "f1" in m) and t in tr and tr[t].nunique() <= 50:
                if not set(col.astype(str)) <= set(tr[t].astype(str)):
                    flags.append(f"labels_not_seen_in_train:{t}")
    if task["higher_is_better"] and ("auc" in m or "accuracy" in m) and cv_mean >= 0.9995:
        flags.append("suspicious_perfect_cv")
    return flags


_running = {}  # slug -> Thread: one experiment at a time per competition, competitions in parallel


def solver(wait=False):
    """Start one experiment per active competition in the background so the fleet loop never blocks."""
    if fleet_paused():
        return
    for c in active_comps():
        t = _running.get(c["slug"])
        if t and t.is_alive():
            continue
        _running[c["slug"]] = t = threading.Thread(target=_solve_safe, args=(c,), daemon=True, name=f"solve-{c['slug']}")
        t.start()
    if wait:
        for t in list(_running.values()):
            t.join()


def container_prefix(slug):
    return f"podium-{re.sub(r'[^a-zA-Z0-9_.-]', '-', slug)[:60]}-"


def kill_experiments(slug):
    """Stop a competition's running experiment immediately (pause / stop / archive)."""
    import subprocess
    ids = subprocess.run(["docker", "ps", "-q", "--filter", f"name={container_prefix(slug)}"], capture_output=True,
                         text=True).stdout.split()
    if ids:
        subprocess.run(["docker", "kill", *ids], capture_output=True)
    return len(ids)


def still_wanted(slug):
    c = db.one("SELECT state, paused FROM competitions WHERE slug=?", slug)
    return bool(c) and c["state"] == "active" and not c["paused"] and not fleet_paused()


def _solve_safe(c):
    try:
        solve_one(c)
    except Exception as e:
        db.emit("Solver", "agent.error", {"error": str(e)[:500], "trace": traceback.format_exc()[-1500:]},
                competition=c["slug"])


def plan_hold(slug=None):
    """True (and one notice) while the Claude plan reserve/limit says the fleet must wait."""
    if llm.plan_ok("experiment"):
        if db.setting("plan_hold"):
            db.set_setting("plan_hold", False)
            db.emit("Fleet", "plan.resumed", {})
        return False
    if not db.setting("plan_hold"):
        db.set_setting("plan_hold", True)
        w = llm.plan_usage().get("windows", {})
        db.emit("Fleet", "plan.hold", {"seven_day": (w.get("seven_day") or {}).get("utilization"),
                                       "five_hour": (w.get("five_hour") or {}).get("utilization"),
                                       "reserve": config.PLAN_RESERVE})
    return True


def solve_one(c):
    run = db.one("SELECT * FROM runs WHERE competition_slug=? AND status='running' ORDER BY started_at DESC LIMIT 1",
                 c["slug"])
    if not run or over_budget(c, run):
        return
    if run["engine"] != "baseline" and plan_hold(c["slug"]):
        return
    history = db.q("SELECT * FROM experiments WHERE run_id=? ORDER BY created_at", run["id"])
    if len(history) >= config.MAX_EXPERIMENTS and since_best(history, bool(c["higher_is_better"])) >= config.PLATEAU:
        return finish_run(c, run, {"experiments": len(history)})
    if run["engine"] != "baseline":
        ensure_strategy(c, run, history)
    task = task_for(db.one("SELECT * FROM competitions WHERE slug=?", c["slug"]))
    orphan = find_orphan(c["slug"])
    if orphan:  # a restart interrupted this experiment: re-run the same script instead of losing it
        exp_id, prop = orphan
        db.emit("Solver", "experiment.resumed", {"experiment_id": exp_id, "summary": prop["summary"]},
                run_id=run["id"], competition=c["slug"])
        return run_experiment(c, run, task, prop, exp_id)
    prop = engines.ENGINES[run["engine"]](task, history)
    db.set_setting(f"conv:{c['slug']}:solver:plan_seen", task.get("plan_version"))  # the Solver has now seen this plan
    if prop is not None and not prop.get("blend") and "USER REQUEST" in task["feedback"]:
        db.set_setting(f"idea:{c['slug']}", [])  # your idea is in this experiment
    if prop is None:
        return finish_run(c, run, {"reason": "engine plan exhausted"})
    if not still_wanted(c["slug"]):  # paused/stopped while the AI was writing the experiment
        return
    exp_id = db.new_id("e")
    db.emit("Solver", "experiment.started", {"experiment_id": exp_id, "summary": prop["summary"],
                                             "blend": prop.get("blend", False)},
            run_id=run["id"], competition=c["slug"], tokens=prop["tokens"], cost_usd=prop["cost_usd"])
    exp_dir = comp_dir(c["slug"]) / "experiments" / exp_id
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "main.py").write_text(prop["code"])
    (exp_dir / "proposal.json").write_text(json.dumps({k: prop.get(k) for k in ("summary", "parent_id", "blend")}))
    return run_experiment(c, run, task, prop, exp_id)


def find_orphan(slug):
    """An experiment whose script was written but never recorded (server restarted mid-run), < 24 h old."""
    root = comp_dir(slug) / "experiments"
    if not root.exists():
        return None
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        pj = d / "proposal.json"
        if (pj.exists() and (d / "main.py").exists() and time.time() - pj.stat().st_mtime < 86400
                and not db.one("SELECT 1 FROM experiments WHERE id=?", d.name)):
            meta = json.loads(pj.read_text())
            return d.name, {"summary": meta.get("summary") or "resumed experiment", "parent_id": meta.get("parent_id"),
                            "code": (d / "main.py").read_text(), "tokens": 0, "cost_usd": 0.0, "blend": meta.get("blend")}
    return None


def run_experiment(c, run, task, prop, exp_id):
    exp_dir = comp_dir(c["slug"]) / "experiments" / exp_id
    done = exp_dir / "result.json"
    if done.exists() and (exp_dir / "submission.csv").exists():  # it already finished (e.g. before a restart): record it
        r = json.loads(done.read_text())
        log = (exp_dir / "log.txt").read_text() if (exp_dir / "log.txt").exists() else ""
        cv, std, secs = float(r["cv_mean"]), float(r.get("cv_std") or 0.0), 0.0
    else:
        cv, std, log, secs = engines.run_anywhere(dict(c), prop["code"], task["dir"], exp_dir, prev_dir=exp_dir.parent,
                                                  name=container_prefix(c["slug"]) + exp_id, plan_md=task.get("plan", ""))
    if cv is None and not still_wanted(c["slug"]):  # killed by pause/stop: not the Solver's mistake, don't record it
        db.emit("Solver", "experiment.cancelled", {"experiment_id": exp_id}, run_id=run["id"], competition=c["slug"])
        return
    flags = critic(exp_dir, task, cv)
    res = exp_dir / "result.json"
    scheme = str(json.loads(res.read_text()).get("cv_scheme") or "default")[:40] if res.exists() else None
    if db.one("SELECT 1 FROM experiments WHERE id=?", exp_id):  # already recorded (e.g. by a process shutting down)
        return
    db.x("INSERT OR IGNORE INTO experiments (id, run_id, parent_id, summary, cv_mean, cv_std, code_uri, sub_uri, "
         "critic_flags, seconds, created_at, cv_scheme) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", exp_id, run["id"],
         prop["parent_id"], prop["summary"], cv, std, str(exp_dir / "main.py"), str(exp_dir / "submission.csv"),
         json.dumps(flags), secs, db.now(), scheme)
    write_meta(exp_id)
    db.emit("Solver", "experiment.finished", {"experiment_id": exp_id, "cv_mean": cv, "cv_std": std,
                                              "seconds": round(secs, 1), "log_tail": log[-400:] if cv is None else ""},
            run_id=run["id"], competition=c["slug"])
    if flags:
        db.emit("Critic", "experiment.flagged", {"experiment_id": exp_id, "flags": flags},
                run_id=run["id"], competition=c["slug"])


def finish_run(c, run, payload):
    db.x("UPDATE runs SET status='done' WHERE id=?", run["id"])
    db.x("UPDATE competitions SET state='done' WHERE slug=?", c["slug"])
    db.emit("Solver", "run.done", payload, run_id=run["id"], competition=c["slug"])


REVIEW_PROMPT = """You are a strict Kaggle Grandmaster reviewing a solution script BEFORE it is submitted.
Be objective. Fail it only for real defects that would make the leaderboard score worse than its CV suggests,
or make the submission invalid:
- target leakage (features built from the target, from test labels, or from future rows in time series)
- preprocessing/encoders/scalers/target-encoding fitted on rows of the fold being scored
- early stopping, iteration count or hyperparameters selected on the fold being scored (this IS a defect:
  it inflates CV; early stopping must use an inner split of the training fold)
- validation scheme that does not match how test differs from train (e.g. random KFold on a time series,
  ignoring groups), so CV is not trustworthy
- train/test preprocessing mismatch, wrong row order or ids, wrong target transform/inverse transform
- metric implemented differently from the competition metric
Also give non-blocking SUGGESTIONS that would raise the score: provided data files or columns left unused, obvious
missing features, weak hyperparameters, cheap ensembling wins.
Reply with JSON only: {"verdict": "pass" or "fail", "issues": ["defect", ...], "suggestions": ["idea", ...]}"""


def review(exp, c):
    """LLM code review gate. Returns True if the experiment may be submitted."""
    if exp["review"]:
        return json.loads(exp["review"])["verdict"] == "pass"
    code = Path(exp["code_uri"]).read_text()
    log = Path(exp["code_uri"]).with_name("log.txt")
    prompt = (f"{task_for(c)['description']}\n\n## Script (CV {exp['cv_mean']:.5f} ± {exp['cv_std']:.5f})\n"
              f"```python\n{code}\n```\n## Run log tail\n{log.read_text()[-2000:] if log.exists() else ''}")
    text, tokens, cost = llm.complete(REVIEW_PROMPT, prompt, max_tokens=4000, role="review", agent="Critic", competition=c["slug"],
                                      purpose="code review")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        r = json.loads(m.group(0))
        r = {"verdict": "pass" if str(r.get("verdict")).lower() == "pass" else "fail", "issues": r.get("issues", []),
             "suggestions": r.get("suggestions", [])[:6]}
    except Exception:
        r = {"verdict": "fail", "issues": ["review reply was not valid JSON"]}
    db.x("UPDATE experiments SET review=? WHERE id=?", json.dumps(r), exp["id"])
    db.emit("Critic", "experiment.reviewed", {"experiment_id": exp["id"], **r}, competition=c["slug"],
            tokens=tokens, cost_usd=cost)
    if r["verdict"] != "pass":
        flags = json.loads(exp["critic_flags"]) + ["review_failed: " + "; ".join(r["issues"])[:600]]
        db.x("UPDATE experiments SET critic_flags=? WHERE id=?", json.dumps(flags), exp["id"])
    write_meta(exp["id"])
    return r["verdict"] == "pass"


# ---------------------------------------------------------------- Submitter
def today_count(slug):
    day = datetime.now(timezone.utc).date().isoformat()
    return db.one("SELECT COUNT(*) AS n FROM submissions WHERE competition_slug=? AND created_at >= ?", slug, day)["n"]


def quota_for(c):
    kaggle_max = c["max_daily_submissions"] or 5
    return min(config.DAILY_SUBMISSIONS, kaggle_max) if config.DAILY_SUBMISSIONS else kaggle_max


def kaggle_today(slug):
    """Submissions Kaggle counted today (UTC day, as Kaggle resets)."""
    day = datetime.now(timezone.utc).date()
    try:
        subs = kaggle().competition_submissions(slug) or []
    except Exception:
        return 0
    return sum(1 for k in subs if k and k.date and k.date.date() == day)


def track_projects():
    """Dedicated projects are built outside the fleet, but their real Kaggle submissions are tracked here
    (every 10 min): status, best public score, rank, and an alert when a score or an error arrives."""
    for c in db.q("SELECT * FROM competitions WHERE state='project'"):
        slug, key = c["slug"], f"proj:{c['slug']}"
        seen = db.setting(key, {})
        if time.time() - seen.get("checked", 0) < 600:
            continue
        try:
            subs = [s for s in (kaggle().competition_submissions(slug) or []) if s]
        except Exception:
            continue
        hib = bool(c["higher_is_better"])
        scored = [float(s.public_score) for s in subs if s.public_score not in (None, "")]
        best = (max(scored) if hib else min(scored)) if scored else None
        latest = subs[0] if subs else None
        status = str(latest.status).split(".")[-1].lower() if latest else "none"
        state = {"checked": time.time(), "count": len(subs), "status": status, "best": best,
                 "latest": (latest.description or "")[:120] if latest else ""}
        for s in subs:
            sid = str(s.ref)
            if s.public_score not in (None, "") and sid not in seen.get("scored", []):
                db.emit("Submitter", "submission.scored", {"experiment_id": (s.description or "")[:60],
                                                          "lb_public": float(s.public_score)}, competition=slug)
            if s.error_description and sid not in seen.get("errored", []):
                db.emit("Submitter", "agent.error", {"error": f"Kaggle rejected submission: {s.error_description}"},
                        competition=slug)
        state["scored"] = [str(s.ref) for s in subs if s.public_score not in (None, "")]
        state["errored"] = [str(s.ref) for s in subs if s.error_description]
        db.set_setting(key, state)
        rank = None
        if best is not None:
            lb = leaderboard(slug, hib=hib)
            rank = 1 + sum(1 for x in lb if (x > best if hib else x < best)) if lb else None
            db.x("UPDATE competitions SET lb_rank=?, lb_teams=?, rank_at=? WHERE slug=?", rank, len(lb) or None,
                 db.now(), slug)
        db.x("UPDATE competitions SET note=? WHERE slug=?",
             f"{len(subs)} submission(s); latest {status}" + (f"; best public {best}" if best is not None else ""), slug)


def submitter():
    from . import projects
    track_projects()
    projects.auto_bootstrap()
    projects.track()
    for c in db.q("SELECT * FROM competitions WHERE state IN ('active','done')"):
        slug, sign = c["slug"], 1 if c["higher_is_better"] else -1
        refresh_scores(slug)
        update_rank(c)
        pick_finals(c)
        if not config.AUTO_SUBMIT or fleet_paused() or c["paused"]:
            continue
        quota = quota_for(c)
        used = max(today_count(slug), kaggle_today(slug))  # Kaggle's count includes manual submissions
        if used >= quota:
            continue
        cands = db.q("SELECT e.* FROM experiments e JOIN runs r ON e.run_id=r.id WHERE r.competition_slug=? "
                     "AND e.critic_flags='[]' AND e.cv_mean IS NOT NULL "
                     "AND e.id NOT IN (SELECT experiment_id FROM submissions)", slug)
        if not cands:
            continue
        # CV is only comparable within one validation scheme: prefer the newest scheme.
        latest = max(cands, key=lambda e: e["created_at"])["cv_scheme"]
        cands = [e for e in cands if e["cv_scheme"] == latest]
        best = max(cands, key=lambda e: sign * e["cv_mean"])
        prev = db.q("SELECT e.cv_mean FROM submissions s JOIN experiments e ON s.experiment_id=e.id "
                    "WHERE s.competition_slug=? AND COALESCE(e.cv_scheme,'') = COALESCE(?, '')", slug, latest)
        best_prev = max((sign * p["cv_mean"] for p in prev), default=None)
        gain = sign * best["cv_mean"] - best_prev if best_prev is not None else None
        noise = max(best["cv_std"] or 0, 1e-4)
        any_prev = db.one("SELECT COUNT(*) n FROM submissions WHERE competition_slug=?", slug)["n"]
        if gain is None:
            reason = "first submission" if not any_prev else f"first submission under validation scheme '{latest}'"
        elif gain > noise:
            reason = f"CV gain {gain:.5f} > noise {noise:.5f}"
        elif gain > -noise and used < quota // 2 and c["state"] == "active":  # no probes on plateaued runs
            # Within noise of our best: use spare quota (at most half a day's) to let the LB arbitrate.
            reason = f"LB probe: CV within noise ({gain:+.5f}) of best submitted"
        else:
            continue
        run = db.one("SELECT engine FROM runs WHERE id=?", best["run_id"])
        if config.REVIEW and run["engine"] != "baseline" and config.MODEL != "none" and not review(best, c):
            continue  # rejected: flagged for the Solver to fix next pass; next candidate gets its turn
        archive = comp_dir(slug) / "submissions" / f"{db.now()[:19].replace(':', '')}_{best['id']}.csv"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(Path(best["sub_uri"]).read_bytes())
        archive.with_suffix(".json").write_text(json.dumps({"experiment": best["id"], "cv": best["cv_mean"],
                                                            "cv_std": best["cv_std"], "reason": reason}, indent=2))
        exp_dir = Path(best["sub_uri"]).parent
        if c["kind"] == "code" and (exp_dir / "kernel.json").exists():  # code competition: submit the notebook
            from . import kaggle_runner
            r = kaggle_runner.submit(slug, exp_dir, f"podium {best['id']} cv={best['cv_mean']:.5f}")
        else:
            r = kaggle().competition_submit(str(archive), f"podium {best['id']} cv={best['cv_mean']:.5f}", slug,
                                            quiet=True)
        db.x("INSERT INTO submissions (id, experiment_id, competition_slug, kaggle_ref, reason, created_at) "
             "VALUES (?,?,?,?,?,?)", db.new_id("s"), best["id"], slug, str(getattr(r, "ref", "") or ""), reason, db.now())
        db.emit("Submitter", "submission.sent", {"experiment_id": best["id"], "cv": best["cv_mean"], "reason": reason},
                competition=slug)


def cv_lb_gap(slug):
    """Latest scored submission: LB - CV. Diverged when the gap exceeds fold noise (3 std, min 0.01)."""
    r = db.one("SELECT s.lb_public, e.cv_mean, e.cv_std FROM submissions s JOIN experiments e ON s.experiment_id=e.id "
               "WHERE s.competition_slug=? AND s.lb_public IS NOT NULL ORDER BY s.created_at DESC LIMIT 1", slug)
    if not r:
        return {"cv_lb_gap": None, "diverged": False}
    gap = r["lb_public"] - r["cv_mean"]
    return {"cv_lb_gap": gap, "diverged": abs(gap) > max(3 * (r["cv_std"] or 0), 0.01)}


def refresh_scores(slug):
    pending = db.q("SELECT * FROM submissions WHERE competition_slug=? AND lb_public IS NULL", slug)
    if not pending:
        return
    remote = kaggle().competition_submissions(slug) or []
    for s in pending:
        for k in remote:
            if k and s["experiment_id"] in (k.description or "") and k.public_score not in (None, ""):
                db.x("UPDATE submissions SET lb_public=?, lb_private=? WHERE id=?", float(k.public_score),
                     float(k.private_score) if k.private_score not in (None, "") else None, s["id"])
                db.emit("Submitter", "submission.scored", {"experiment_id": s["experiment_id"],
                                                          "lb_public": float(k.public_score)}, competition=slug)
                g = cv_lb_gap(slug)
                if g["diverged"]:
                    db.emit("Critic", "cv_lb.diverged", {"experiment_id": s["experiment_id"],
                                                         "gap": round(g["cv_lb_gap"], 5)}, competition=slug)


def update_rank(c):
    """Our best public score placed on the full public leaderboard (refreshed every 30 min)."""
    slug, sign = c["slug"], 1 if c["higher_is_better"] else -1
    best = db.one(f"SELECT {'MAX' if sign > 0 else 'MIN'}(lb_public) v FROM submissions WHERE competition_slug=?", slug)["v"]
    if best is None or (c["rank_at"] and time.time() - datetime.fromisoformat(c["rank_at"]).timestamp() < 1800):
        return
    lb = leaderboard(slug, hib=sign > 0)
    if not lb:
        return
    rank = 1 + sum(1 for x in lb if sign * x > sign * best)
    db.x("UPDATE competitions SET lb_rank=?, lb_teams=?, lb_top=?, lb_p10=?, rank_at=? WHERE slug=?", rank, len(lb),
         lb[0], lb[max(0, len(lb) // 10 - 1)], db.now(), slug)
    if rank != c["lb_rank"]:
        db.emit("Submitter", "rank.updated", {"rank": rank, "teams": len(lb), "top_pct": round(100 * rank / len(lb), 1),
                                             "best_public": best, "previous": c["lb_rank"]}, competition=slug)


def pick_finals(c):
    """Best CV plus the next-best different experiment. Kaggle has no API for this, so we ask the human."""
    slug, sign = c["slug"], 1 if c["higher_is_better"] else -1
    if db.setting(f"final_manual:{slug}"):
        return
    subs = db.q("SELECT s.id, e.cv_mean, s.lb_public FROM submissions s JOIN experiments e ON s.experiment_id=e.id "
                "WHERE s.competition_slug=?", slug)
    subs = db.q("SELECT s.id, e.cv_mean, e.cv_std, s.lb_public FROM submissions s JOIN experiments e "
                "ON s.experiment_id=e.id WHERE s.competition_slug=?", slug)
    by_cv = sorted(subs, key=lambda s: -sign * s["cv_mean"])
    # Hedge against private-LB risk: the most robust candidate (CV penalised by its fold std), not the public-LB
    # winner, which is the classic way to overfit a small public split.
    by_robust = sorted(subs, key=lambda s: -(sign * s["cv_mean"] - (s["cv_std"] or 0)))
    top = {s["id"] for s in by_cv[:1]}
    top |= {next((s["id"] for s in by_robust + by_cv if s["id"] not in top), None)} - {None}
    for s in subs:
        db.x("UPDATE submissions SET is_final_pick=? WHERE id=?", int(s["id"] in top), s["id"])
    days_left = (datetime.fromisoformat(c["deadline"]) - datetime.now(timezone.utc)).days
    if top and days_left <= 3:
        db.open_task(slug, "approve", f"https://www.kaggle.com/competitions/{slug}/submissions",
                     "Deadline soon. Optional: select the 2 final picks marked in Podium on Kaggle (no API for it). "
                     "If you do nothing, Kaggle auto-selects your best public-LB submissions.")


# ---------------------------------------------------------------- ADK loop
def _adk_fleet():
    from google.adk.agents import BaseAgent, LoopAgent
    from google.adk.events import Event

    class Step(BaseAgent):
        fn: Callable

        async def _run_async_impl(self, ctx):
            try:
                await asyncio.to_thread(self.fn)
            except Exception as e:
                db.emit(self.name, "agent.error", {"error": str(e)[:500], "trace": traceback.format_exc()[-1500:]})
            yield Event(author=self.name)

    class Sleep(BaseAgent):
        async def _run_async_impl(self, ctx):
            await asyncio.sleep(config.LOOP_SECONDS)
            yield Event(author=self.name)

    return LoopAgent(name="Fleet", sub_agents=[
        Step(name="Maintenance", fn=maintenance), Step(name="Scout", fn=scout), Step(name="Gatekeeper", fn=gatekeeper),
        Step(name="Solver", fn=solver),
        Step(name="Submitter", fn=submitter), Sleep(name="Sleep")])


async def run_fleet():
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=_adk_fleet(), app_name="podium")
    s = await runner.session_service.create_session(app_name="podium", user_id="fleet")
    db.emit("Fleet", "fleet.started", {"engine": config.ENGINE, "model": config.MODEL, "sandbox": config.SANDBOX})
    async for _ in runner.run_async(user_id="fleet", session_id=s.id,
                                    new_message=types.Content(role="user", parts=[types.Part(text="run")])):
        pass
