"""python -m podium [serve|check|step] [--no-fleet]"""
import shutil
import subprocess
import sys

from . import config, db


def check():
    ok = True
    print(f"model={config.MODEL} engine={config.ENGINE} sandbox={config.SANDBOX} data={config.DATA_DIR}")
    try:
        from .agents import kaggle
        n = len(kaggle().competitions_list(sort_by="recentlyCreated").competitions)
        print(f"[ok]   Kaggle auth ({n} competitions listed)")
    except Exception as e:
        ok = False
        print(f"[fail] Kaggle auth: {e}")
    from . import llm
    if any(config.model_for(r).startswith("claude-code") for r in config.ROLES):
        auth = llm.claude_code_auth()
        if auth.get("loggedIn"):
            print(f"[ok]   Claude Code {config.CLAUDE_BIN}: {auth.get('email')} via {auth.get('authMethod')} "
                  f"{auth.get('subscriptionType') or ''}")
        else:
            ok = False
            print(f"[fail] Claude Code not logged in ({config.CLAUDE_BIN}): run `claude auth login`")
    for role in config.ROLES:
        m = config.model_for(role)
        if m == "none":
            print(f"[--]   {role:<10} none (baseline engine)")
            continue
        try:
            text, tokens, cost = llm.complete("Answer with one word.", "Say: ready", role=role, max_tokens=20,
                                              agent="Setup", purpose=f"health check ({role})")
            print(f"[ok]   {role:<10} {m}: {text.strip()[:20]!r}")
        except Exception as e:
            ok = False
            print(f"[fail] {role:<10} {m}: {str(e)[:200]}")
    if config.SANDBOX == "docker":
        if not shutil.which("docker"):
            ok = False
            print("[fail] docker not installed (or set PODIUM_SANDBOX=local on a trusted machine)")
        elif subprocess.run(["docker", "image", "inspect", config.SANDBOX_IMAGE], capture_output=True).returncode:
            ok = False
            print(f"[fail] sandbox image missing: docker build -t {config.SANDBOX_IMAGE} sandbox/")
        else:
            print(f"[ok]   sandbox image {config.SANDBOX_IMAGE}")
    return ok


def main():
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith("-") else "serve"
    db.init()
    if cmd == "check":
        sys.exit(0 if check() else 1)
    if cmd == "step":  # one synchronous pass of every agent, handy for debugging
        from . import agents
        for fn in (agents.scout, agents.gatekeeper, lambda: agents.solver(wait=True), agents.submitter):
            fn()
        return
    import uvicorn
    from .api import app
    app.state.fleet = config.FLEET and "--no-fleet" not in args
    if config.HOST not in ("127.0.0.1", "localhost") and not config.PASSWORD:
        sys.exit("Refusing to listen on the network without PODIUM_PASSWORD set in .env.")
    print(f"Podium on http://{config.HOST}:{config.PORT}  (fleet {'on' if app.state.fleet else 'off'})")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


main()
