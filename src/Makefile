PROJECT = bed
OUTDIR = ../dist/ # /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON = python3

.PHONY: build version
.PHONY: clean build rename-sdist sign release all

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
# TODO: create bed/src/bed/Makefile so '$(MAKE) -C bed clean' works; model on casino/src/casino/Makefile (subdir cleans) and bed/Makefile root (systemd targets)

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
