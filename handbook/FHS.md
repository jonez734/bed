# FHS/UAPI Compliance

## Overview

`bed` complies with the Filesystem Hierarchy Standard (FHS) and
UAPI Linux File System Hierarchy specification. The daemon is
installed under per-service paths and ships factory-default
configuration under `/usr/share/factory/`.

## Key FHS/UAPI Locations

| Location | Purpose |
|---|---|
| `/etc/<package>/` | System-specific configuration (operator-edited) |
| `/usr/share/factory/etc/<package>/` | Vendor-supplied default configs |
| `/usr/lib/systemd/system/` | Vendor-shipped systemd units |
| `/usr/lib/sysusers.d/` | Vendor sysusers configs |
| `/usr/lib/tmpfiles.d/` | Vendor tmpfiles configs |

## Per-service venv

`bed` is the foundational WebSocket daemon. It owns a per-service
venv at `/var/lib/bed/venv` (owned by `bed:bed`). It does **not**
share a venv with any other package. The dependency direction is:

```
games  ──consume──>  zoid6  ──consume──>  bed  ──consume──>  bbsengine6
                                       └────consume────>  websockets
```

`bed` does not depend on `zoid6`. `bed/pyproject.toml` lists only
`bbsengine6` and `websockets` as runtime dependencies. Consumers
of `bed` (`zoid6`, games, `bbsengine6`) own their own venvs and
install the bed wheel into theirs via `pip install`.

### venv defaults

- `bed/Makefile`: `VENV_DIR ?= /var/lib/bed/venv`,
  `VENV_OWNER ?= bed`, `VENV_GROUP ?= bed`. Each service Makefile
  declares its own venv path and owner; the topology is
  per-service, not shared.
- `zoid6/src/Makefile`: `VENV_DIR ?= /var/lib/zoid6/venv`,
  `VENV_OWNER ?= zoid6`, `VENV_GROUP ?= zoid6`.
- Each consumer Makefile that imports `bed` builds and installs
  the bed wheel as part of `install-venv`.

### systemd units

`ExecStart` uses the `@VENV_DIR@` placeholder; `install-systemd`
substitutes it with `$(VENV_DIR)` at install time. Each service
unit points at its own per-service venv (`/var/lib/bed/venv/bin/bed`
for `bed.service`, `/var/lib/zoid6/venv/bin/zoid6` for `zoid6.service`).
Service `User=` is per-service (`bed`, `zoid6`).

### SELinux

`restorecon` / `semanage` rules target `$(VENV_DIR)/bin(/.*)?`.
Each Makefile labels its own per-service venv
(`/var/lib/bed/venv/bin` for `bed`, `/var/lib/zoid6/venv/bin` for
`zoid6`).

## Decisions

- **Directory creation**: Both `tmpfiles.d` and the Makefile create
  `/etc/<package>/`. The factory default is then copied from
  `/usr/share/factory/etc/<package>/` into `/etc/<package>/` on
  install.
- **Factory defaults**: byte-identical to the wheel-shipped
  packaged default (`bed/src/bed/data/bed.json`), so resolving
  to the packaged default is semantically equivalent to
  "operator has not customised the config". See
  `bed.specs.auth.md §Configuration` for the precedence chain.
- **`bed.service`**: ships with
  `ExecStart=@VENV_DIR@/bin/bed --config /etc/bed/bed.json`.
  Per-game services (e.g. `zoid6-bed.service`) live in the game
  repo and pass `--config` + `--router`.

## See also

- `handbook/BED_AUTH.md` — bearer-token protocol reference.
- `handbook/ARCHITECTURE.md` — dep graph + code map.
- `handbook/ADOPTERS.md` — per-game consumer pointers.
- `bbsengine6/handbook/specs/` — sibling spec tree.
