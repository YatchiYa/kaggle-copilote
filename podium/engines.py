"""Solver engines. Each engine turns (task, history) into one Python script; the sandbox runs it.

Script contract (same for every engine):
  - read competition files from $PODIUM_TASK (read-only)
  - write $PODIUM_OUT/submission.csv in sample_submission format
  - print one final line: PODIUM_RESULT {"cv_mean": <float>, "cv_std": <float>}
To add an engine (AIDE, R&D-Agent, MLE-STAR...), add a function to ENGINES with the same signature.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, llm

CONTRACT = """You are a Kaggle Grandmaster with 20+ years of competition wins. Implement the next experiment of the
team strategy: the top untested item of its Hypothesis queue, unless the evidence clearly says something else
gives a bigger gain. One focused change per experiment, built on the best script so far.
Write ONE self-contained Python script for this Kaggle competition.
Rules:
- Read files from the directory in env var PODIUM_TASK (read-only). Write outputs only to env var PODIUM_OUT.
- Use exactly int(os.environ["PODIUM_CPUS"]) threads/jobs (n_jobs, num_threads, thread_count); the machine is shared.
- No internet. Available: Python 3.12, pandas 3 (text columns have dtype 'str', not object: test with
  pd.api.types.is_numeric_dtype), numpy, scikit-learn, lightgbm, xgboost, catboost, scipy, optuna (tuning),
  torch (CPU only: small MLPs/embeddings for ensemble diversity), statsmodels, category_encoders.
- Use the validation scheme from the strategy (it mimics train->test) and the exact competition metric.
  Never fit anything (encoders, scalers, imputers, target stats) on rows of the fold being scored.
  Early stopping and any tuning must use an inner split of the TRAINING fold, never the fold being scored
  (then refit/predict with the chosen iteration count).
- Final test predictions: average the fold models (or refit on all data with the CV-chosen settings); use
  multiple seeds when cheap.
- Write PODIUM_OUT/submission.csv with exactly the columns, dtypes and row count of the submission template
  (the *submission*.csv file in PODIUM_TASK).
- External public data (if the task lists any) is read-only under env var PODIUM_EXTERNAL; use it as the strategy says.
- PODIUM_WORK is a persistent read-write workspace shared by all experiments of this competition. Use it to go faster
  and to keep improving across experiments:
  * folds: save/load fold indices as PODIUM_WORK/folds_<cv_scheme>.npy so every experiment uses identical splits;
  * feature cache: save expensive feature frames as PODIUM_WORK/feat_<short-hash-of-recipe>.parquet and reuse them;
  * tuning: optuna.create_study(study_name="<model>_<cv_scheme>", storage=f"sqlite:///{PODIUM_WORK}/optuna.db",
    load_if_exists=True, direction=...) so studies continue across experiments instead of restarting.
- Also write PODIUM_OUT/oof.csv: out-of-fold predictions for the train rows you validated on, same columns as the
  submission (id column + targets, raw scores/probabilities rather than rounded labels when the metric allows).
  Later blending experiments combine these files.
- Write PODIUM_OUT/diagnostics.md (max 60 lines): per-fold scores, top-25 feature importances (or coefficients),
  the worst-scoring segments of the OOF predictions (by the most relevant categorical / time bucket), and anything
  surprising. It is fed back to you next round, so make it useful for deciding the next experiment.
- Before printing PODIUM_RESULT, self-check with asserts: submission shape/columns/ids match the template,
  no NaN, predictions in the valid range for the metric. Fail loudly rather than write a bad file.
- Last line printed must be: PODIUM_RESULT {"cv_mean": <float>, "cv_std": <float>, "cv_scheme": "<short id>"}
  cv_scheme names the validation scheme (e.g. "strat5", "fwd16x4", "group5"). Keep it identical when you keep
  the scheme; change it when you change folds/split logic, because scores from different schemes are not comparable.
- Keep total runtime under %d minutes.
Reply format: first line exactly `SUMMARY: <one sentence: what this experiment changes and why>`, then the script
in a single ```python block.""" % (
    config.EXPERIMENT_TIMEOUT_S // 60)

BASELINE = r'''
import json, os, numpy as np, pandas as pd
from sklearn.model_selection import KFold, StratifiedKFold
from sklearn import metrics
from sklearn.ensemble import HistGradientBoostingClassifier as HGBC, HistGradientBoostingRegressor as HGBR
from sklearn.ensemble import RandomForestClassifier as RFC, RandomForestRegressor as RFR
T, O = os.environ["PODIUM_TASK"], os.environ["PODIUM_OUT"]
METRIC, PARAMS, FAMILY = __METRIC__, __PARAMS__, __FAMILY__
import glob
tr, te = pd.read_csv(f"{T}/train.csv"), pd.read_csv(f"{T}/test.csv")
ss = pd.read_csv(sorted(glob.glob(f"{T}/*submission*.csv"))[0])
idc, targets = ss.columns[0], [c for c in ss.columns[1:]]
assert all(t in tr.columns for t in targets), f"targets {targets} not in train"
feats = [c for c in te.columns if c != idc and c in tr.columns]
X = pd.concat([tr[feats], te[feats]])
cat = []
for c in feats:
    if not pd.api.types.is_numeric_dtype(X[c]):
        X[c] = X[c].astype("category").cat.codes
        cat.append(X[c].nunique() <= 250)
    else:
        cat.append(False)
Xtr, Xte = X.iloc[:len(tr)].values, X.iloc[len(tr):].values
m = METRIC.lower()
def score(y, p):
    if "auc" in m: return metrics.roc_auc_score(y, p)
    if "rmsle" in m: return metrics.mean_squared_log_error(y, np.clip(p, 0, None)) ** .5
    if "rmse" in m or "root mean" in m: return metrics.mean_squared_error(y, p) ** .5
    if "mae" in m or "absolute" in m: return metrics.mean_absolute_error(y, p)
    if "log" in m and "loss" in m: return metrics.log_loss(y, p)
    if "f1" in m: return metrics.f1_score(y, p, average="macro")
    if "r2" in m: return metrics.r2_score(y, p)
    return metrics.accuracy_score(y, p) if clf else metrics.mean_squared_error(y, p) ** .5
proba = "auc" in m or ("log" in m and "loss" in m)
scores_all = []
for t in targets:
    y = tr[t]
    clf = not pd.api.types.is_numeric_dtype(y) or y.dtype == bool or (y.nunique() <= 20 and pd.api.types.is_integer_dtype(y))
    if clf:
        classes = np.unique(y); yy = np.searchsorted(classes, y.values)
    else:
        yy = y.values.astype(float)
    if "rmsle" in m: yy_fit = np.log1p(yy)
    else: yy_fit = yy
    folds = (StratifiedKFold if clf else KFold)(5, shuffle=True, random_state=42)
    oof, pred, scores = np.zeros(len(tr)), np.zeros(len(te)), []
    for a, b in folds.split(Xtr, yy):
        if FAMILY == "hgb":
            mdl = (HGBC if clf else HGBR)(categorical_features=np.array(cat), random_state=42, **PARAMS)
        else:
            mdl = (RFC if clf else RFR)(n_jobs=int(os.environ.get("PODIUM_CPUS", 1)), random_state=42, **PARAMS)
        mdl.fit(Xtr[a], yy_fit[a])
        if clf and proba: f = lambda Z: mdl.predict_proba(Z)[:, 1]
        elif "rmsle" in m: f = lambda Z: np.expm1(mdl.predict(Z))
        else: f = mdl.predict
        oof[b] = f(Xtr[b]); pred += f(Xte) / 5
        scores.append(score(yy[b], oof[b]))
    scores_all.append(scores)
    if clf and not proba:
        pred = classes[np.rint(pred).clip(0, len(classes) - 1).astype(int)]
    ss[t] = pred
ss[idc] = te[idc].values
ss.to_csv(f"{O}/submission.csv", index=False)
if len(targets) == 1 and idc in tr.columns:
    pd.DataFrame({idc: tr[idc].values, targets[0]: oof}).to_csv(f"{O}/oof.csv", index=False)
s = np.mean(scores_all, axis=0)
print("PODIUM_RESULT " + json.dumps({"cv_mean": float(s.mean()), "cv_std": float(s.std())}))
'''

BASELINE_PLAN = [
    ("hgb", {}, "HistGradientBoosting defaults"),
    ("hgb", {"learning_rate": 0.05, "max_iter": 600, "early_stopping": True}, "HGB lr 0.05, 600 iters, early stop"),
    ("hgb", {"learning_rate": 0.03, "max_iter": 1500, "max_leaf_nodes": 63, "early_stopping": True,
             "l2_regularization": 1.0}, "HGB deeper, lr 0.03, l2 1.0"),
    ("rf", {"n_estimators": 400, "min_samples_leaf": 3}, "RandomForest 400 trees"),
]


def baseline(task, history):
    if len(history) >= len(BASELINE_PLAN):
        return None
    fam, params, summary = BASELINE_PLAN[len(history)]
    code = (BASELINE.replace("__METRIC__", repr(task["metric"] or "")).replace("__PARAMS__", repr(params))
            .replace("__FAMILY__", repr(fam)))
    return {"summary": summary, "code": code, "parent_id": history[0]["id"] if history else None,
            "tokens": 0, "cost_usd": 0.0}


BLEND = """Write ONE self-contained Python script that blends earlier experiments of this competition.
Their outputs are read-only under env var PODIUM_PREV: PODIUM_PREV/<experiment_id>/oof.csv (out-of-fold predictions
on train rows) and PODIUM_PREV/<experiment_id>/submission.csv (test predictions). Train data is in PODIUM_TASK.
- Align rows by id; use only experiments whose oof.csv exists and covers the same rows.
- Fit non-negative blend weights (or rank averaging) on OOF predictions with the competition metric, and estimate
  the blended score honestly: fit weights inside K folds of the OOF rows, score on the held-out fold, report mean/std.
- Prefer diverse models; drop experiments that do not help.
- Write PODIUM_OUT/submission.csv (template format) and PODIUM_OUT/oof.csv (blended OOF), self-check with asserts.
- Last line printed must be: PODIUM_RESULT {"cv_mean": <float>, "cv_std": <float>}
Reply format: first line exactly `SUMMARY: <one sentence naming the blend>`, then the script in a ```python block."""
BLEND_EVERY = 4  # every 4th experiment is a blend once 3+ good experiments have OOF files


def _exp_report(h, full_log=False):
    """What one finished experiment taught us: score, verdicts, diagnostics, reviewer suggestions, log on failure."""
    d = Path(h["code_uri"]).parent
    out = [f"Experiment {h['id']} ({h['summary'][:160]}): " + (
        f"CV {h['cv_mean']:.5f} ± {h['cv_std']:.5f} [scheme {h.get('cv_scheme')}]" if h["cv_mean"] is not None else "FAILED")]
    if h["critic_flags"] not in ("[]", None):
        out.append(f"Rejected by critic/reviewer: {h['critic_flags'][:600]}")
    if h.get("review"):
        rv = json.loads(h["review"])
        if rv.get("suggestions"):
            out.append("Reviewer suggestions: " + "; ".join(rv["suggestions"])[:800])
    diag = d / "diagnostics.md"
    if diag.exists():
        out.append("Diagnostics:\n" + diag.read_text()[:2500])
    log = d / "log.txt"
    if (h["cv_mean"] is None or full_log) and log.exists():
        out.append("Log tail:\n" + log.read_text()[-2500:])
    return "\n".join(out)


def llm_engine(task, history):
    """The Solver is ONE conversation per competition: a full briefing on the first turn, then each turn reports
    what the previous experiment taught and asks for the next one. Tree search: build on the best, fix failures,
    blend periodically. Falls back to a fresh re-briefed conversation when the model changes or after N turns."""
    ok = [h for h in history if h["cv_mean"] is not None and h["critic_flags"] == "[]"]
    if ok:  # compare like with like: only experiments on the most recent validation scheme
        scheme = ok[-1].get("cv_scheme")
        ok = [h for h in ok if h.get("cv_scheme") == scheme]
    best = (max if task["higher_is_better"] else min)(ok, key=lambda h: h["cv_mean"]) if ok else None
    last = history[-1] if history else None
    with_oof = [h for h in ok if Path(h["code_uri"]).with_name("oof.csv").exists()]
    if len(with_oof) >= 3 and len(history) % BLEND_EVERY == BLEND_EVERY - 1:
        top = sorted(with_oof, key=lambda h: h["cv_mean"], reverse=task["higher_is_better"])[:6]
        listing = "\n".join(f"- {h['id']}: CV {h['cv_mean']:.5f} ± {h['cv_std']:.5f} · {h['summary'][:120]}" for h in top)
        text, tokens, cost = llm.complete(BLEND, f"{task['description']}\n\n## Experiments available\n{listing}",
                                          role="experiment", agent="Solver", competition=task["slug"], purpose="blend")
        return {**_parse(text, "Blend of top experiments"), "parent_id": best["id"], "tokens": tokens,
                "cost_usd": cost, "blend": True}

    parent, mode = select_parent(ok, history, task)

    key = f"{task['slug']}:solver"
    turn = llm.session_state(key, "experiment")
    parts = []
    if turn == 0:  # briefing: everything, plus what the fleet already learned on other competitions
        parts.append(task["description"])
        if task.get("memory"):
            parts.append("## Fleet memory (lessons from earlier competitions)\n" + task["memory"])
        if history:
            parts.append("## Experiments so far (most recent last)\n" + "\n\n".join(_exp_report(h) for h in history[-8:]))
        parts.append("This is a long-running conversation for this competition: after each experiment you will receive "
                     "its results and leaderboard feedback. Learn from every round; keep improving the rank.")
    else:  # continuation: only what is new since the last turn
        if last:
            parts.append("## Result of your last experiment\n" + _exp_report(last))
        parts.append("## Leaderboard & situation now\n" + task.get("feedback", ""))
        if task.get("plan_changed"):
            parts.append(f"## The strategy was updated (v{task.get('plan_version')}): follow it\n{task.get('plan', '')}")
    if best:
        parts.append(f"## Best so far: {best['id']} (CV {best['cv_mean']:.5f} ± {best['cv_std']:.5f}) {best['summary'][:160]}")
    if mode == "final":
        parts.append("## FINAL ROUND (deadline close): no new ideas. Rebuild the best pipeline/blend with 5 seeds (and a "
                     "full-data refit where the strategy allows), average the predictions, and keep validation identical. "
                     "Robustness beats a tiny CV gain now. Start SUMMARY with 'FINAL'.")
    if mode == "draft":
        parts.append("## EXPLORE: write a substantially DIFFERENT approach from every previous experiment (another model "
                     "family or feature paradigm, e.g. linear/NN/CatBoost-native-categoricals/target-transform), keeping "
                     "the same validation and cv_scheme. Its job is ensemble diversity, not to beat the best alone.")
    elif parent:
        why = "the current best" if best and parent["id"] == best["id"] else "a promising branch (tree search: less explored)"
        parts.append(f"## Improve this node: {parent['id']}, {why} (CV {parent['cv_mean']:.5f}) {parent['summary'][:160]}")
        if turn == 0 or (last and last["id"] != parent["id"]):
            parts.append(f"Its code (build on it):\n```python\n{Path(parent['code_uri']).read_text()}\n```")
    else:
        parts.append("Start with the strategy's validation design and a strong, simple gradient-boosting baseline.")
    if task.get("diverged"):
        parts.append("PRIORITY: CV does not match the public leaderboard, so the validation is untrustworthy. Unless the "
                     "best script already implements the strategy's validation design exactly, THIS experiment must "
                     "rebuild validation to that design (new cv_scheme) before any other change.")
    parts.append("Write the next experiment: ONE focused change with the highest expected leaderboard gain, as a full script.")
    text, tokens, cost = llm.complete(CONTRACT, "\n\n".join(parts), role="experiment", agent="Solver",
                                      competition=task["slug"], purpose="experiment", session=key)
    return {**_parse(text, "LLM experiment"), "parent_id": None if mode == "draft" else (parent or best or {}).get("id"),
            "tokens": tokens, "cost_usd": cost}


DRAFT_EVERY = 6  # every 6th experiment explores a new branch from the root (diversity)
UCB_C = 0.5  # exploration weight; values are CV ranks in [0, 1]


def select_parent(ok, history, task):
    """MCTS-style node selection over the experiment tree. Returns (parent, mode) with mode in
    'final' (deadline close: multi-seed rebuild), 'draft' (new root branch), 'improve'."""
    import math
    if task.get("days_left") is not None and task["days_left"] <= 3 and ok and \
            not any(h["summary"].upper().startswith("FINAL") for h in history[-3:]):
        best = sorted(ok, key=lambda h: h["cv_mean"], reverse=task["higher_is_better"])[0]
        return best, "final"
    if len(ok) >= 3 and len(history) % DRAFT_EVERY == DRAFT_EVERY - 1:
        return None, "draft"
    if not ok:
        return None, "improve"
    ranked = sorted(ok, key=lambda h: h["cv_mean"], reverse=not task["higher_is_better"])  # worst -> best
    value = {h["id"]: (i + 1) / len(ranked) for i, h in enumerate(ranked)}
    kids = {h["id"]: 0 for h in ok}
    for h in history:
        if h.get("parent_id") in kids:
            kids[h["parent_id"]] += 1
    n = len(history) + 1
    pick = max(ok, key=lambda h: value[h["id"]] + UCB_C * math.sqrt(math.log(n) / (1 + kids[h["id"]])))
    return pick, "improve"


def _parse(text, default_summary):
    m = re.search(r"```python\n(.*?)```", text, re.S)
    if not m:
        raise RuntimeError("LLM reply had no python block")
    head = text[:m.start()].strip()
    tagged = re.search(r"SUMMARY:\s*(.+)", text)
    summary = tagged.group(1).strip() if tagged else (
        re.sub(r"^(here'?s|one concrete improvement:?)\s*", "", head.splitlines()[-1], flags=re.I) if head else "")
    return {"summary": (summary or default_summary)[:200], "code": m.group(1)}


ENGINES = {"baseline": baseline, "llm": llm_engine}


def share():
    """Fair CPU/memory share per parallel experiment, so competitions don't starve each other."""
    n = max(1, int(config.MAX_ACTIVE))
    cpus = max(1, (os.cpu_count() or 2) // n)
    mem_gb = max(2, int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9 * 0.75 / n))
    return cpus, mem_gb


def executor_for(c):
    """'docker' (local sandbox) or 'kaggle' (private notebook), and whether it needs a GPU."""
    gpu = c.get("kind") in ("cv", "nlp", "audio") or (c.get("kind") == "code" and (c.get("data_bytes") or 0) > 500e6)
    if config.EXECUTOR in ("docker", "kaggle"):
        return config.EXECUTOR, gpu and config.EXECUTOR == "kaggle"
    return ("kaggle", gpu) if c.get("kind") in ("code", "cv", "nlp", "audio") else ("docker", False)


def kaggle_sources(plan_md):
    """Kaggle Datasets/Models a strategy asks to attach to notebooks (offline pretrained weights)."""
    sec = plan_md.split("## Kaggle sources", 1)[1].split("\n## ", 1)[0] if "## Kaggle sources" in (plan_md or "") else ""
    ds = re.findall(r"dataset:\s*`?([\w.-]+/[\w.-]+)", sec)
    ms = re.findall(r"model:\s*`?([\w.-]+/[\w.-]+/[\w.-]+/[\w.-]+(?:/\d+)?)", sec)
    return ds[:10], ms[:10]


def run_anywhere(c, code, task_dir, out_dir, prev_dir=None, name=None, plan_md=""):
    """Dispatch an experiment to the right executor."""
    where, gpu = executor_for(c)
    if where == "kaggle":
        from . import kaggle_runner
        ds, ms = kaggle_sources(plan_md)
        return kaggle_runner.run(code, c["slug"], out_dir, out_dir.name, gpu=gpu, datasets=ds, models=ms)
    return execute(code, task_dir, out_dir, prev_dir=prev_dir, name=name)


def execute(code, task_dir, out_dir, prev_dir=None, name=None):
    """Run a generated script in a throwaway sandbox. Returns (cv_mean, cv_std, log, seconds)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "main.py").write_text(code)
    cpus, mem_gb = share()
    ext = task_dir.parent / "external"  # data/competitions/<slug>/external/<owner__dataset>/
    work = task_dir.parent / "work"  # persistent per-competition workspace (folds, feature cache, optuna.db)
    work.mkdir(exist_ok=True)
    threads = {k: str(cpus) for k in ("PODIUM_CPUS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}
    if config.SANDBOX == "docker":
        cmd = ["docker", "run", "--rm", *(["--name", name] if name else []), "--network", "none",
               "--user", f"{os.getuid()}:{os.getgid()}",
               "--cpus", str(cpus), "--memory", f"{mem_gb}g", *[x for k, v in threads.items() for x in ("-e", f"{k}={v}")],
               "-e", "PODIUM_TASK=/task", "-e", "PODIUM_OUT=/out", "-e", "HOME=/tmp",
               "-e", "PODIUM_PREV=/prev", "-v", f"{task_dir.resolve()}:/task:ro", "-v", f"{out_dir.resolve()}:/out",
               *(["-v", f"{prev_dir.resolve()}:/prev:ro"] if prev_dir else []),
               *(["-e", "PODIUM_EXTERNAL=/external", "-v", f"{ext.resolve()}:/external:ro"] if ext.exists() else []),
               "-e", "PODIUM_WORK=/work", "-v", f"{work.resolve()}:/work",
               config.SANDBOX_IMAGE, "python", "/out/main.py"]
        env = None
    else:
        # ponytail: local mode has no isolation, only an empty env (no secrets). Use docker for LLM code.
        cmd = [sys.executable, str(out_dir / "main.py")]
        env = {**threads, "PATH": os.environ["PATH"], "HOME": str(out_dir), "PODIUM_TASK": str(task_dir.resolve()),
               "PODIUM_OUT": str(out_dir.resolve()),
               "PODIUM_PREV": str(prev_dir.resolve()) if prev_dir else "",
               "PODIUM_EXTERNAL": str(ext.resolve()) if ext.exists() else "", "PODIUM_WORK": str(work.resolve())}
    if name and config.SANDBOX == "docker":  # a stale container with this name (e.g. from a restart) blocks the run
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=config.EXPERIMENT_TIMEOUT_S)
        log = p.stdout[-20000:] + "\n" + p.stderr[-20000:]
        if p.returncode:
            hint = {137: f"killed: OUT OF MEMORY (container limit {mem_gb} GB). Use float32, fewer/lighter features "
                         "held at once, smaller batches, del + gc.collect() between folds.",
                    124: "timed out."}.get(p.returncode, "the script exited with an error (see traceback above).")
            log += f"\n[podium] container exit code {p.returncode}: {hint}"
    except subprocess.TimeoutExpired:
        log = f"TIMEOUT after {config.EXPERIMENT_TIMEOUT_S}s: the experiment must finish within " \
              f"{config.EXPERIMENT_TIMEOUT_S // 60} minutes (fewer folds/seeds/rounds, or cache features in PODIUM_WORK)."
        if name and config.SANDBOX == "docker":
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    (out_dir / "log.txt").write_text(log)
    res = re.findall(r"PODIUM_RESULT (\{.*\})", log)
    if not res:
        return None, None, log, time.time() - t0
    r = json.loads(res[-1])
    (out_dir / "result.json").write_text(json.dumps(r))
    return float(r["cv_mean"]), float(r.get("cv_std") or 0.0), log, time.time() - t0


# ---------------------------------------------------------------- expert loop: profile -> strategy -> reflect
PROFILE = r'''
import glob, json, os, numpy as np, pandas as pd
T, O = os.environ["PODIUM_TASK"], os.environ["PODIUM_OUT"]
out = []
p = lambda *a: out.append(" ".join(str(x) for x in a))
files = sorted(glob.glob(f"{T}/*"))
p("## Files"); [p(f"- {os.path.basename(f)}: {os.path.getsize(f)/1e6:.1f} MB") for f in files]
read = lambda f: pd.read_csv(f, low_memory=False)
tr = read(f"{T}/train.csv") if os.path.exists(f"{T}/train.csv") else None
te = read(f"{T}/test.csv") if os.path.exists(f"{T}/test.csv") else None
subs = sorted(glob.glob(f"{T}/*submission*.csv")); ss = read(subs[0]) if subs else None
for f in files:
    if f.endswith(".csv") and os.path.basename(f) not in ("train.csv", "test.csv") and f not in subs:
        d = pd.read_csv(f, nrows=3); p(f"- extra table {os.path.basename(f)} columns: {list(d.columns)}")
if tr is None or te is None or ss is None:
    p("Non-standard layout: no train.csv/test.csv/submission template.")
else:
    idc, targets = ss.columns[0], list(ss.columns[1:])
    p(f"\n## Shapes\ntrain {tr.shape}, test {te.shape}, submission {ss.shape}; id column `{idc}`; targets {targets}")
    for t in targets:
        if t in tr:
            y = tr[t]
            if pd.api.types.is_numeric_dtype(y) and y.nunique() > 20:
                p(f"target `{t}`: regression, mean {y.mean():.4g}, std {y.std():.4g}, min {y.min():.4g}, max {y.max():.4g}, skew {y.skew():.3g}, zeros {(y==0).mean():.1%}")
            else:
                p(f"target `{t}`: classification, {y.nunique()} classes, distribution {y.value_counts(normalize=True).round(3).head(10).to_dict()}")
    p("\n## Columns (train | test)")
    p("| column | dtype | unique | missing train | missing test | sample |"); p("|---|---|---|---|---|---|")
    for c in tr.columns[:80]:
        mt = f"{te[c].isna().mean():.1%}" if c in te else "n/a (not in test)"
        p(f"| {c} | {tr[c].dtype} | {tr[c].nunique()} | {tr[c].isna().mean():.1%} | {mt} | {str(tr[c].dropna().iloc[0])[:30] if tr[c].notna().any() else ''} |")
    dates = []
    for c in tr.columns:
        if tr[c].dtype == object or "date" in c.lower() or "time" in c.lower():
            try:
                d = pd.to_datetime(tr[c].dropna().head(200), errors="raise")
                if d.dt.year.between(1950, 2100).all(): dates.append(c)
            except Exception: pass
    for c in dates:
        if c in te:
            a, b = pd.to_datetime(tr[c], errors="coerce"), pd.to_datetime(te[c], errors="coerce")
            p(f"\ntime column `{c}`: train {a.min()} -> {a.max()}, test {b.min()} -> {b.max()}"
              + ("  => TEST IS AFTER TRAIN: use time-based validation (forward chaining), never random KFold." if b.min() >= a.max() else ""))
    ov = set(tr[idc]) & set(te[idc]) if idc in tr else set()
    p(f"\nid overlap train/test: {len(ov)}; duplicate train rows (excluding id/targets): {tr.drop(columns=[c for c in [idc]+targets if c in tr]).duplicated().sum()}")
    common = [c for c in te.columns if c in tr.columns and c != idc and c not in dates]
    if common:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import cross_val_predict
        from sklearn.metrics import roc_auc_score
        n = min(len(tr), len(te), 30000)
        X = pd.concat([tr[common].sample(n, random_state=0), te[common].sample(n, random_state=0)])
        for c in X.columns:
            if not pd.api.types.is_numeric_dtype(X[c]): X[c] = X[c].astype("category").cat.codes
        y = np.r_[np.zeros(n), np.ones(n)]
        pr = cross_val_predict(HistGradientBoostingClassifier(max_iter=100), X, y, cv=3, method="predict_proba")[:, 1]
        auc = roc_auc_score(y, pr)
        p(f"\n## Adversarial validation (train vs test)\nAUC {auc:.3f} " + ("(train and test look alike: random/stratified KFold is fine)" if auc < 0.6 else "(SHIFT between train and test: make validation mimic test, consider dropping/transforming drifting features)"))
        if auc >= 0.6:
            m = HistGradientBoostingClassifier(max_iter=100).fit(X, y)
            from sklearn.inspection import permutation_importance
            imp = permutation_importance(m, X.iloc[::10], y[::10], n_repeats=3, random_state=0)
            top = sorted(zip(imp.importances_mean, X.columns), reverse=True)[:8]
            p("drifting features: " + ", ".join(f"{c} ({v:.3f})" for v, c in top))
open(f"{O}/profile.md", "w").write("\n".join(out))
print("PODIUM_RESULT " + json.dumps({"cv_mean": 0.0, "cv_std": 0.0}))
'''

EXPERT = """You are the lead of a Kaggle Grandmaster team with 20+ years of competition wins across tabular, time
series, NLP and vision. You know the winning playbooks: validation that mimics the private test split, strong GBDT
baselines (LightGBM/XGBoost/CatBoost) tuned with care, target/frequency encodings done inside folds, domain
features, multi-seed averaging, diverse models (GBDT, linear, NN), hill-climbing/stacking ensembles on OOF, and
trusting CV over the public leaderboard unless they correlate. You are evidence-driven: every decision cites the data
profile or experiment results. You never use leakage or rule-breaking tricks."""

STRATEGY = EXPERT + """

Write the competition STRATEGY as markdown with exactly these sections:
## Expert team  (the 2-3 specialists with 20+ years in THIS problem's domain that this competition needs, e.g. airline
   customer-experience analyst, retail demand forecaster, radiologist; one line each on what insight they bring)
## Headline  (one sentence: the core bet)
## Problem framing  (what is predicted, metric behaviour, what decides the leaderboard)
## Validation design  (the exact CV scheme that mimics train->test; why; what would make it lie)
## Feature plan  (ranked ideas, specific to these columns)
## Model portfolio  (models to build and why they add diversity)
## Ensembling plan
## Hypothesis queue  (numbered; each item = ONE experiment, concrete, highest expected gain first; 8-12 items)
## Risks & traps  (leakage, shift, metric quirks)
## External data  (only if a list of permitted public datasets is given below: one line per dataset to use,
   formatted exactly `- dataset: owner/slug` plus how to use it; write "none" otherwise)
## Kaggle sources  (only for notebook/GPU competitions: Kaggle Datasets/Models to attach for offline pretrained
   weights, one per line as `- model: owner/model/framework/variation` or `- dataset: owner/slug`; "none" otherwise)
## Research  (if you have web search: what winning solutions of THIS competition's discussions and of the most
   similar past Kaggle competitions did (1st-place write-ups, top public ideas), with links; use only public
   information and never private code sharing. Write "no web access" otherwise.)
Keep it under 1000 words. Be specific to THIS data, not generic."""

REFLECT = EXPERT + """

You are reviewing progress and REWRITING the strategy. Study the experiment log, the CV<->leaderboard pairs and the
rank. Decide what worked, what did not, whether CV is trustworthy (does it track the public LB?), and what gives the
biggest expected rank gain next. Write the full updated strategy with the same sections as before (Expert team ... External data, Research: keep
the research notes and add any new findings with links), plus a first section:
## Lessons learned  (bullets, each tied to evidence: experiment ids / scores)
Rewrite the Hypothesis queue from scratch: drop what failed, double down on what worked, include ensembling once
there are 3+ good diverse models. If progress has plateaued and you have web search, research what top solutions of
similar competitions did before rewriting the queue. Factor in the days left and the daily submission quota.
End with:
## Transferable lessons  (2-5 bullets of GENERAL lessons that would help on future, different competitions, e.g.
   about validation, leakage, tooling, data types; not specific to this dataset)
Keep it under 1100 words."""


# Competition archetypes: what top finishers actually do. Injected into the strategist prompt.
PLAYBOOKS = {
    "playground": """Kaggle Playground (synthetic tabular generated from an original public dataset). The top-10% band is
usually within 0.001-0.003 of the leader, so every 0.0005 matters. Winning recipe: append the ORIGINAL dataset to train
(dedupe, add an is_original flag, check its label mapping), 5-10 fold stratified CV on the synthetic rows only,
strong LightGBM + XGBoost + CatBoost tuned carefully (low learning rate, many rounds, early stopping on inner splits),
treat low-cardinality numerics as categorical too, target/count encodings inside folds, 3+ seeds per model, then a
hill-climbing or ridge/logistic stacker on 5-20 diverse OOF prediction sets. Public LB is a small sample: trust CV.""",
    "timeseries": """Forecasting. Validation must replicate the forecast horizon and gap (forward chaining). Only features
available at prediction time (lags >= horizon). Global GBDT on lag/rolling/calendar features + per-group models +
seasonal-naive anchors; predict in the metric's space (log1p for RMSLE); blend on time-based OOF.""",
    "tabular": """Real-world tabular. Understand the data-generating process and leakage first. GBDTs with domain features,
careful categorical handling, group-aware CV when entities repeat, adversarial validation for shift, ensembles of
diverse models stacked on OOF.""",
    "vision": """Computer vision. Runs as a Kaggle GPU notebook with internet OFF: pretrained weights only from Kaggle
Models/Datasets attached under /kaggle/input (list them in "## Kaggle sources"). Strong recipe: a pretrained backbone
(timm/torchvision convnext/efficientnet/vit) fine-tuned with mixed precision, stratified/grouped K-fold, standard
augmentations, cosine LR, TTA (flips), then an ensemble of 2-3 backbones/seeds. Respect the notebook time limit:
start with a small image size and 1-2 folds to validate the pipeline, scale up only when it works.""",
    "nlp": """NLP. Runs as a Kaggle GPU notebook with internet OFF: pretrained transformers only from attached Kaggle
Models/Datasets (list them in "## Kaggle sources"). Strong recipe: a DeBERTa-v3/RoBERTa-class model fine-tuned with
K-fold, mixed precision, layer-wise LR decay, max length tuned to the data; ensembles of seeds/backbones; a TF-IDF +
linear model as a fast, diverse baseline.""",
    "code": """Code competition: the submission is a Kaggle notebook that is RE-RUN on hidden test data at scoring time.
The script must read the test files at runtime from PODIUM_TASK, finish within the notebook time limit, run offline,
and write submission.csv. Keep inference fast and robust; train in the same notebook or load weights from an attached
dataset.""",
    "getting_started": """Educational competition; public LB is often polluted by leaked perfect scores. Optimise honest CV,
keep models simple and robust, and do not chase the public board.""",
}


def playbook(category, kind, profile_md):
    if kind == "cv":
        return PLAYBOOKS["vision"] + "\n" + PLAYBOOKS["code"]
    if kind == "nlp":
        return PLAYBOOKS["nlp"] + "\n" + PLAYBOOKS["code"]
    if kind == "code":
        return PLAYBOOKS["code"]
    if category == "Playground":
        return PLAYBOOKS["playground"]
    if "TEST IS AFTER TRAIN" in (profile_md or ""):
        return PLAYBOOKS["timeseries"]
    if category == "Getting Started":
        return PLAYBOOKS["getting_started"] + "\n" + PLAYBOOKS["tabular"]
    return PLAYBOOKS["tabular"]


def profile(task, out_dir, c=None):
    """Deterministic data profile, run once per competition (in the sandbox or as a Kaggle notebook)."""
    if c is not None and executor_for(c)[0] == "kaggle":
        from . import kaggle_runner
        kaggle_runner.run(PROFILE, c["slug"], out_dir, "profile", gpu=False)
    else:
        execute(PROFILE, task["dir"], out_dir)
    f = out_dir / "profile.md"
    return f.read_text() if f.exists() else "(profile failed: see log.txt)"


def strategy(task, profile_md, previous=None, evidence=None, book="", external=""):
    """Strategist (first plan) or reflector (rewrite from evidence). Returns (markdown, tokens, cost)."""
    ctx = (f"{task['description']}\n\n# Data profile\n{profile_md}\n\n# Playbook for this kind of competition\n{book}"
           + (f"\n\n# Permitted public datasets (competition allows external data)\n{external}" if external else ""))
    if task.get("memory"):
        ctx += f"\n\n# Fleet memory (lessons from earlier competitions)\n{task['memory']}"
    tools = ["WebSearch", "WebFetch"] if config.RESEARCH else None
    if previous is None:
        return llm.complete(STRATEGY, ctx, role="strategy", agent="Strategist", competition=task["slug"],
                            purpose="strategy", tools=tools)
    return llm.complete(REFLECT, f"{ctx}\n\n# Current strategy\n{previous}\n\n# Evidence\n{evidence}", role="strategy",
                        agent="Strategist", competition=task["slug"], purpose="reflection", tools=tools)
