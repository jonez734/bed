PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON ?= python3.12
RSYNC = rsync --chmod=F0644 --mkpath --archive --verbose

.PHONY: all help clean build ensure-repo ensure-build-dir version rename-sdist sign release install uninstall install-venv uninstall-venv install-systemd uninstall-systemd install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles install-etc uninstall-etc restorecon setup-db deploy deploy-venv deploy-prod commit-version

all: help

help:
	@echo "bed - BBS Engine Daemon"
	@echo ""
	@echo "Targets:"
	@echo "  install            Full install: sysusers + tmpfiles + venv + systemd + etc"
	@echo "  version            Stamp src/bed/_version.py with date + git hash"
	@echo "  build              Build sdist+wheel into $(OUTDIR)"
	@echo "  rename-sdist       Rename built sdist to include -src suffix"
	@echo "  sign               GPG-detach-sign every artifact in $(OUTDIR)"
	@echo "  release            clean + version + build + rename-sdist + sign"
	@echo "  install-sysusers   Create bed user and group via systemd-sysusers"
	@echo "  uninstall-sysusers Remove sysusers.d/bed.conf"
	@echo "  install-tmpfiles   Create /var/log/bed via systemd-tmpfiles"
	@echo "  uninstall-tmpfiles Remove tmpfiles.d/bed.conf"
	@echo "  install-venv       Install bed wheel into per-service venv at $(VENV_DIR)"
	@echo "  uninstall-venv     Remove $(VENV_DIR)"
	@echo "  restorecon         Relabel venv binaries for SELinux (Fedora/RHEL)"
	@echo "  install-systemd    Install bed.service (@VENV_DIR@ templated) and daemon-reload"
	@echo "  uninstall-systemd  Stop, disable, and remove the bed.service unit"
	@echo "  install-etc        Install /etc/bed/ config from factory defaults"
	@echo "  uninstall-etc      Remove installed config files"
	@echo "  setup-db           Bootstrap database: bbsengine6 startup + bed role"
	@echo "  deploy             Non-sudo: build wheels + pip install into active venv (alias for deploy-venv)"
	@echo "  deploy-venv        Non-sudo: build wheels + pip install into active venv"
	@echo "  deploy-prod        Full prod install (sysusers + tmpfiles + venv + systemd + etc)"
	@echo "  clean              Remove build artifacts"

clean:
	-rm -rf build dist
	-rm -rf *.egg-info
	-find . -type d -name __pycache__ -exec rm -rf {} +
	-find . -type d -name .pytest_cache -exec rm -rf {} +
	-find . -type d -name .ruff_cache -exec rm -rf {} +
	-find . -type d -name .mypy_cache -exec rm -rf {} +

version:
	@echo '__version__ = "0.0.1.dev$(VERSION)"' > src/$(PROJECT)/_version.py
	@echo '__datestamp__ = "$(VERSION)"' >> src/$(PROJECT)/_version.py
	@echo '__githash__ = "'`git log -1 --format='%H' 2>/dev/null | cut -c 1-16`'"' >> src/$(PROJECT)/_version.py
	@cat src/$(PROJECT)/_version.py

.PHONY: ensure-repo
ensure-repo:
	@stat -c '%G' /srv/repo 2>/dev/null | grep -qx repo || sudo chgrp repo /srv/repo
	@stat -c '%a' /srv/repo 2>/dev/null | grep -q '^2775$$' || sudo chmod 2775 /srv/repo

.PHONY: ensure-build-dir
ensure-build-dir: ensure-repo
	@mkdir -p /srv/repo/$(PROJECT)/
	@stat -c '%G' /srv/repo/$(PROJECT)/ 2>/dev/null | grep -qx repo || sudo chgrp repo /srv/repo/$(PROJECT)/
	@stat -c '%a' /srv/repo/$(PROJECT)/ 2>/dev/null | grep -q '^2775$$' || sudo chmod 2775 /srv/repo/$(PROJECT)/

build: version ensure-build-dir
	$(call PREPARE_BUILD,$(CURDIR))
	$(PYTHON) -m build --outdir $(OUTDIR)

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

release: clean version build rename-sdist sign

UNIT_SRC = src/$(PROJECT)/daemon/$(PROJECT).service
UNIT_DST = /usr/lib/systemd/system/$(PROJECT).service

FACTORY_DIR = usr/share/factory/etc/$(PROJECT)
ETC_DIR = /etc/$(PROJECT)

SYSUSERS_SRC = src/$(PROJECT)/daemon/$(PROJECT).sysusers
SYSUSERS_DST = /usr/lib/sysusers.d/$(PROJECT).conf

TMPFILES_SRC = src/$(PROJECT)/daemon/$(PROJECT).tmpfiles
TMPFILES_DST = /usr/lib/tmpfiles.d/$(PROJECT).conf

install-sysusers:
	sudo $(RSYNC) $(SYSUSERS_SRC) $(SYSUSERS_DST)
	sudo systemd-sysusers
	@echo "Created bed user and group via $(SYSUSERS_DST)"

uninstall-sysusers:
	-sudo rm -f $(SYSUSERS_DST)
	@echo "Removed $(SYSUSERS_DST)"

install-tmpfiles: install-sysusers
	sudo $(RSYNC) $(TMPFILES_SRC) $(TMPFILES_DST)
	sudo systemd-tmpfiles --create
	@echo "Created /etc/bed, /var/log/bed, /var/lib/bed via $(TMPFILES_DST)"

uninstall-tmpfiles:
	-sudo rm -f $(TMPFILES_DST)
	@echo "Removed $(TMPFILES_DST)"

VENV_DIR ?= /var/lib/bed/venv
VENV_OWNER ?= bed
VENV_GROUP ?= bed

WHEEL_DIR = /tmp/$(PROJECT)-$$
BBSENGINE_DIR = $(CURDIR)/../bbsengine6/py
GETDATE_DIR = $(CURDIR)/../getdate_next

# Make sure $(1)/build/ exists with mode 0755 (no setgid) before invoking
# `python -m build`. `chmod g-s` is used (not `chmod 0755`) because the
# process lacks CAP_FSETID, so only stripping the setgid bit is permitted
# on a dir we own; `chmod 0755` on a 0o2775 dir raises EPERM. Without
# this, setuptools bdist_wheel EPERMs in SELinux-enforcing + NoNewPrivs
# containers when shutil.copystat mirrors the in-tree egg-info's mode
# 0o2775 onto the freshly-created dist-info dir.
PREPARE_BUILD = mkdir -p $(1)/build && chmod g-s $(1)/build

install-venv:
	@command -v sudo >/dev/null 2>&1 || { echo "Error: sudo required"; exit 1; }
	@sudo -u $(VENV_OWNER) test -d "$(VENV_DIR)" || sudo -u $(VENV_OWNER) $(PYTHON) -m venv "$(VENV_DIR)"
	sudo -u $(VENV_OWNER) $(VENV_DIR)/bin/pip install --upgrade pip
	$(PYTHON) -m ensurepip --upgrade >/dev/null 2>&1 || true
	$(PYTHON) -m pip install build setuptools wheel
	mkdir -p $(WHEEL_DIR)
	rm -f $(WHEEL_DIR)/*.whl
	$(MAKE) -C $(BBSENGINE_DIR) version
	$(call PREPARE_BUILD,$(GETDATE_DIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(GETDATE_DIR)
	$(call PREPARE_BUILD,$(BBSENGINE_DIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(BBSENGINE_DIR)
	$(MAKE) version
	$(call PREPARE_BUILD,$(CURDIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(CURDIR)
	sudo -u $(VENV_OWNER) $(VENV_DIR)/bin/pip install $(WHEEL_DIR)/*.whl
	rm -rf $(WHEEL_DIR)
	@command -v semanage >/dev/null 2>&1 && \
		sudo semanage fcontext -a -t bin_t "$(VENV_DIR)/bin(/.*)?" 2>/dev/null || true
	@command -v restorecon >/dev/null 2>&1 && sudo restorecon -R $(VENV_DIR)/bin/ || true
	@echo "Installed bed into $(VENV_DIR)"

uninstall-venv:
	-rm -rf $(VENV_DIR)
	@echo "Removed $(VENV_DIR)"

restorecon:
	@command -v semanage >/dev/null 2>&1 && \
		sudo semanage fcontext -a -t bin_t "$(VENV_DIR)/bin(/.*)?" 2>/dev/null || true
	sudo restorecon -R $(VENV_DIR)/bin/
	@echo "Relabeled $(VENV_DIR)/bin/ for SELinux"

install-etc:
	sudo rsync --chmod=F0755 --mkpath --archive --verbose $(FACTORY_DIR)/ $(ETC_DIR)/
	sudo rsync --chmod=F0640 --mkpath --archive --verbose $(FACTORY_DIR)/bed.env $(ETC_DIR)/bed.env
	@echo "Installed $(ETC_DIR) from factory defaults"

uninstall-etc:
	-sudo rm -rf $(ETC_DIR)
	@echo "Removed $(ETC_DIR)"

install: install-sysusers install-tmpfiles install-venv install-systemd install-etc
	@echo "bed fully installed. Run: systemctl enable --now $(PROJECT)"

setup-db:
	$(VENV_DIR)/bin/bed-startup
	@echo "Database bootstrapped. Run: systemctl enable --now $(PROJECT)"

install-systemd:
	sed 's|@VENV_DIR@|$(VENV_DIR)|g' $(UNIT_SRC) | sudo tee $(UNIT_DST) > /dev/null
	sudo chmod 0644 $(UNIT_DST)
	sudo chown root:root $(UNIT_DST)
	sudo systemctl daemon-reload
	@echo "Installed $(UNIT_DST). Run: systemctl enable --now $(PROJECT)"

uninstall-systemd:
	-sudo systemctl stop $(PROJECT)
	-sudo systemctl disable $(PROJECT)
	-sudo rm -f $(UNIT_DST)
	-sudo systemctl daemon-reload
	@echo "Removed $(UNIT_DST)"

uninstall: uninstall-systemd uninstall-venv uninstall-tmpfiles uninstall-sysusers uninstall-etc
	@echo "bed fully uninstalled"

# Non-sudo: build wheels for getdate_next + bbsengine6 + bed, then
# pip install into the active venv. Mirrors install-venv (lines 120-138)
# minus the sudo -u $(VENV_OWNER) venv bootstrap (122-123) and the
# SELinux relabel (135-137). WHEEL_DIR lives in /tmp (user-owned) so
# no sudo is needed for the build either.
deploy-venv:
	$(MAKE) -C $(BBSENGINE_DIR) version
	$(call PREPARE_BUILD,$(GETDATE_DIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(GETDATE_DIR)
	$(call PREPARE_BUILD,$(BBSENGINE_DIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(BBSENGINE_DIR)
	$(MAKE) version
	$(call PREPARE_BUILD,$(CURDIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(CURDIR)
	$(VIRTUAL_ENV)/bin/pip install $(WHEEL_DIR)/*.whl \
		2>/dev/null || $(PYTHON) -m pip install $(WHEEL_DIR)/*.whl
	-rm -rf $(WHEEL_DIR)
	@echo "bed installed into active venv (non-sudo)"

# Umbrella prod install: includes everything that needs sudo
# AND the per-service venv. Reuses the existing install target.
deploy-prod: install
	@echo "bed installed (production)"

# Non-sudo default — mirrors the `deploy-venv` shape (build wheels for
# getdate_next + bbsengine6 + bed, then pip install into the active
# venv). The sudo umbrella is `deploy-prod` (alias for `install`).
deploy: deploy-venv

commit-version:
	git add src/$(PROJECT)/_version.py
	git diff --cached --quiet || git commit -m "Bump $(PROJECT) version to $(VERSION)"
