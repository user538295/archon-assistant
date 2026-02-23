PLIST_LABEL  = com.archon.assistant
PLIST_SRC    = scripts/$(PLIST_LABEL).plist
LAUNCH_AGENTS = $(HOME)/Library/LaunchAgents
DEST_PLIST   = $(LAUNCH_AGENTS)/$(PLIST_LABEL).plist
LOG_FILE     = $(HOME)/.archon/archon.log
UV           = $(shell which uv)
DIR          = $(PWD)

.PHONY: install uninstall logs

install:
	@mkdir -p $(LAUNCH_AGENTS)
	@mkdir -p $(HOME)/.archon
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
