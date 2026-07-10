PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON ?= python3

.PHONY: all help clean build version rename-sdist sign release install uninstall install-venv uninstall-venv install-systemd uninstall-systemd install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles

all: help

help:
	@echo "bed - BBS Engine Daemon"
	@echo ""
	@echo "Targets:"
	@echo "  install            Full install: sysusers + tmpfiles + venv + systemd"
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
	@echo "  install-systemd    Copy bed.service to /etc/systemd/system/ and daemon-reload"
	@echo "  uninstall-systemd  Stop, disable, and remove the bed.service unit"
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
	@echo 'githash = "'`git log -1 --format='%H' 2>/dev/null | cut -c 1-16`'"' >> src/$(PROJECT)/_version.py
	@echo 'datestamp = "$(VERSION)"' >> src/$(PROJECT)/_version.py
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
UNIT_DST = /etc/systemd/system/$(PROJECT).service

SYSUSERS_SRC = src/$(PROJECT)/daemon/$(PROJECT).sysusers
SYSUSERS_DST = /usr/lib/sysusers.d/$(PROJECT).conf

TMPFILES_SRC = src/$(PROJECT)/daemon/$(PROJECT).tmpfiles
TMPFILES_DST = /usr/lib/tmpfiles.d/$(PROJECT).conf

install-sysusers:
	install -m 0644 $(SYSUSERS_SRC) $(SYSUSERS_DST)
	systemd-sysusers
	@echo "Created bed user and group via $(SYSUSERS_DST)"

uninstall-sysusers:
	-rm -f $(SYSUSERS_DST)
	@echo "Removed $(SYSUSERS_DST)"

install-tmpfiles: install-sysusers
	install -m 0644 $(TMPFILES_SRC) $(TMPFILES_DST)
	systemd-tmpfiles --create
	@echo "Created /var/log/bed via $(TMPFILES_DST)"

uninstall-tmpfiles:
	-rm -f $(TMPFILES_DST)
	@echo "Removed $(TMPFILES_DST)"

VENV_DIR = /var/lib/bed/venv

install-venv:
	@command -v sudo >/dev/null 2>&1 || { echo "Error: sudo required"; exit 1; }
	@sudo -u bed test -d "$(VENV_DIR)" || sudo -u bed python3 -m venv "$(VENV_DIR)"
	sudo -u bed $(VENV_DIR)/bin/pip install --upgrade pip build setuptools wheel
	sudo -u bed $(VENV_DIR)/bin/python -m build --no-isolation --wheel --outdir $(CURDIR)/dist $(CURDIR)
	sudo -u bed $(VENV_DIR)/bin/pip install $(CURDIR)/dist/$(PROJECT)-*.whl
	@echo "Installed bed into $(VENV_DIR)"

uninstall-venv:
	-rm -rf $(VENV_DIR)
	@echo "Removed $(VENV_DIR)"

install: install-sysusers install-tmpfiles install-venv install-systemd
	@echo "bed fully installed. Run: systemctl enable --now $(PROJECT)"

install-systemd:
	install -m 0644 $(UNIT_SRC) $(UNIT_DST)
	systemctl daemon-reload
	@echo "Installed $(UNIT_DST). Run: systemctl enable --now $(PROJECT)"

uninstall-systemd:
	-systemctl stop $(PROJECT)
	-systemctl disable $(PROJECT)
	-rm -f $(UNIT_DST)
	-systemctl daemon-reload
	@echo "Removed $(UNIT_DST)"

uninstall: uninstall-systemd uninstall-venv uninstall-tmpfiles uninstall-sysusers
	@echo "bed fully uninstalled"
