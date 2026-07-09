PROJECT = bed
OUTDIR = ../dist/ # /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON = python3

UNIT_SRC = bed/daemon/bed.service
UNIT_DST = /etc/systemd/system/bed.service

SYSUSERS_SRC = bed/daemon/bed.sysusers
SYSUSERS_DST = /usr/lib/sysusers.d/bed.conf

TMPFILES_SRC = bed/daemon/bed.tmpfiles
TMPFILES_DST = /usr/lib/tmpfiles.d/bed.conf

.PHONY: build version
.PHONY: clean build rename-sdist sign release all
.PHONY: install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles install-systemd uninstall-systemd

build: version
	cd .. && $(PYTHON) -m build --outdir $(OUTDIR)

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

release: clean build rename-sdist sign

all:

clean:
	-rm *~
	$(MAKE) -C bed clean

# TODO: OUTDIR differs from existing bed/Makefile (../dist/ vs /srv/repo/bed/); consolidate entry points
# TODO: standardize version label names across packages (drop __ prefix/suffix on __datestamp__/__version__)
install-sysusers:
	install -m 0644 $(SYSUSERS_SRC) $(SYSUSERS_DST)
	systemd-sysusers
	@echo "Created bed user and group via $(SYSUSERS_DST)"

uninstall-sysusers:
	-rm -f $(SYSUSERS_DST)
	@echo "Removed $(SYSUSERS_DST)"

install-tmpfiles:
	install -m 0644 $(TMPFILES_SRC) $(TMPFILES_DST)
	systemd-tmpfiles --create
	@echo "Created /var/log/bed and /var/lib/bed via $(TMPFILES_DST)"

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

version:
	@echo '__version__ = "0.0.1.dev$(VERSION)"' > $(PROJECT)/_version.py
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
