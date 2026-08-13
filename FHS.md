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

## Per-service venv; consumers install the bed wheel

`bed` is the foundational WebSocket daemon. It owns a per-service venv at
`/var/lib/bed/venv` (owned by `bed:bed`). It does **not** share a venv with
any other package. The dependency direction is:

```
games  ──consume──>  zoid6  ──consume──>  bed  ──consume──>  bbsengine6
                                       └────consume────>  websockets
```

`bed` does not depend on `zoid6`. `bed/pyproject.toml` only lists `bbsengine6`
and `websockets` as runtime dependencies. Consumers of `bed` (`zoid6`, games,
`bbsengine6`) own their own venvs and install the bed wheel into theirs via
`pip install`.

### 1. Makefile venv defaults
- `bed/Makefile` uses `VENV_DIR ?= /var/lib/bed/venv`,
  `VENV_OWNER ?= bed`, `VENV_GROUP ?= bed`. Each service Makefile declares its
  own venv path and owner; the topology is per-service, not shared.
- `zoid6/src/Makefile` uses `VENV_DIR ?= /var/lib/zoid6/venv`,
  `VENV_OWNER ?= zoid6`, `VENV_GROUP ?= zoid6`.
- Each consumer Makefile that imports `bed` (currently `zoid6`) builds and
  installs the bed wheel as part of `install-venv`.

### 2. Install behavior
- `bed/Makefile` `install-venv` builds wheels for `../bbsengine6/py`,
  `../getdate_next`, and `bed` itself, then installs them into
  `/var/lib/bed/venv` via `sudo -u bed /var/lib/bed/venv/bin/pip install`.
- `zoid6/src/Makefile` `install-venv` builds the `bed` wheel and installs it
  into `/var/lib/zoid6/venv` alongside the zoid6 wheel, so `import bed` works
  inside the zoid6 daemon.
- The two venvs are independent: removing one does not affect the other.

### 3. systemd units
- `ExecStart` uses the `@VENV_DIR@` placeholder; `install-systemd` substitutes
  it with `$(VENV_DIR)` at install time. Each service unit points at its own
  per-service venv (`/var/lib/bed/venv/bin/bed` for `bed.service`,
  `/var/lib/zoid6/venv/bin/zoid6` for `zoid6.service`).
- Service `User=` is per-service (`bed`, `zoid6`). No cross-service group
  bridge is required because the venvs are not shared.

### 4. SELinux
- `restorecon` / `semanage` rules target `$(VENV_DIR)/bin(/.*)?`. Each Makefile
  labels its own per-service venv (`/var/lib/bed/venv/bin` for `bed`,
  `/var/lib/zoid6/venv/bin` for `zoid6`).

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
