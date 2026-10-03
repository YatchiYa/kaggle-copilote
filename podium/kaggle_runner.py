"""Run an experiment script as a private Kaggle notebook (free CPU/GPU, competition data attached, internet off).

Same contract as the Docker sandbox: the script reads PODIUM_TASK, writes PODIUM_OUT/submission.csv and prints
PODIUM_RESULT {...}. Used for code-only competitions (they must be submitted from a notebook) and for deep-learning
competitions that need a GPU. Returns the same tuple as engines.execute().
"""
import hashlib
import json
import re
import time
from pathlib import Path

from . import config

HEADER = '''# --- Podium header: map the Podium contract onto Kaggle's notebook filesystem ---
import os, glob
_slug = {slug!r}
_cands = [p for p in [f"/kaggle/input/{{_slug}}", f"/kaggle/input/competitions/{{_slug}}"] if os.path.isdir(p)]
_cands += [os.path.dirname(p) for p in glob.glob("/kaggle/input/**/*.csv", recursive=True)][:1]
os.environ["PODIUM_TASK"] = _cands[0] if _cands else "/kaggle/input"
os.environ["PODIUM_OUT"] = "/kaggle/working"
os.environ["PODIUM_WORK"] = "/kaggle/working/work"; os.makedirs("/kaggle/working/work", exist_ok=True)
os.environ.setdefault("PODIUM_CPUS", str(os.cpu_count() or 2)); os.environ.setdefault("PODIUM_PREV", "")
os.environ.setdefault("PODIUM_EXTERNAL", "")
# --- end of Podium header ---
'''
DONE = {"complete", "error", "cancelacknowledged", "cancelrequested"}


def kernel_ref(slug, exp_id):
    from .agents import kaggle
    user = kaggle().get_config_value("username")
    short = hashlib.sha1(slug.encode()).hexdigest()[:6]
    return user, f"podium-{short}-{exp_id.replace('_', '-')}"[:50]


def run(code, slug, exp_dir, exp_id, gpu=False, timeout_s=None, datasets=(), models=()):
    """Push the script as a private notebook, wait for it, download its outputs into exp_dir."""
    from .agents import kaggle
    timeout_s = timeout_s or (config.NOTEBOOK_TIMEOUT_S if gpu else config.EXPERIMENT_TIMEOUT_S)
    exp_dir.mkdir(parents=True, exist_ok=True)
    folder = exp_dir / "kernel"
    folder.mkdir(exist_ok=True)
    user, name = kernel_ref(slug, exp_id)
    (folder / "main.py").write_text(HEADER.format(slug=slug) + code)
    (folder / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{user}/{name}", "title": name, "code_file": "main.py", "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": bool(gpu), "enable_internet": False, "competition_sources": [slug],
        "dataset_sources": list(datasets), "kernel_sources": [], "model_sources": list(models)}, indent=2))
    (exp_dir / "main.py").write_text(code)
    t0 = time.time()
    resp = kaggle().kernels_push(str(folder), acc=config.KAGGLE_ACCELERATOR if gpu else None)
    if getattr(resp, "error", None):
        raise RuntimeError(f"Kaggle notebook push failed: {resp.error}")
    version = getattr(resp, "version_number", None)
    ref = f"{user}/{name}"
    (exp_dir / "kernel.json").write_text(json.dumps({"ref": ref, "version": version, "gpu": bool(gpu)}))
    status = "queued"
    while time.time() - t0 < timeout_s + 900:  # + queueing time
        time.sleep(30)
        try:
            st = kaggle().kernels_status(ref)
            status = str(getattr(st, "status", st)).split(".")[-1].lower()
        except Exception:
            continue
        if status in DONE:
            break
    out = exp_dir / "kaggle_output"
    out.mkdir(exist_ok=True)
    try:
        kaggle().kernels_output(ref, str(out), force=True)
    except Exception as e:
        (exp_dir / "log.txt").write_text(f"Kaggle notebook {ref} ended with status {status}; output download failed: {e}")
        return None, None, (exp_dir / "log.txt").read_text(), time.time() - t0
    for f in out.rglob("*"):  # move contract files next to the experiment, like the Docker sandbox does
        if f.is_file() and f.name in ("submission.csv", "oof.csv", "diagnostics.md", "result.json", "profile.md"):
            f.replace(exp_dir / f.name)
    logs = [p for p in out.rglob("*.log")]
    log = "\n".join(_log_text(p) for p in logs) or f"(no log; status {status})"
    log = f"[kaggle notebook {ref} v{version}, status {status}]\n" + log
    (exp_dir / "log.txt").write_text(log[-60000:])
    res = re.findall(r"PODIUM_RESULT (\{.*?\})", log)
    if not res or status != "complete":
        return None, None, log, time.time() - t0
    r = json.loads(res[-1])
    (exp_dir / "result.json").write_text(json.dumps(r))
    return float(r["cv_mean"]), float(r.get("cv_std") or 0.0), log, time.time() - t0


def _log_text(p):
    """Kaggle logs are JSON lists of {stream_name, data}; fall back to raw text."""
    raw = Path(p).read_text(errors="replace")
    try:
        return "".join(x.get("data", "") for x in json.loads(raw))
    except Exception:
        return raw


def submit(slug, exp_dir, message):
    """Code competitions: submit the notebook version that produced this experiment's submission.csv."""
    from .agents import kaggle
    k = json.loads((exp_dir / "kernel.json").read_text())
    return kaggle().competition_submit_code("submission.csv", message, slug, kernel=k["ref"], kernel_version=k["version"],
                                            quiet=True)
