"""Dedicated projects: competitions the fleet cannot run end-to-end (agents, notebooks, papers...).

Podium still drives them:
  bootstrap(slug)  -> projects/<slug>/: official pages, top public notebooks, an expert PLAN.md with a dated timeline
  baseline(slug)   -> fork the best-scoring public notebook as a private notebook, run it on Kaggle, and (code
                      competitions) submit it automatically when it finishes: a leaderboard position to improve from
  track()          -> each pass: follow baseline runs, submit finished ones, record results
"""
import json
import re
import threading
import time

from . import config, db, llm

PLAN_PROMPT = """You are the lead of a Kaggle Grandmaster team with 20+ years of competition wins. This competition
cannot be solved by our tabular fleet; it is a DEDICATED PROJECT. Read the official pages and the top public notebooks
below (and research the web for past winning approaches if you have web search). Write the project PLAN as markdown:

# <competition title>: project plan
**Competitions:** `<slug>` (deadline YYYY-MM-DD)
**Status (<today>):** one line.

## 1. What is scored  (submission format, metric, limits: runtime, GPU, internet, daily submissions)
## 2. Expert read  (what actually decides the leaderboard here; what top public notebooks do; their scores)
## 3. Approach  (fastest path to a leaderboard position, e.g. fork the best public notebook; then the improvement
   levers, ranked by expected gain, concrete to this competition)
## 4. Timeline  (a markdown table `| Dates | Milestone |` from today to the deadline, dates written like
   `Oct 4–8` or `Nov 2`, 5-8 rows, first row = baseline submission)
## 5. Risks
## 6. Needs from you  (only what a human must do)

Be specific and honest about difficulty. Under 900 words."""


def project_dir(slug):
    """projects/<slug>/, or an existing project folder whose PLAN.md already names this competition."""
    root = config.ROOT / "projects"
    for plan in sorted(root.glob("*/PLAN.md")) if root.exists() else []:
        if f"`{slug}`" in plan.read_text() or plan.parent.name == slug:
            return plan.parent
    return root / slug


def _kaggle():
    from .agents import kaggle
    return kaggle()


def fetch_official(slug, d):
    from kagglesdk.competitions.types import competition_api_service as t
    out = d / "official"
    out.mkdir(parents=True, exist_ok=True)
    req = t.ApiListCompetitionPagesRequest()
    req.competition_name = slug
    try:
        with _kaggle().build_kaggle_client() as k:
            r = k.competitions.competition_api_client.list_competition_pages(req)
        pages = getattr(r, "pages", None) or getattr(r, "competition_pages", None) or []
    except Exception:
        pages = []
    for p in pages:
        dd = p.to_dict()
        name = re.sub(r"[^A-Za-z0-9 _-]", "", dd.get("name") or dd.get("pageName") or "page")
        (out / f"{name}.md").write_text(dd.get("content") or dd.get("postBody") or "")
    return sorted(out.glob("*.md"))


def public_notebooks(slug, hib=True, n=6):
    rows, seen = [], set()
    for sort in ("scoreDescending" if hib else "scoreAscending", "voteCount"):
        try:
            for k in _kaggle().kernels_list(competition=slug, sort_by=sort, page_size=n) or []:
                if k.ref not in seen:
                    seen.add(k.ref)
                    rows.append({"ref": k.ref, "title": k.title, "votes": getattr(k, "total_votes", None), "by": sort})
        except Exception:
            continue
    return rows


_busy = set()


def bootstrap(slug, force=False):
    if slug in _busy:
        return {"busy": True, "note": "the plan is already being written"}
    _busy.add(slug)
    try:
        return _bootstrap(slug, force)
    finally:
        _busy.discard(slug)


def _bootstrap(slug, force):
    c = db.one("SELECT * FROM competitions WHERE slug=?", slug)
    if not c:
        raise ValueError(f"unknown competition {slug}")
    d = project_dir(slug)
    plan = d / "PLAN.md"
    if plan.exists() and not force:
        return {"dir": str(d), "plan": str(plan), "created": False}
    d.mkdir(parents=True, exist_ok=True)
    pages = fetch_official(slug, d)
    nbs = public_notebooks(slug, bool(c["higher_is_better"]))
    (d / "public_notebooks.json").write_text(json.dumps(nbs, indent=1))
    official = "\n\n".join(f"## {p.stem}\n{p.read_text()[:6000]}" for p in pages
                           if not p.stem.lower().startswith(("foundational", "tracks", "judges")))
    ctx = (f"Competition: {c['title']} (`{slug}`), kind {c['kind']}, category {c['category']}, metric {c['metric']}, "
           f"deadline {c['deadline'][:10]}, teams {c['team_count']}, daily submissions {c['max_daily_submissions']}. "
           f"Today is {db.now()[:10]}.\n\n# Official pages\n{official[:30000]}\n\n# Top public notebooks\n"
           + "\n".join(f"- {n['title']} ({n['votes']} votes, {n['by']}) {n['ref']}" for n in nbs))
    md, tokens, cost = llm.complete(PLAN_PROMPT, ctx, role="strategy", agent="Strategist", competition=slug,
                                    purpose="project plan", tools=["WebSearch", "WebFetch"] if config.RESEARCH else None)
    if f"`{slug}`" not in md:  # the Ongoing page links plans to competitions through this line
        md = md.replace("\n", f"\n**Competitions:** `{slug}` (deadline {c['deadline'][:10]})\n", 1)
    plan.write_text(md)
    db.emit("Strategist", "project.bootstrapped", {"dir": str(d), "pages": len(pages), "notebooks": len(nbs)},
            competition=slug, tokens=tokens, cost_usd=cost)
    return {"dir": str(d), "plan": str(plan), "created": True}


def baseline(slug, ref=None):
    """Fork the best-scoring public notebook (or `ref`) privately and run it on Kaggle."""
    c = db.one("SELECT * FROM competitions WHERE slug=?", slug)
    d = project_dir(slug)
    nbs = public_notebooks(slug, bool(c["higher_is_better"])) if not ref else [{"ref": ref, "title": ref}]
    if not nbs:
        raise ValueError("no public notebook to start from")
    src = nbs[0]
    n = len(list((d / "runs").glob("baseline-*"))) + 1 if (d / "runs").exists() else 1
    tag = f"baseline-{n}"
    run_dir = d / "runs" / tag
    kdir = run_dir / "kernel"
    kdir.mkdir(parents=True, exist_ok=True)
    _kaggle().kernels_pull(src["ref"], str(kdir), metadata=True)
    meta = json.loads((kdir / "kernel-metadata.json").read_text())
    user = _kaggle().get_config_value("username")
    name = f"podium-{slug[:24]}-{tag}".lower().replace("_", "-")[:50]
    code = next((p.name for p in kdir.iterdir() if p.suffix in (".ipynb", ".py", ".r", ".R")), None)
    meta.setdefault("code_file", code)
    meta.setdefault("language", "python")
    meta.update({"id": f"{user}/{name}", "title": name, "is_private": True})
    meta.pop("id_no", None)
    for k in ("competition_sources",):
        meta[k] = sorted(set((meta.get(k) or []) + [slug]))
    (kdir / "kernel-metadata.json").write_text(json.dumps(meta, indent=1))
    resp = _kaggle().kernels_push(str(kdir), acc=meta.get("machine_shape") if meta.get("enable_gpu") else None)
    info = {"ref": f"{user}/{name}", "version": getattr(resp, "version_number", None), "agent": f"fork of {src['ref']}",
            "source": src, "tasks": [], "purpose": "baseline", "submit_after": True, "submitted": False,
            "url": getattr(resp, "url", None), "error": getattr(resp, "error", None) or None, "launched": db.now()}
    (run_dir / "run.json").write_text(json.dumps(info, indent=1))
    db.emit("Gatekeeper", "project.baseline", {"from": src["ref"], "title": src.get("title"), "run": tag,
                                               "error": info["error"]}, competition=slug)
    return info


def track():
    """Follow project baseline runs; submit finished ones (code competitions) once."""
    for c in db.q("SELECT * FROM competitions WHERE state='project'"):
        d = project_dir(c["slug"])
        for rj in sorted((d / "runs").glob("*/run.json")) if (d / "runs").exists() else []:
            info = json.loads(rj.read_text())
            if not info.get("submit_after") or info.get("submitted") or info.get("failed"):
                continue
            if time.time() - info.get("checked", 0) < 120:
                continue
            info["checked"] = time.time()
            try:
                st = _kaggle().kernels_status(info["ref"])
                status = str(getattr(st, "status", st)).split(".")[-1].lower()
            except Exception:
                rj.write_text(json.dumps(info, indent=1))
                continue
            info["status"] = status
            if status in ("error", "cancelacknowledged"):
                info["failed"] = True
                db.emit("Gatekeeper", "agent.error", {"error": f"baseline notebook {info['ref']} ended with {status}: "
                                                               f"{getattr(st, 'failure_message', '')}"}, competition=c["slug"])
            elif status == "complete":
                out = rj.parent / "output"
                out.mkdir(exist_ok=True)
                try:
                    _kaggle().kernels_output(info["ref"], str(out), force=True)
                except Exception:
                    pass
                files = sorted(p.name for p in out.rglob("submission*") if p.is_file())
                fname = files[0] if files else "submission.csv"
                try:
                    _kaggle().competition_submit_code(fname, f"podium baseline: fork of {info['source']['ref']}",
                                                      c["slug"], kernel=info["ref"], kernel_version=info["version"], quiet=True)
                    info["submitted"] = True
                    db.emit("Submitter", "submission.sent", {"experiment_id": "baseline", "cv": 0.0,
                                                             "reason": f"project baseline (fork of {info['source']['ref']})"},
                            competition=c["slug"])
                except Exception as e:
                    info["failed"] = True
                    db.emit("Submitter", "agent.error", {"error": f"baseline submission failed: {str(e)[:300]}"},
                            competition=c["slug"])
            rj.write_text(json.dumps(info, indent=1))


_worker = None


def auto_bootstrap():
    """Every joined project competition gets its workspace and plan, in a background thread (one at a time)."""
    global _worker
    if _worker and _worker.is_alive():
        return
    todo = [c["slug"] for c in db.q("SELECT slug FROM competitions WHERE state='project' AND deadline > ?", db.now())
            if c["slug"] not in _busy and not (project_dir(c["slug"]) / "PLAN.md").exists()]
    if todo:
        def go(slug=todo[0]):
            try:
                bootstrap(slug)
            except Exception as e:
                db.emit("Strategist", "agent.error", {"error": f"project bootstrap failed: {str(e)[:300]}"}, competition=slug)
        _worker = threading.Thread(target=go, daemon=True)
        _worker.start()
