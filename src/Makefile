PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION := $(shell date +%Y%m%d%H%M)

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

UNIT_SRC = bed/daemon/bed.service
UNIT_DST = /usr/lib/systemd/system/bed.service

# BED_HOME: the bed account's home, and the parent of the default venv. Same variable as
# the top-level Makefile. bed reads nothing from /etc.
BED_HOME ?= /home/NEEDINFO
BED_VENV ?= $(BED_HOME)/venv

ZOID6_UNIT_SRC = bed/daemon/zoid6.service
ZOID6_UNIT_DST = /usr/lib/systemd/system/zoid6.service

SYSUSERS_SRC = bed/daemon/bed.sysusers
SYSUSERS_DST = /usr/lib/sysusers.d/bed.conf

TMPFILES_SRC = bed/daemon/bed.tmpfiles
TMPFILES_DST = /usr/lib/tmpfiles.d/bed.conf

.PHONY: install version
.PHONY: clean rename-sdist sign release all
.PHONY: install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles install-systemd uninstall-systemd install-zoid6-systemd uninstall-zoid6-systemd

install: version
	cd .. && $(PIP) install --no-cache-dir -e . && rm -rf src/bed.egg-info

rename-sdist:
	@for f in $(OUTDIR)/*.tar.gz; do \
		if [ -f "$$f" ] && echo "$$f" | grep -vq '\-src\.tar\.gz' ; then \
			mv "$$f" "$${f%.tar.gz}-src.tar.gz"; \
			echo "Renamed $$f -> $${f%.tar.gz}-src.tar.gz"; \
		fi \
	done

sign:
	@for f in $(OUTDIR)/*; do \
		if [ -f "$$f" ] && [ ! -f "$$f.asc" ] && [ "$${f##*.}" != "asc" ]; then \
			gpg --armor --detach-sign "$$f"; \
			echo "Signed $$f"; \
		fi \
	done

release:
	$(MAKE) -C .. release

all:

clean:
	-rm *~
	$(MAKE) -C bed clean

# TODO: standardize version label names across packages (drop __ prefix/suffix on __datestamp__/__version__)
install-sysusers:
	sed 's|@BED_HOME@|$(BED_HOME)|g' $(SYSUSERS_SRC) > /tmp/bed.sysusers.$$$$ && \
		install -m 0644 /tmp/bed.sysusers.$$$$ $(SYSUSERS_DST) && rm -f /tmp/bed.sysusers.$$$$
	systemd-sysusers
	@echo "Created bed user (home $(BED_HOME)) via $(SYSUSERS_DST)"

uninstall-sysusers:
	-rm -f $(SYSUSERS_DST)
	@echo "Removed $(SYSUSERS_DST)"

install-tmpfiles: install-sysusers
	sed 's|@BED_HOME@|$(BED_HOME)|g' $(TMPFILES_SRC) > /tmp/bed.tmpfiles.$$$$ && \
		install -m 0644 /tmp/bed.tmpfiles.$$$$ $(TMPFILES_DST) && rm -f /tmp/bed.tmpfiles.$$$$
	systemd-tmpfiles --create
	@echo "Created /var/log/bed and $(BED_HOME) via $(TMPFILES_DST)"

uninstall-tmpfiles:
	-rm -f $(TMPFILES_DST)
	@echo "Removed $(TMPFILES_DST)"

install-systemd:
	install -m 0644 $(UNIT_SRC) $(UNIT_DST)
	systemctl daemon-reload
	@echo "Installed $(UNIT_DST). Run: systemctl enable --now bed"

uninstall-systemd:
	-systemctl stop bed
	-systemctl disable bed
	-rm -f $(UNIT_DST)
	-systemctl daemon-reload
	@echo "Removed $(UNIT_DST)"

# The zoid6 unit runs bed (owned by the bed user, venv at $(BED_VENV)) with zoid6's
# router. zoid6 must be importable from that venv or the unit fails at start. zoid6's
# own install-venv target uses its own venv (owner zoid6) and does NOT satisfy this;
# install into the bed venv instead:
#   sudo -u bed $(BED_VENV)/bin/pip install ../../zoid6/src
# zoid6 reads its packaged zoid6.json, so nothing is copied to /etc.
install-zoid6-systemd:
	@if ! $(BED_VENV)/bin/python -c "import zoid6" 2>/dev/null; then \
		echo "ERROR: zoid6 is not installed in $(BED_VENV). Install it there first:" >&2; \
		echo "  sudo -u bed $(BED_VENV)/bin/pip install ../../zoid6/src" >&2; \
		exit 1; \
	fi
	install -m 0644 $(ZOID6_UNIT_SRC) $(ZOID6_UNIT_DST)
	systemctl daemon-reload
	@echo "Installed $(ZOID6_UNIT_DST). Run: systemctl enable --now zoid6"

uninstall-zoid6-systemd:
	-systemctl stop zoid6
	-systemctl disable zoid6
	-rm -f $(ZOID6_UNIT_DST)
	-systemctl daemon-reload
	@echo "Removed $(ZOID6_UNIT_DST)."

version:
	@echo '__version__ = "0.4.0.dev$(VERSION)"' > $(PROJECT)/_version.py
	@echo 'githash = "'`git log -1 --format='%H' 2>/dev/null | cut -c 1-16`'"' >> $(PROJECT)/_version.py
	@echo 'datestamp = "$(VERSION)"' >> $(PROJECT)/_version.py

push: tag
	git push -u github v$(VERSION)

tag:
	@if git rev-parse v$(VERSION) >/dev/null 2>&1; then \
		echo "Tag v$(VERSION) already exists. Skipping."; \
	else \
		git tag -a v$(VERSION) -m "Release v$(VERSION)"; \
		echo "Tag v$(VERSION) created locally."; \
	fi

# ---- pgsql: identd mapping for the daemon login (run as root) -------------------------------
# Adds the managed lines in daemon/pgsql/ to the server's pg_ident.conf and pg_hba.conf.
#   - pg_ident.conf: appended (map entries have no order).
#   - pg_hba.conf: inserted above the first existing rule for database zoid6, because the first
#     matching rule wins. If there is no such rule, appended.
# Idempotent: a line already present is skipped. Backs up both files, validates, then reloads.
# Bed's rollback login. Removed at cutover, with the bed role.
# Not part of prod: run explicitly.
PGSQL_DATA ?= /var/lib/pgsql/18/data
PGSQL_BIN  ?= /usr/pgsql-18/bin
PGSQL_UNIT ?= postgresql-18.service
PGSQL_SRC  ?= $(CURDIR)/../daemon/pgsql

install-pgsql:
	@test "$$(id -u)" -eq 0 || [ -n "$(PGSQL_ALLOW_NONROOT)" ] || { echo "install-pgsql: run as root (sudo make install-pgsql)"; exit 1; }
	@test -f $(PGSQL_DATA)/pg_hba.conf || { echo "install-pgsql: $(PGSQL_DATA)/pg_hba.conf not found"; exit 1; }
	@ts=$$(date +%Y%m%d%H%M%S); \
	cp -p $(PGSQL_DATA)/pg_hba.conf $(PGSQL_DATA)/pg_hba.conf.bak-$$ts && \
	cp -p $(PGSQL_DATA)/pg_ident.conf $(PGSQL_DATA)/pg_ident.conf.bak-$$ts && \
	echo "backed up pg_hba.conf and pg_ident.conf (suffix .bak-$$ts)"
	@# pg_ident.conf: append any managed line not already present.
	@while IFS= read -r line; do \
	  case "$$line" in ''|\#*) continue ;; esac; \
	  if grep -qxF -- "$$line" $(PGSQL_DATA)/pg_ident.conf; then echo "already in pg_ident.conf: $$line"; \
	  else printf '%s\n' "$$line" >> $(PGSQL_DATA)/pg_ident.conf; echo "added to pg_ident.conf: $$line"; fi; \
	done < $(PGSQL_SRC)/pg_ident.bed.conf
	@# pg_hba.conf: insert each managed rule above the first rule for database zoid6.
	@while IFS= read -r line; do \
	  case "$$line" in ''|\#*) continue ;; esac; \
	  if grep -qxF -- "$$line" $(PGSQL_DATA)/pg_hba.conf; then echo "already in pg_hba.conf: $$line"; continue; fi; \
	  first=$$(grep -nE '^[[:space:]]*(host|hostssl|hostnossl|local)[[:space:]]+(zoid6)[[:space:]]' $(PGSQL_DATA)/pg_hba.conf | head -1 | cut -d: -f1); \
	  if [ -n "$$first" ]; then \
	    sed -i "$${first}i $$line" $(PGSQL_DATA)/pg_hba.conf; echo "inserted above line $$first of pg_hba.conf: $$line"; \
	  else printf '%s\n' "$$line" >> $(PGSQL_DATA)/pg_hba.conf; echo "appended to pg_hba.conf: $$line"; fi; \
	done < $(PGSQL_SRC)/pg_hba.bed.conf
	@# Validate the files with the server's own parser; a bad line stops the install before reload.
	@$(if $(PGSQL_ALLOW_NONROOT),true,sudo -u postgres) $(PGSQL_BIN)/postgres -D $(PGSQL_DATA) -C server_version >/dev/null 2>&1 || \
	  { echo "install-pgsql: server config check failed; restore from the .bak-* files"; exit 1; }
	$(if $(PGSQL_ALLOW_NONROOT),@echo "sandbox: reload skipped",systemctl reload $(PGSQL_UNIT))
	@echo "install-pgsql: managed lines in place and $(PGSQL_UNIT) reloaded"
