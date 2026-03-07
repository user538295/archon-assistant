PLIST_LABEL  = com.archon.assistant
PLIST_SRC    = scripts/$(PLIST_LABEL).plist
LAUNCH_AGENTS = $(HOME)/Library/LaunchAgents
DEST_PLIST   = $(LAUNCH_AGENTS)/$(PLIST_LABEL).plist
LOG_FILE     = $(HOME)/.archon/logs/archon.log
UV           = $(shell which uv)
DIR          = $(PWD)

SERVICE_NAME = archon
SERVICE_SRC  = scripts/archon.service
SYSTEMD_USER = $(HOME)/.config/systemd/user

.PHONY: install uninstall logs install-linux uninstall-linux lint-docs

install:
	@mkdir -p $(LAUNCH_AGENTS)
	@mkdir -p $(HOME)/.archon/scripts
	@cp scripts/health_check.sh scripts/qmd_checker.sh $(HOME)/.archon/scripts/
	@chmod +x $(HOME)/.archon/scripts/health_check.sh $(HOME)/.archon/scripts/qmd_checker.sh
	sed \
		-e 's|__ARCHON_DIR__|$(DIR)|g' \
		-e 's|__UV_PATH__|$(UV)|g' \
		-e 's|__LOG_FILE__|$(LOG_FILE)|g' \
		$(PLIST_SRC) > $(DEST_PLIST)
	launchctl load $(DEST_PLIST)
	@echo "Archon installed. Service will start automatically on login."

uninstall:
	-launchctl unload $(DEST_PLIST)
	-rm -f $(DEST_PLIST)
	@echo "Archon service uninstalled."

logs:
	tail -f $(LOG_FILE)

install-linux:
	@mkdir -p $(SYSTEMD_USER)
	@mkdir -p $(HOME)/.archon
	sed \
		-e 's|__ARCHON_DIR__|$(DIR)|g' \
		-e 's|__UV_PATH__|$(UV)|g' \
		-e 's|__LOG_FILE__|$(LOG_FILE)|g' \
		$(SERVICE_SRC) > $(SYSTEMD_USER)/$(SERVICE_NAME).service
	systemctl enable --user $(SERVICE_NAME)
	@echo "Archon installed as systemd user service."

uninstall-linux:
	-systemctl disable --user $(SERVICE_NAME)
	-rm -f $(SYSTEMD_USER)/$(SERVICE_NAME).service
	@echo "Archon systemd service uninstalled."

lint-docs:  ## Lint all Markdown documentation files
	markdownlint-cli2 "**/*.md" "#node_modules" "#.venv"
