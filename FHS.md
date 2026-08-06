# FHS/UAPI Compliance Upgrade

## Overview

Upgrade bed and mistermcfeely to comply with the Filesystem Hierarchy Standard (FHS)
and UAPI Linux File System Hierarchy specification.

## Key FHS/UAPI Locations

| Location | Purpose |
|---|---|
| `/etc/<package>/` | System-specific configuration |
| `/usr/share/factory/etc/<package>/` | Vendor-supplied default configs |
| `/usr/lib/systemd/system/` | Vendor-shipped systemd units |
| `/usr/lib/sysusers.d/` | Vendor sysusers configs |
| `/usr/lib/tmpfiles.d/` | Vendor tmpfiles configs |

## bed Changes

### 1. Create `/usr/share/factory/etc/bed/`
- Add `bed.json` (vendor default config)

### 2. Update `src/bed/daemon/bed.tmpfiles`
- Add entry: `d /etc/bed 0755 root root - -`

### 3. Update `src/bed/daemon/bed.service`
- Add `--config /etc/bed/bed.json` to `ExecStart`

### 4. Update `src/bed/config.py`
- Change default config path to `/etc/bed/bed.json`
- Keep `bed/data/bed.json` as fallback if `/etc/bed/bed.json` doesn't exist

### 5. Update `Makefile`
- Change `install-systemd` destination from `/etc/systemd/system/` to `/usr/lib/systemd/system/`
- Update `uninstall-systemd` accordingly
- Add `install-etc` target:
  - Creates `/etc/bed/` (if not exists)
  - Copies `/usr/share/factory/etc/bed/bed.json` to `/etc/bed/bed.json`
  - Creates `/etc/bed/bed.env` with documented env var examples
- Add `uninstall-etc` target
- Update `install` target chain to include `install-etc`

### 6. Update `src/Makefile`
- Same changes as root Makefile

## mistermcfeely Changes

### 1. Create `/usr/share/factory/etc/postoffice/`
- Add `mcfeely-authd.conf` (vendor default config)

### 2. Update `etc-postoffice/postoffice.tmpfiles`
- Add entry: `d /etc/postoffice 0755 root root - -`

### 3. Update `etc-postoffice/mcfeely-authd.service`
- Change `--config` path to `/etc/postoffice/mcfeely-authd.conf`

### 4. Update `src/mcfeely_authd/config.py`
- Change default config path from `/etc/mcfeely-authd.conf` to `/etc/postoffice/mcfeely-authd.conf`

### 5. Update `Makefile`
- Change `install-systemd` destination from `/etc/systemd/system/` to `/usr/lib/systemd/system/`
- Update `uninstall-systemd` accordingly
- Update `install-etc` target:
  - Install to `/etc/postoffice/` instead of `/etc/`
  - Copy factory defaults to `/etc/postoffice/`
- Update `uninstall-etc` target

### 6. Update `etc-postoffice/Makefile`
- Update `mcfeely-authd.conf` destination to `/etc/postoffice/`
- Update systemd service destination to `/usr/lib/systemd/system/`

## Shared venv under `zoid6`

zoid6 is the anchor package for a single shared venv at `/var/lib/zoid6/venv`,
owned by a `zoid6` system user/group. All bbsengine6 services (bed, postoffice,
casino, empyre, murdermotel, etc.) are installed into it so any service user can
`import` any package. `deploytool` owns the topology — per-project Makefiles do
not hard-code venv paths.

### 1. Makefile venv defaults
- Both Makefiles use `VENV_DIR ?= /var/lib/zoid6/venv` (overridable, no longer a
  per-service path like `/var/lib/bed/venv`)
- `VENV_OWNER ?= zoid6`, `VENV_GROUP ?= zoid6`

### 2. Install behavior
- `install-venv` builds wheels for `../bbsengine6/py`, `../getdate_next`, and
  the project, then installs them via `sudo -u $(VENV_OWNER) $(VENV_DIR)/bin/pip install`
- The anchor venv is created by `make install-venv` in `zoid6/src` (sysusers,
  tmpfiles, venv); `deploytool postoffice bed zoid6` populates it

### 3. systemd units
- `ExecStart` uses the `@VENV_DIR@` placeholder; `install-systemd` substitutes
  it with `$(VENV_DIR)` at install time
- Service `User=` stays per-service (bed, postoffice) even though the venv is
  shared; the sysusers bridge (`m bed zoid6`, `m postoffice zoid6`) grants the
  zoid6 group so service users can read the shared venv

### 4. SELinux
- `restorecon` / `semanage` rules target `$(VENV_DIR)/bin(/.*)?`, i.e. the shared
  `/var/lib/zoid6/venv/bin/`

## Files Modified

| Project | File | Change |
|---|---|---|
| bed | `src/bed/config.py` | Default config path to `/etc/bed/bed.json` |
| bed | `src/bed/daemon/bed.service` | Add `--config` flag |
| bed | `src/bed/daemon/bed.tmpfiles` | Add `/etc/bed` entry |
| bed | `Makefile` | Add `install-etc`, fix `install-systemd` |
| bed | `src/Makefile` | Same as root Makefile |
| mistermcfeely | `src/mcfeely_authd/config.py` | Default config path to `/etc/postoffice/mcfeely-authd.conf` |
| mistermcfeely | `etc-postoffice/mcfeely-authd.service` | Update `--config` path |
| mistermcfeely | `etc-postoffice/postoffice.tmpfiles` | Add `/etc/postoffice` entry |
| mistermcfeely | `Makefile` | Fix `install-etc`, `install-systemd` |
| mistermcfeely | `etc-postoffice/Makefile` | Fix destinations |

## Files Created

| Project | File |
|---|---|
| bed | `/usr/share/factory/etc/bed/bed.json` |
| mistermcfeely | `/usr/share/factory/etc/postoffice/mcfeely-authd.conf` |

## Decisions

- **Directory creation**: Both tmpfiles.d and Makefile create `/etc/<package>/`
- **Factory defaults**: Makefile copies them to `/etc/` on install
