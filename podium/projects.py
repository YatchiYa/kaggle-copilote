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
    (kdir / "source-metadata.json").write_text(json.dumps(meta, indent=1))
    user = _kaggle().get_config_value("username")
    name = f"podium-{slug[:24]}-{tag}".lower().replace("_", "-")[:50]
    code = next((p.name for p in kdir.iterdir() if p.suffix in (".ipynb", ".py", ".r", ".R")), None)
    meta.setdefault("code_file", code)
    meta.setdefault("language", "python")
    meta.update({"id": f"{user}/{name}", "title": name, "is_private": True})
    meta.pop("id_no", None)
    for k in ("dataset_sources", "kernel_sources", "model_sources", "competition_sources"):
        meta[k] = [x for x in (meta.get(k) or []) if x]  # sources we can't see come back as "" and break the push/run
    meta["competition_sources"] = sorted(set(meta["competition_sources"] + [slug]))
    (kdir / "kernel-metadata.json").write_text(json.dumps(meta, indent=1))
    resp = _kaggle().kernels_push(str(kdir), acc=meta.get("machine_shape") if meta.get("enable_gpu") else None)
    info = {"ref": f"{user}/{name}", "version": getattr(resp, "version_number", None), "agent": f"fork of {src['ref']}",
            "source": src, "tasks": [], "purpose": "baseline", "submit_after": True, "submitted": False,
            "url": getattr(resp, "url", None), "error": getattr(resp, "error", None) or None, "launched": db.now()}
    (run_dir / "run.json").write_text(json.dumps(info, indent=1))
    db.emit("Gatekeeper", "project.baseline", {"from": src["ref"], "title": src.get("title"), "run": tag,
                                               "error": info["error"]}, competition=slug)
    return info


DIAGNOSE_PROMPT = """You are a senior Kaggle engineer. A notebook run on Kaggle failed. From its metadata and the tail of
its log, find the root cause. Reply with JSON only:
{"cause": "one sentence", "category": "environment|dependency|data_path|gpu|timeout|memory|code|quota|infra",
 "transient": true/false (true only if simply re-running unchanged can succeed: GPU unavailable, infra hiccup),
 "fix": "the exact change to make (metadata field, cell, package, path...)",
 "metadata_patch": {} (only fields of kernel-metadata.json to change if that alone fixes it, e.g. machine_shape,
                       enable_gpu, docker_image, dataset_sources; otherwise empty)}"""
SAFE_PATCH = {"machine_shape", "enable_gpu", "enable_internet", "dataset_sources", "model_sources",
              "competition_sources", "kernel_sources", "docker_image", "docker_image_pinning_type"}
MAX_RETRIES = 2


def diagnose(run_dir, info):
    """Reflect on a failed run: read its log, ask for the root cause and fix, apply safe fixes and re-run."""
    out = run_dir / "output"
    out.mkdir(exist_ok=True)
    try:
        _kaggle().kernels_output(info["ref"], str(out), force=True)
    except Exception:
        pass
    log = "".join(p.read_text(errors="replace") for p in sorted(out.glob("*.log")))[-8000:]
    meta_f = run_dir / "kernel" / "kernel-metadata.json"
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    refs = [json.loads(f.read_text()) for f in sorted(run_dir.parent.parent.glob("experiments/reference/*/kernel-metadata.json"))]
    refs += [json.loads(f.read_text()) for f in [run_dir / "kernel" / "source-metadata.json"] if f.exists()]
    known = "\n".join(json.dumps({k: m.get(k) for k in SAFE_PATCH | {"id"} if m.get(k) is not None}) for m in refs)
    ctx = (f"Known-good metadata of the official/source notebooks (copy fields from here if they fix it):\n{known or 'none'}\n\n"
           f"Kaggle failure message: {info.get('failure') or ''}\n\nkernel-metadata.json:\n"
           f"{json.dumps({k: v for k, v in meta.items() if k != 'id_no'}, indent=1)}\n\nLog tail:\n{log}")
    try:
        text, tokens, cost = llm.complete(DIAGNOSE_PROMPT, ctx, role="review", agent="Critic", purpose="run diagnosis",
                                          competition=info.get("competition"))
        d = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception as e:
        d = {"cause": f"could not diagnose automatically: {str(e)[:200]}", "transient": False, "fix": "", "metadata_patch": {}}
    patch = {k: v for k, v in (d.get("metadata_patch") or {}).items() if k in SAFE_PATCH}
    d["at"] = db.now()
    (run_dir / "diagnosis.json").write_text(json.dumps(d, indent=1))
    retry = (d.get("transient") or patch) and info.get("retries", 0) < MAX_RETRIES and meta_f.exists()
    if retry:
        meta.update(patch)
        meta_f.write_text(json.dumps(meta, indent=1))
        resp = _kaggle().kernels_push(str(meta_f.parent))
        info.update(version=getattr(resp, "version_number", info.get("version")), retries=info.get("retries", 0) + 1,
                    status="queued", failed=False)
    db.emit("Critic", "project.run_failed", {"run": run_dir.name, "cause": d.get("cause"), "fix": d.get("fix"),
                                             "retried": bool(retry), "patch": patch}, competition=info.get("competition"))
    return d


def track():
    """Every project Kaggle run (baselines, dev runs): follow it; on error reflect, fix and retry;
    submit finished baselines (code competitions) once."""
    for c in db.q("SELECT * FROM competitions WHERE state='project'"):
        d = project_dir(c["slug"])
        for rj in sorted((d / "runs").glob("*/run.json")) if (d / "runs").exists() else []:
            info = json.loads(rj.read_text())
            if info.get("submitted") or info.get("failed") or info.get("collected") or \
                    time.time() - info.get("checked", 0) < 120:
                continue
            info["checked"], info["competition"] = time.time(), c["slug"]
            try:
                st = _kaggle().kernels_status(info["ref"])
                status = str(getattr(st, "status", st)).split(".")[-1].lower()
                info["failure"] = getattr(st, "failure_message", "") or ""
            except Exception:
                rj.write_text(json.dumps(info, indent=1))
                continue
            info["status"] = status
            if status in ("error", "cancelacknowledged"):
                diag = diagnose(rj.parent, info)
                info["failed"] = info.get("status") != "queued"  # not retried: needs a code fix
                info["cause"] = diag.get("cause")
            elif status == "complete" and not info.get("submit_after"):
                info["collected"] = True
                try:
                    _kaggle().kernels_output(info["ref"], str(rj.parent / "output"), force=True)
                except Exception:
                    pass
                db.emit("Gatekeeper", "project.run_done", {"run": rj.parent.name}, competition=c["slug"])
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


def auto_baseline():
    """Autopilot: a joined code-type project with a plan, no Kaggle submission and no run yet gets its public baseline.
    One project at a time (Kaggle GPU quota), never for agent/paper competitions (kind 'other')."""
    from .agents import fleet_paused
    if not config.PROJECT_AUTOPILOT or not config.AUTO_SUBMIT or fleet_paused():
        return
    comps = db.q("SELECT * FROM competitions WHERE state='project' AND kind!='other' AND deadline > ?", db.now())
    for c in comps:  # one live run anywhere is enough
        rd = project_dir(c["slug"]) / "runs"
        for rj in rd.glob("*/run.json") if rd.exists() else []:
            r = json.loads(rj.read_text())
            if not (r.get("failed") or r.get("submitted") or r.get("collected")):
                return
    for c in comps:
        d = project_dir(c["slug"])
        if (d / "PLAN.md").exists() and not (d / "runs").exists() and not db.setting(f"proj:{c['slug']}", {}).get("count"):
            try:
                baseline(c["slug"])
            except Exception as e:
                (d / "runs").mkdir(parents=True, exist_ok=True)  # don't retry every pass
                db.emit("Gatekeeper", "agent.error", {"error": f"autopilot baseline failed: {str(e)[:300]}"}, competition=c["slug"])
            return


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
