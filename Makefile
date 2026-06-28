PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON ?= python3

.PHONY: all help clean build version rename-sdist sign release install-systemd uninstall-systemd

all: help

help:
	@echo "bed - BBS Engine Daemon"
	@echo ""
	@echo "Targets:"
	@echo "  version            Stamp src/bed/_version.py with date + git hash"
	@echo "  build              Build sdist+wheel into $(OUTDIR)"
	@echo "  rename-sdist       Rename built sdist to include -src suffix"
	@echo "  sign               GPG-detach-sign every artifact in $(OUTDIR)"
	@echo "  release            clean + version + build + rename-sdist + sign"
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
