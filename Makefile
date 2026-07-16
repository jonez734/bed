PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON ?= python3.12
RSYNC = rsync --chmod=F0644 --mkpath --archive --verbose

.PHONY: all help clean build version rename-sdist sign release install uninstall install-venv uninstall-venv install-systemd uninstall-systemd install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles install-etc uninstall-etc restorecon setup-db deploy

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
	@echo "  install-venv       Create venv and install bed wheel at /var/lib/bed/venv"
	@echo "  uninstall-venv     Remove /var/lib/bed/venv"
	@echo "  restorecon         Relabel venv binaries for SELinux (Fedora/RHEL)"
	@echo "  install-systemd    Copy bed.service to /usr/lib/systemd/system/ and daemon-reload"
	@echo "  uninstall-systemd  Stop, disable, and remove the bed.service unit"
	@echo "  install-etc        Install /etc/bed/ config from factory defaults"
	@echo "  uninstall-etc      Remove installed config files"
	@echo "  setup-db           Bootstrap database: bbsengine6 startup + bed role"
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

build: version
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

VENV_DIR = /var/lib/bed/venv

WHEEL_DIR = /tmp/$(PROJECT)-$$
BBSENGINE_DIR = $(CURDIR)/../bbsengine6/py
GETDATE_DIR = $(CURDIR)/../getdate_next

install-venv:
	@command -v sudo >/dev/null 2>&1 || { echo "Error: sudo required"; exit 1; }
	@sudo -u bed test -d "$(VENV_DIR)" || sudo -u bed $(PYTHON) -m venv "$(VENV_DIR)"
	sudo -u bed $(VENV_DIR)/bin/pip install --upgrade pip
	$(PYTHON) -m ensurepip --upgrade >/dev/null 2>&1 || true
	$(PYTHON) -m pip install build setuptools wheel
	mkdir -p $(WHEEL_DIR)
	rm -f $(WHEEL_DIR)/*.whl
	$(MAKE) -C $(BBSENGINE_DIR) version
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(GETDATE_DIR)
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(BBSENGINE_DIR)
	$(MAKE) version
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(CURDIR)
	sudo -u bed $(VENV_DIR)/bin/pip install $(WHEEL_DIR)/*.whl
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
	sudo $(RSYNC) $(UNIT_SRC) $(UNIT_DST)
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

deploy: install
