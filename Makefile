# Podium: one-command operations.  `make up` = install/repair everything, then start in the background.
PY      := .venv/bin/python
PIDFILE := data/podium.pid
LOG     := data/server.log
PORT    := $(shell grep -E '^PODIUM_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' "' || true)
URL     := http://127.0.0.1:$(or $(PORT),8000)

.PHONY: help setup up run down restart status logs check test e2e service unservice backup restore clean
help:            ## list commands
	@grep -E '^[a-z]+:.*## ' Makefile | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup:           ## install / repair: Python env, .env, Claude Code, sandbox image, health check
	@./setup.sh

up: setup        ## setup + start in the background (idempotent)
	@mkdir -p data
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then echo "Podium already running (pid $$(cat $(PIDFILE))) -> $(URL)"; \
	else nohup $(PY) -m podium >> $(LOG) 2>&1 & echo $$! > $(PIDFILE); sleep 3; echo "Podium started (pid $$(cat $(PIDFILE))) -> $(URL)"; fi

run:             ## start in the foreground (Ctrl+C to stop)
	@$(PY) -m podium

down:            ## stop Podium and cancel running experiment containers
	@if [ -f $(PIDFILE) ]; then kill $$(cat $(PIDFILE)) 2>/dev/null || true; rm -f $(PIDFILE); fi
	@for p in $$(pgrep -f '^[^ ]*python[^ ]* -m podium' 2>/dev/null); do \
	  [ "$$(readlink /proc/$$p/cwd 2>/dev/null)" = "$(CURDIR)" ] && kill $$p 2>/dev/null || true; done
	@docker ps --format '{{.ID}} {{.Names}}' --filter ancestor=podium-sandbox | awk '$$2 ~ /^podium-/ {print $$1}' | \
	  xargs -r docker kill >/dev/null 2>&1 || true
	@echo "Podium stopped"

restart: down    ## restart (keeps all data and conversations)
	@$(MAKE) --no-print-directory up

status:          ## is it running? + quick fleet summary
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then echo "running (pid $$(cat $(PIDFILE))) -> $(URL)"; else echo "not running"; fi
	@curl -fs $(URL)/api/fleet 2>/dev/null | $(PY) -c "import json,sys;d=json.load(sys.stdin);print('KPIs:',d['kpis'])" || true

logs:            ## follow the server log
	@tail -f $(LOG)

check:           ## verify Kaggle auth, AI engine and sandbox
	@$(PY) -m podium check

test:            ## offline test suite (engine, sandbox, critic, review gate, quotas, joins, stop)
	@$(PY) test_podium.py && $(PY) -m podium.copilot

e2e:             ## end-to-end UI test against the running dashboard (installs a headless browser once)
	@uv run -q --with playwright playwright install chromium >/dev/null 2>&1 || true
	@uv run -q --with playwright python tests/e2e_ui.py $(URL)

service:         ## auto-start at login and restart on crash (systemd --user, Linux)
	@mkdir -p ~/.config/systemd/user
	@printf '[Unit]\nDescription=Podium autonomous Kaggle fleet\nAfter=network-online.target docker.service\n\n[Service]\nWorkingDirectory=%s\nExecStart=%s/.venv/bin/python -m podium\nRestart=always\nRestartSec=10\nEnvironment=PATH=%s/.local/bin:/usr/local/bin:/usr/bin:/bin\n\n[Install]\nWantedBy=default.target\n' "$(CURDIR)" "$(CURDIR)" "$(HOME)" > ~/.config/systemd/user/podium.service
	@systemctl --user daemon-reload && systemctl --user enable --now podium && echo "systemd service 'podium' enabled -> $(URL)"

unservice:       ## remove the systemd service
	@systemctl --user disable --now podium 2>/dev/null || true; rm -f ~/.config/systemd/user/podium.service; systemctl --user daemon-reload

backup:          ## archive state (db, strategies, fleet memory, experiments metadata) to backups/
	@mkdir -p backups && tar czf backups/podium-$$(date +%Y%m%d-%H%M).tgz --exclude='data/competitions/*/data' --exclude='data/competitions/*/external' data projects/*/PLAN.md projects/*/paper && ls -t backups | head -1

restore:         ## restore the newest backup (make restore FILE=backups/xxx.tgz for a specific one)
	@tar xzf $(or $(FILE),$$(ls -t backups/*.tgz | head -1)) && echo "restored"

clean:           ## remove the Python env (data is kept)
	@rm -rf .venv
