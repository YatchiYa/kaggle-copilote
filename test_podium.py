"""Offline check: baseline engine -> sandbox -> critic on synthetic data. No Kaggle, no LLM.
Run: .venv/bin/python test_podium.py   (uses PODIUM_SANDBOX from .env; docker needs the podium-sandbox image)"""
import os
import tempfile
from pathlib import Path

os.environ["PODIUM_DATA_DIR"] = tempfile.mkdtemp()  # never touch the real state db

import numpy as np
import pandas as pd

from podium import agents, db, engines


def make_task(tmp, kind):
    rng = np.random.default_rng(0)
    n, d = 2000, Path(tmp) / kind
    d.mkdir()
    X = pd.DataFrame({"id": range(n), "a": rng.normal(size=n), "b": rng.normal(size=n),
                      "c": rng.choice(["x", "y", "z"], n)})
    signal = X.a * 2 + (X.c == "x") + rng.normal(scale=.5, size=n)
    X["target"] = (signal > 0).astype(int) if kind == "auc" else signal * 10 + 100
    tr, te = X.iloc[:1500], X.iloc[1500:].drop(columns="target")
    tr.to_csv(d / "train.csv", index=False)
    te.to_csv(d / "test.csv", index=False)
    pd.DataFrame({"id": te.id, "target": 0.5}).to_csv(d / "sample_submission.csv", index=False)
    metric = "ROC AUC" if kind == "auc" else "RMSE"
    return {"slug": kind, "metric": metric, "higher_is_better": kind == "auc", "dir": d, "description": ""}


with tempfile.TemporaryDirectory() as tmp:
    for kind, check in (("auc", lambda cv: 0.85 < cv <= 1), ("rmse", lambda cv: 0 < cv < 15)):
        task = make_task(tmp, kind)
        history = []
        for step in range(2):
            prop = engines.baseline(task, history)
            out = Path(tmp) / f"{kind}_{step}"
            cv, std, log, secs = engines.execute(prop["code"], task["dir"], out)
            flags = agents.critic(out, task, cv)
            print(f"{kind} step {step}: {prop['summary']} cv={cv} std={std} flags={flags} {secs:.1f}s")
            assert cv is not None and check(cv), log[-2000:]
            assert flags == [], flags
            history.append({"id": f"e{step}"})
        # Critic catches broken submissions.
        sub = pd.read_csv(out / "submission.csv")
        if kind == "auc":
            sub.assign(target=sub.target * 3).to_csv(out / "submission.csv", index=False)
            assert any(f.startswith("probabilities_out_of_range") for f in agents.critic(out, task, cv))
        sub.assign(id=sub.id + 10**6).to_csv(out / "submission.csv", index=False)
        assert "ids_do_not_match_template" in agents.critic(out, task, cv)
        (out / "submission.csv").write_text("id,target\n1,0.5\n")
        assert "bad_submission_format" in agents.critic(out, task, cv)

# Submitter: submits the best CV, then spends quota again only when the gain beats fold noise.
class FakeKaggle:
    sent = []
    def competition_submit(self, path, msg, slug, quiet=True):
        self.sent.append(msg)
    def competition_submissions(self, slug):
        return []

agents._api = FakeKaggle()
db.init()
db.x("INSERT INTO competitions (slug,title,metric,kind,deadline,state,higher_is_better,max_daily_submissions) "
     "VALUES ('c','C','AUC','tabular','2099-01-01T00:00:00+00:00','active',1,5)")
db.x("INSERT INTO runs (id,competition_slug,engine,status,budget_usd,budget_gpu_h,started_at) VALUES ('r','c','baseline','running',1,1,'')")
def exp(i, cv, std=0.002):
    db.x("INSERT INTO experiments (id,run_id,summary,cv_mean,cv_std,sub_uri,created_at) VALUES (?,?,?,?,?,?,?)",
         i, "r", i, cv, std, "/dev/null", db.now())
exp("e1", 0.90); exp("e2", 0.91)
agents.submitter(); assert FakeKaggle.sent[-1].startswith("podium e2"), FakeKaggle.sent
exp("e3", 0.911)           # within noise, spare quota (1 < 5//2) -> LB probe
agents.submitter(); assert len(FakeKaggle.sent) == 2 and "e3" in FakeKaggle.sent[-1], FakeKaggle.sent
exp("e4", 0.912)           # within noise, but probes already used half the quota -> skip
agents.submitter(); assert len(FakeKaggle.sent) == 2, FakeKaggle.sent
exp("e5", 0.93)            # gain 0.019 > noise -> submit
agents.submitter(); assert len(FakeKaggle.sent) == 3 and "e5" in FakeKaggle.sent[-1], FakeKaggle.sent
assert {s["experiment_id"] for s in db.q("SELECT * FROM submissions WHERE is_final_pick=1")} <= {"e2", "e3", "e5"}

# Review gate: an LLM-engine candidate that fails review is flagged and not submitted.
from podium import config, llm
(agents.comp_dir("c") / "data").mkdir(parents=True, exist_ok=True)
config.MODEL, config.REVIEW = "claude-code", True
db.x("INSERT INTO runs (id,competition_slug,engine,status,budget_usd,budget_gpu_h,started_at) VALUES ('r2','c','llm','running',1,1,'')")
code = Path(tempfile.mkdtemp()) / "main.py"; code.write_text("print('x')")
db.x("INSERT INTO experiments (id,run_id,summary,cv_mean,cv_std,sub_uri,code_uri,created_at) VALUES "
     "('e6','r2','leaky',0.99,0.001,'/dev/null',?,?)", str(code), db.now())
db.x("UPDATE submissions SET created_at='2000-01-01'")  # fresh daily quota for this check
llm.complete = lambda *a, **k: ('{"verdict": "fail", "issues": ["target encoding fitted on full train"]}', 10, 0.0)
agents.submitter()
assert "e6" not in FakeKaggle.sent[-1], FakeKaggle.sent
e6 = db.one("SELECT * FROM experiments WHERE id='e6'"); assert "review_failed" in e6["critic_flags"], e6

# Validation scheme change: an honest new scheme is submitted even though its CV looks worse.
config.REVIEW = False
db.x("UPDATE submissions SET created_at='2000-01-01'")
db.x("INSERT INTO experiments (id,run_id,summary,cv_mean,cv_std,sub_uri,created_at,cv_scheme,critic_flags) VALUES "
     "('e7','r','honest fwd cv',0.80,0.002,'/dev/null','2999-01-01','fwd4','[]')")
agents.submitter()
assert "e7" in FakeKaggle.sent[-1], FakeKaggle.sent
assert "validation scheme 'fwd4'" in db.one("SELECT reason FROM submissions WHERE experiment_id='e7'")["reason"]

# Rank: our best public score placed on the leaderboard.
db.x("UPDATE submissions SET lb_public=0.95 WHERE experiment_id='e5'")
agents.leaderboard = lambda slug, max_age_s=0, hib=True: [0.99, 0.97, 0.96, 0.95, 0.94, 0.90]
agents.update_rank(db.one("SELECT * FROM competitions WHERE slug='c'"))
c = db.one("SELECT * FROM competitions WHERE slug='c'"); assert (c["lb_rank"], c["lb_teams"]) == (4, 6), c

# Manual join on kaggle.com: unseen competition is added, opted in, announced, and (agent kind) becomes a project.
from datetime import datetime, timedelta
class Comp: pass
g = Comp()
g.ref, g.title, g.evaluation_metric, g.category = "https://www.kaggle.com/competitions/agent-x", "Agent X", "", "Featured"
g.deadline, g.user_has_entered, g.max_daily_submissions, g.team_count = datetime.utcnow() + timedelta(days=30), True, 1, 10
g.is_kernels_submissions_only, g.tags, g.submissions_disabled, g.reward = False, [], False, "1,000 Usd"
class Listing: competitions = [g]
FakeKaggle.competitions_list = lambda self, **kw: Listing() if kw.get("group") == "entered" else Listing()
FakeKaggle.competition_list_files = lambda self, *a, **k: type("F", (), {"files": []})()
agents.gatekeeper()
x = db.one("SELECT * FROM competitions WHERE slug='agent-x'")
assert x and x["rules_accepted"] == 1 and x["opt_in"] == 1 and x["state"] == "project", x
assert db.one("SELECT 1 FROM events WHERE type='competition.joined' AND competition='agent-x'")
# Stop sticks: a stopped competition is never restarted by the Gatekeeper.
from podium import api as _api
_api.stop("c"); agents.gatekeeper()
assert db.one("SELECT state FROM competitions WHERE slug='c'")["state"] == "stopped"

# Tree search: UCB picks a less-explored good branch; every 6th is an exploration draft; deadline -> final round.
ok_nodes = [{"id": "a", "cv_mean": 0.80, "summary": "x", "parent_id": None},
            {"id": "b", "cv_mean": 0.82, "summary": "y", "parent_id": "a"},
            {"id": "c", "cv_mean": 0.81, "summary": "z", "parent_id": "a"}]
hist = ok_nodes + [{"id": "d", "cv_mean": 0.805, "summary": "w", "parent_id": "b"}]
t = {"higher_is_better": True, "days_left": 20}
assert engines.select_parent(ok_nodes, hist, t) == (ok_nodes[1], "improve")  # best, few children
hist_b = hist + [{"id": f"k{i}", "cv_mean": None, "summary": "", "parent_id": "b"} for i in range(4)]
assert engines.select_parent(ok_nodes, hist_b, t)[0]["id"] == "c"  # 'b' over-explored -> sibling branch
assert engines.select_parent(ok_nodes, hist + [{"id": "e", "cv_mean": None, "summary": "", "parent_id": "b"}], t)[1] == "draft"
assert engines.select_parent(ok_nodes, hist, {"higher_is_better": True, "days_left": 2})[1] == "final"

# Rank chance: a small tabular playground beats a leaked getting-started board.
class C: pass
def comp(cat):
    x = C(); x.category, x.max_daily_submissions = cat, 5; return x
good, _ = agents.chance(comp("Playground"), "tabular", 25, 50e6, [0.96] * 800, 1, "ROC AUC")
bad, why = agents.chance(comp("Getting Started"), "tabular", 400, 1e6, [1.0] * 15000, 1, "Categorization Accuracy")
assert good >= 80 and bad < 45, (good, bad, why)
assert agents.chance(comp("Playground"), "cv", 25, 50e6, [], 1, "AUC")[0] == 0
print("ok")
