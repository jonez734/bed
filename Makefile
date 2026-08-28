PROJECT = bed
OUTDIR = /srv/repo/$(PROJECT)/
VERSION = $(shell date +%Y%m%d%H%M)

PYTHON ?= python3.12
RSYNC = rsync --chmod=F0644 --mkpath --archive --verbose

.PHONY: all help clean build ensure-repo ensure-build-dir version rename-sdist sign release install uninstall install-venv uninstall-venv install-systemd uninstall-systemd install-sysusers uninstall-sysusers install-tmpfiles uninstall-tmpfiles install-etc uninstall-etc restorecon setup-db deploy deploy-venv deploy-prod commit-version clean-egg-info

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
	@echo "  deploy                          Non-sudo: build wheels + pip install into active venv (alias for deploy-venv)"
	@echo "  deploy EDITABLE=1               Like deploy, but install bed editable from src/ (live edits)"
	@echo "  deploy DEPLOY_EDITABLE=1        Same as EDITABLE=1 (deploytool --editable sets this)"
	@echo "  deploy DEV=1                    Same as EDITABLE=1 (legacy alias; will be removed in a future release)"
	@echo "  deploy-venv                     Non-sudo: build wheels + pip install into active venv"
	@echo "  deploy-prod                     Full prod install (sysusers + tmpfiles + venv + systemd + etc)"
	@echo "  clean                           Remove build artifacts"
	@echo "  clean-egg-info                  Remove in-tree *.egg-info/ dirs (defensive)"

# `make deploy EDITABLE=1` installs bed editable from src/ (live edits).
# `make deploy`             installs bed from a freshly-built wheel.
# EDITABLE=1 is a no-op for `install-venv` / `deploy-prod` / `build` —
# those paths always produce wheel artifacts. EDITABLE is set on the
# make command line via variable override (GNU make treats unknown
# flags like --foo as errors before target parsing, so a CLI flag idiom
# is not portable across make implementations).
#
# Accepted names (in order of preference):
#   EDITABLE=1          — canonical, recommended
#   DEPLOY_EDITABLE=1   — set by `deploytool --editable`
#   DEV=1               — legacy alias, kept for one release
#
# Behavior change vs. the previous DEV=1 form: editable mode installs
# into the **active** venv (the one that called `make deploy`), not
# into the per-service `/var/lib/bed/venv`. This matches the spirit of
# the dev/edit loop (test changes against the venv you're already in)
# and avoids surprising the operator with a separate install location
# during iteration.
ifeq ($(DEPLOY_EDITABLE),1)
EDITABLE := 1
else ifeq ($(EDITABLE),1)
EDITABLE := 1
else ifeq ($(DEV),1)
EDITABLE := 1
else
EDITABLE :=
endif

# Wipe in-tree *.egg-info/ dirs that a prior `pip install -e .` may have
# left behind. Such egg-infos bake absolute paths into SOURCES.txt and
# poison any later `python -m build`, which trips the setuptools
# "no absolute paths" guard with messages like:
#   "setup script specifies an absolute path:
#    /home/opencode/data/work/bed/src/bed/__init__.py"
clean-egg-info:
	-rm -rf *.egg-info src/*.egg-info

clean: clean-egg-info
	-rm -rf build dist build.stale.* build.old
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

build: clean version ensure-build-dir
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

# Make sure $(1)/build/ exists with mode 1775 (sticky + rwxrwxr-x) before
# invoking `python -m build`. Mode 1775 is intentional:
#   - sticky (t): only the owner of a file inside may delete/rename it,
#     so concurrent builds under a shared group can't stomp each other.
#   - setgid (s) is intentionally NOT set: setuptools' shutil.copystat
#     mirrors build/'s mode onto the freshly-created dist-info dir, and
#     a setgid'd dist-info EPERMs the subsequent bdist_wheel step in
#     SELinux-enforcing + NoNewPrivs containers (we lack CAP_FSETID).
#   - group write (g+w): any user in the build group can rebuild
#     without needing to chown.
# The chmod is expressed as `chmod g-s,+t` (drop the setgid bit the
# parent dir inherited onto the freshly-mkdir'd build/, then add the
# sticky bit). The numeric form `chmod 1775` is functionally equivalent
# but fails on BTRFS+SELinux setups where the parent directory's
# setgid bit blocks the owner from clearing it via the numeric mode
# (`chmod: Operation not permitted` on a dir the caller owns). The
# symbolic form works because the kernel only restricts numeric-mode
# changes that would remove the inherited setgid bit; `g-s` is
# permitted regardless of where the bit came from.
#
# If $(1)/build/ exists but is owned by a different user (e.g. left over
# from a prior build run as a different uid), rename it out of the way
# first. The parent dir is group-writable in this tree so the rename is
# permitted even when we don't own the build/ contents. Without this,
# the subsequent chmod fails with EPERM and the build aborts.
PREPARE_BUILD = \
	if [ -d $(1)/build ] && [ ! -O $(1)/build ]; then \
		mv $(1)/build $(1)/build.stale.$$ 2>/dev/null || true; \
	fi; \
	mkdir -p $(1)/build && chmod g-s,+t $(1)/build

install-venv: clean-egg-info
	@command -v sudo >/dev/null 2>&1 || { echo "Error: sudo required"; exit 1; }
	@sudo -u $(VENV_OWNER) test -d "$(VENV_DIR)" || sudo -u $(VENV_OWNER) $(PYTHON) -m venv "$(VENV_DIR)"
	sudo -u $(VENV_OWNER) $(VENV_DIR)/bin/pip install --upgrade pip
	$(PYTHON) -m ensurepip --upgrade >/dev/null 2>&1 || true
	$(PYTHON) -m pip install build setuptools wheel
	mkdir -p $(WHEEL_DIR)
	rm -f $(WHEEL_DIR)/*.whl
	$(MAKE) -C $(BBSENGINE_DIR) version
	$(call PREPARE_BUILD,$(BBSENGINE_DIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(BBSENGINE_DIR)
	$(MAKE) version
	$(call PREPARE_BUILD,$(CURDIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(CURDIR)
	sudo -u $(VENV_OWNER) $(VENV_DIR)/bin/pip install $(WHEEL_DIR)/*.whl
	# TODO(verify-install): this `sudo -u <owner> pip install` runs as
	# VENV_OWNER, not the active operator, so the verify check has to be
	# either (a) re-sudoed to inspect the owner venv, or (b) accept that
	# silent no-ops here won't be caught from this Makefile. Compare the
	# wheel's METADATA Version against `pip show bbsengine6` AND
	# `pip show bed` (both get installed by the *.whl glob above). See
	# zoidoffice/src/Makefile's VERIFY_INSTALL variable for the reference
	# implementation; that one is operator-side and doesn't need the
	# sudo wrapper. Editable mode (EDITABLE/DEPLOY_EDITABLE/DEV=1) skips
	# the wheel install entirely so no check applies here.
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

# Non-sudo: build wheels for bbsengine6 + bed, then pip install into
# the active venv. Mirrors install-venv (lines 120-138) minus the
# sudo -u $(VENV_OWNER) venv bootstrap (122-123) and the SELinux
# relabel (135-137). WHEEL_DIR lives in /tmp (user-owned) so no sudo
# is needed for the build either.
# getdate_next is intentionally NOT built here. `bbsengine6/py/
# pyproject.toml` declares `getdate-next` as a runtime dep and pip
# resolves it (typically from PyPI) when the freshly-built bbsengine6
# wheel is installed. To use local getdate_next source instead, run
# `make -C $(CURDIR)/../getdate_next deploy-venv` before invoking
# this target.
# With EDITABLE=1 (or DEPLOY_EDITABLE=1 from `deploytool --editable`,
# or DEV=1 legacy alias), bed is installed editable from src/ instead
# of from a freshly-built wheel. See the EDITABLE detection block
# above `clean-egg-info` for accepted names and the venv-targeting
# behavior change.
deploy-venv: clean clean-egg-info
	@mkdir -p $(WHEEL_DIR)
	@rm -f $(WHEEL_DIR)/*.whl
	$(MAKE) -C $(BBSENGINE_DIR) version
	$(call PREPARE_BUILD,$(BBSENGINE_DIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(BBSENGINE_DIR)
	$(VIRTUAL_ENV)/bin/pip install $(WHEEL_DIR)/*.whl 2>/dev/null
	# TODO(verify-install): after this `pip install` of bbsengine6's
	# wheel, compare the wheel's METADATA Version against
	# `pip show bbsengine6`. Catches silent no-ops where pip reports
	# "already installed" without actually installing. See
	# zoidoffice/src/Makefile's VERIFY_INSTALL variable for the reference.
	# Note: stderr is dropped (`2>/dev/null`) here, so the verbatim pip
	# show output should also be captured when this gets implemented.
ifeq ($(EDITABLE),1)
	$(MAKE) -C src install
else
	$(MAKE) version
	$(call PREPARE_BUILD,$(CURDIR))
	$(PYTHON) -m build --no-isolation --wheel --outdir $(WHEEL_DIR) $(CURDIR)
	$(VIRTUAL_ENV)/bin/pip install $(WHEEL_DIR)/*.whl 2>/dev/null
	# TODO(verify-install): after this `pip install` of bed's wheel,
	# compare the wheel's METADATA Version against `pip show bed`.
	# Same rationale as the bbsengine6 check above. Editable branch
	# (the ifeq $(EDITABLE)=1 above) installs from source, not a wheel,
	# so the comparison semantics differ — skip the check there.
endif
	-rm -rf $(WHEEL_DIR)
	@echo "bed installed into active venv$(if $(EDITABLE), in dev/editable mode)"

# Umbrella prod install: includes everything that needs sudo
# AND the per-service venv. Reuses the existing install target.
deploy-prod: install
	@echo "bed installed (production)"

# Non-sudo default — mirrors the `deploy-venv` shape (build wheels for
# bbsengine6 + bed, then pip install into the active venv). The sudo
# umbrella is `deploy-prod` (alias for `install`).
deploy: deploy-venv

commit-version:
	git add src/$(PROJECT)/_version.py
	git diff --cached --quiet || git commit -m "Bump $(PROJECT) version to $(VERSION)"
