PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION := $(shell date +%Y%m%d%H%M)

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

UNIT_SRC = bed/daemon/bed.service
UNIT_DST = /usr/lib/systemd/system/bed.service

SYSUSERS_SRC = bed/daemon/bed.sysusers
SYSUSERS_DST = /usr/lib/sysusers.d/bed.conf

TMPFILES_SRC = bed/daemon/bed.tmpfiles
TMPFILES_DST = /usr/lib/tmpfiles.d/bed.conf

FACTORY_DIR = ../usr/share/factory/etc/bed
ETC_DIR = /etc/bed

.PHONY: install version
.PHONY: clean rename-sdist sign release all
.PHONY: install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles install-systemd uninstall-systemd install-etc uninstall-etc

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
	install -m 0644 $(SYSUSERS_SRC) $(SYSUSERS_DST)
	systemd-sysusers
	@echo "Created bed user and group via $(SYSUSERS_DST)"

uninstall-sysusers:
	-rm -f $(SYSUSERS_DST)
	@echo "Removed $(SYSUSERS_DST)"

install-tmpfiles: install-sysusers
	install -m 0644 $(TMPFILES_SRC) $(TMPFILES_DST)
	systemd-tmpfiles --create
	@echo "Created /etc/bed, /var/log/bed, /var/lib/bed via $(TMPFILES_DST)"

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

install-etc:
	install -d $(ETC_DIR)
	install -m 0644 $(FACTORY_DIR)/bed.json $(ETC_DIR)/bed.json
	@echo "Installed $(ETC_DIR)/bed.json from factory defaults"
	@test -f $(ETC_DIR)/bed.env || \
		(echo "# bed environment overrides" > $(ETC_DIR)/bed.env && \
		 echo "# BED_DATABASEUSER=zoid6" >> $(ETC_DIR)/bed.env && \
		 echo "# BED_DATABASEPASSWORD=..." >> $(ETC_DIR)/bed.env && \
		 echo "Created $(ETC_DIR)/bed.env")
	@chmod 0640 $(ETC_DIR)/bed.env
	@echo "Installed $(ETC_DIR)/bed.env"

uninstall-etc:
	-rm -rf $(ETC_DIR)
	@echo "Removed $(ETC_DIR)"

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
