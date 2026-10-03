"""SQLite state. Same tables as the spec; events is the append-only trace."""
import json
import smtplib
import sqlite3
import threading
import time
import urllib.request
import uuid
from email.message import EmailMessage
from email.utils import formataddr
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS competitions (
  slug TEXT PRIMARY KEY, title TEXT, metric TEXT, kind TEXT, deadline TEXT,
  state TEXT DEFAULT 'scouted', interest_score REAL DEFAULT 0, rules_accepted INTEGER DEFAULT 0,
  higher_is_better INTEGER DEFAULT 1, max_daily_submissions INTEGER DEFAULT 5, team_count INTEGER,
  cap_usd REAL, cap_hours REAL, paused INTEGER DEFAULT 0, note TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, competition_slug TEXT REFERENCES competitions(slug), engine TEXT,
  status TEXT, budget_usd REAL, budget_gpu_h REAL, started_at TEXT);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), parent_id TEXT, summary TEXT,
  cv_mean REAL, cv_std REAL, code_uri TEXT, oof_uri TEXT, sub_uri TEXT, critic_flags TEXT DEFAULT '[]',
  seconds REAL DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY, experiment_id TEXT REFERENCES experiments(id), competition_slug TEXT,
  kaggle_ref TEXT, lb_public REAL, lb_private REAL, reason TEXT, is_final_pick INTEGER DEFAULT 0,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, competition TEXT, agent TEXT, type TEXT,
  payload TEXT, tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, ts TEXT);
CREATE TABLE IF NOT EXISTS human_tasks (
  id TEXT PRIMARY KEY, competition_slug TEXT, kind TEXT, url TEXT, detail TEXT,
  status TEXT DEFAULT 'open', created_at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS copilot_convs (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS copilot_msgs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, role TEXT, content TEXT, attachments TEXT DEFAULT '[]',
  steps TEXT DEFAULT '[]', suggestions TEXT DEFAULT '[]', status TEXT DEFAULT 'done', run_id TEXT, error TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, competition TEXT, purpose TEXT, backend TEXT, model TEXT,
  billing TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0, seconds REAL DEFAULT 0, ok INTEGER DEFAULT 1, error TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


MIGRATIONS = ["ALTER TABLE competitions ADD COLUMN why TEXT", "ALTER TABLE competitions ADD COLUMN data_bytes INTEGER",
              "ALTER TABLE competitions ADD COLUMN lb_top REAL", "ALTER TABLE competitions ADD COLUMN lb_p10 REAL",
              "ALTER TABLE competitions ADD COLUMN lb_rank INTEGER", "ALTER TABLE competitions ADD COLUMN lb_teams INTEGER",
              "ALTER TABLE competitions ADD COLUMN scored_at TEXT", "ALTER TABLE competitions ADD COLUMN rank_at TEXT",
              "ALTER TABLE competitions ADD COLUMN category TEXT",
              "ALTER TABLE competitions ADD COLUMN opt_in INTEGER DEFAULT 0", "ALTER TABLE experiments ADD COLUMN review TEXT",
              "ALTER TABLE experiments ADD COLUMN cv_scheme TEXT",
              "ALTER TABLE competitions ADD COLUMN practice INTEGER DEFAULT 0"]


def init():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        for m in MIGRATIONS:  # ponytail: additive column migrations only; use a real tool if schema churn grows
            try:
                c.execute(m)
            except sqlite3.OperationalError:
                pass  # column exists


def q(sql, *args):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def one(sql, *args):
    rows = q(sql, *args)
    return rows[0] if rows else None


def x(sql, *args):
    with conn() as c:
        c.execute(sql, args)


# Event types that reach you by email / webhook. Noisy ones are throttled per competition.
NOTIFY_TYPES = {"plan.hold", "submission.scored", "rank.updated", "human_task.opened", "agent.error", "cv_lb.diverged",
                "competition.unblocked", "competition.recommended", "strategy.updated", "competition.joined",
                "project.bootstrapped"}
THROTTLED = {"agent.error": 3600, "cv_lb.diverged": 6 * 3600, "strategy.updated": 3600}
_last_sent = {}


def describe(type, payload, competition):
    """One human sentence per event, used in emails, webhooks and the dashboard."""
    p, c = payload or {}, competition or "fleet"
    return {
        "submission.scored": lambda: f"{c}: submission scored {p.get('lb_public')} on the public leaderboard.",
        "rank.updated": lambda: f"{c}: rank #{p.get('rank')}/{p.get('teams')} (top {p.get('top_pct')}%)"
                                + (f", was #{p['previous']}." if p.get("previous") else "."),
        "human_task.opened": lambda: f"{c}: action needed. {p.get('detail', '')}",
        "competition.recommended": lambda: f"New competition worth joining: {p.get('title')} "
                                           f"(chance {p.get('chance')}/100). Join: {p.get('url')}",
        "competition.unblocked": lambda: f"{c}: joined and data downloaded. The fleet is now working on it.",
        "agent.error": lambda: f"{c}: agent error: {str(p.get('error'))[:300]}",
        "cv_lb.diverged": lambda: f"{c}: CV and public leaderboard disagree by {p.get('gap')}. Strategy will adapt.",
        "strategy.updated": lambda: f"{c}: strategy v{p.get('version')} written. {str(p.get('headline', ''))[:300]}",
        "submission.sent": lambda: f"{c}: submitted {p.get('experiment_id')} (CV {p.get('cv'):.5f}). {p.get('reason')}",
        "experiment.started": lambda: f"{c}: experiment {p.get('experiment_id')} started: {str(p.get('summary'))[:200]}",
        "experiment.finished": lambda: f"{c}: experiment {p.get('experiment_id')} finished, CV "
                                       + (f"{p['cv_mean']:.5f} ± {p['cv_std']:.5f}" if p.get("cv_mean") is not None
                                          else "failed") + f" in {p.get('seconds')}s.",
        "experiment.flagged": lambda: f"{c}: experiment {p.get('experiment_id')} rejected: {', '.join(p.get('flags', []))[:300]}",
        "experiment.reviewed": lambda: f"{c}: code review {p.get('verdict', '').upper()} for {p.get('experiment_id')}"
                                       + (f": {'; '.join(p.get('issues', []))[:300]}" if p.get("issues") else "."),
        "profile.done": lambda: f"{c}: data profiled.",
        "scout.done": lambda: f"Scout listed {p.get('listed')} live competitions, {p.get('new')} new.",
        "competition.found": lambda: f"{c}: discovered (chance {p.get('chance')}/100).",
        "run.done": lambda: f"{c}: run finished ({json.dumps(p)[:120]}).",
        "copilot.answered": lambda: f"Copilot answered: {str(p.get('question'))[:120]}"
                                    + (f" (used {', '.join(p['tools'])})" if p.get("tools") else ""),
        "experiment.cancelled": lambda: f"{c}: running experiment {p.get('experiment_id')} cancelled (paused or stopped by you).",
        "experiment.resumed": lambda: f"{c}: experiment {p.get('experiment_id')} resumed after a restart (no new AI call).",
        "backup.done": lambda: f"Nightly backup written: {p.get('file')}.",
        "plan.hold": lambda: (f"Claude plan: fleet AI work paused to keep your {int(100 * (p.get('reserve') or 0))}% reserve "
                              f"(week {int(100 * (p.get('seven_day') or 0))}% used, 5-hour {int(100 * (p.get('five_hour') or 0))}%). "
                              "It resumes automatically when the window resets."),
        "plan.resumed": lambda: "Claude plan: allowance available again, the fleet resumed its AI work.",
        "project.bootstrapped": lambda: f"{c}: project ready: official pages ({p.get('pages')}), "
                                        f"{p.get('notebooks')} top public notebooks and an expert PLAN.md with a timeline.",
        "project.baseline": lambda: (f"{c}: baseline launch failed: {p['error']}" if p.get("error") else
                                     f"{c}: forked public notebook {p.get('from')} privately as {p.get('run')}; it runs on Kaggle "
                                     "and is submitted when it finishes."),
        "competition.practice": lambda: f"{c}: practice mode on (late submissions: scored, not ranked).",
        "strategy.stagnation": lambda: f"{c}: {p.get('since_best')} experiments without a new best: emergency reflection with research.",
        "competition.added": lambda: f"{c}: added by you" + (" (already ended)" if p.get("ended") else "") + ".",
        "competition.joined": lambda: f"{c}: you joined {p.get('title', '')} on Kaggle. The fleet is taking it on.",
        "competition.project": lambda: f"{c}: joined; handled as a dedicated project (outside the fleet).",
        "competition.stopped": lambda: f"{c}: stopped by you. It will not restart until you resume it.",
        "competition.archived": lambda: f"{c}: archived (hidden from lists; files and history kept).",
        "competition.resumed": lambda: f"{c}: resumed; the Gatekeeper restarts it within a minute.",
        "fleet.started": lambda: f"Fleet started: model {p.get('model')}, engine {p.get('engine')}, sandbox {p.get('sandbox')}.",
    }.get(type, lambda: f"{c}: {type} {json.dumps(p)[:300]}")()


def emit(agent, type, payload=None, run_id=None, competition=None, tokens=0, cost_usd=0.0):
    x("INSERT INTO events (run_id, competition, agent, type, payload, tokens, cost_usd, ts) VALUES (?,?,?,?,?,?,?,?)",
      run_id, competition, agent, type, json.dumps(payload or {}), tokens, cost_usd, now())
    if type in NOTIFY_TYPES and (config.NOTIFY_URL or config.NOTIFY_EMAIL):
        key = (type, competition)
        if type in THROTTLED and time.time() - _last_sent.get(key, 0) < THROTTLED[type]:
            return
        _last_sent[key] = time.time()
        text = describe(type, payload, competition)
        threading.Thread(target=notify, args=(f"[Podium] {text[:120]}", text), daemon=True).start()


def notify(subject, text):
    """Best effort: email (SMTP_* + PODIUM_NOTIFY_EMAIL) and/or webhook (ntfy / Slack / Discord). Returns errors."""
    errors = []
    if config.NOTIFY_EMAIL and config.SMTP_HOST:
        try:
            send_email(subject, text)
        except Exception as e:
            errors.append(f"email: {e}")
    if config.NOTIFY_URL:
        try:
            if "ntfy" in config.NOTIFY_URL:
                req = urllib.request.Request(config.NOTIFY_URL, data=text.encode(), headers={"Title": subject[:200]})
            else:
                req = urllib.request.Request(config.NOTIFY_URL, data=json.dumps({"text": text, "content": text}).encode(),
                                             headers={"content-type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            errors.append(f"webhook: {e}")
    return errors


def send_email(subject, text):
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, config.NOTIFY_EMAIL
    msg["From"] = formataddr((config.FROM_NAME, config.FROM_EMAIL))
    link = f"\n\nOpen Podium: {config.PUBLIC_URL}" if config.PUBLIC_URL else ""
    msg.set_content(f"{text}{link}\n\n-- Podium, your autonomous Kaggle fleet")
    port = int(config.SMTP_PORT)
    cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with cls(config.SMTP_HOST, port, timeout=20) as s:
        if port != 465:
            s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)


def setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", key)
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    x("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      key, json.dumps(value))


def open_task(slug, kind, url, detail):
    """One open task per (competition, kind) — no inbox spam."""
    if not one("SELECT 1 FROM human_tasks WHERE competition_slug=? AND kind=? AND status='open'", slug, kind):
        x("INSERT INTO human_tasks (id, competition_slug, kind, url, detail, created_at) VALUES (?,?,?,?,?,?)",
          new_id("h"), slug, kind, url, detail, now())
        emit("Gatekeeper", "human_task.opened", {"kind": kind, "detail": detail}, competition=slug)
