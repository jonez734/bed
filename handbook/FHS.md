# FHS/UAPI Compliance

## Overview

`bed` complies with the Filesystem Hierarchy Standard (FHS) and
UAPI Linux File System Hierarchy specification. The daemon is
installed under per-service paths and ships factory-default
configuration under `/usr/share/factory/`.

## Key FHS/UAPI Locations

| Location | Purpose |
|---|---|
| `$(BED_HOME)` (`/home/NEEDINFO` until set) | The `bed` account's home and venv parent |
| `/usr/lib/systemd/system/` | Vendor-shipped systemd units |
| `/usr/lib/sysusers.d/` | Vendor sysusers configs |
| `/usr/lib/tmpfiles.d/` | Vendor tmpfiles configs |

## Per-service venv

`bed` is the foundational WebSocket daemon. It owns a per-service
venv at `/home/bed/venv` (owned by `bed:bed`). It does **not**
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

- `bed/Makefile`: `VENV_DIR ?= /home/bed/venv` (overridable with `--prefix`),
  `VENV_OWNER ?= bed`, `VENV_GROUP ?= bed`. Each service Makefile
  declares its own venv path and owner; the topology is
  per-service, not shared.
- `zoid6/src/Makefile`: `VENV_DIR ?= /home/zoid6/venv`,
  `VENV_OWNER ?= zoid6`, `VENV_GROUP ?= zoid6`.
- Each consumer Makefile that imports `bed` builds and installs
  the bed wheel as part of `install-venv`.

### systemd units

`ExecStart` uses the `@VENV_DIR@` placeholder; `install-systemd`
substitutes it with `$(VENV_DIR)` at install time. Each service
unit points at its own per-service venv (`/home/bed/venv/bin/bed` for
`bed.service`). bed installs only its own unit. zoid6's unit,
`zoid6/src/zoid6/daemon/zoid6.service`, belongs to zoid6 and is installed by zoid6's
`install-systemd`. Service `User=` is per-service.

### SELinux

`restorecon` / `semanage` rules target `$(VENV_DIR)/bin(/.*)?`.
Each Makefile labels its own per-service venv
(`/home/bed/venv/bin` for `bed`, `/home/zoid6/venv/bin` for `zoid6`).

## Decisions

- **No `/etc`**: bed reads and writes nothing under `/etc`, so it installs on a
  read-only root filesystem. There is no `/etc/bed` directory and no factory copy into it.
- **Configuration**: the packaged `bed/src/bed/data/bed.json`, shipped inside the wheel. The
  resolver order is `--config`, then `$BED_CONFIG`, then the packaged file. `install-venv`
  refuses a wheel without a valid packaged `bed.json`.
- **`bed.service`**: `ExecStart=@VENV_DIR@/bin/bed`, with no `--config`. Per-game services
  (e.g. `zoid6-bed.service`) live in the game repo and pass `--config` + `--router`.
- **`BED_HOME`**: the one variable for the account home, rendered into the sysusers, tmpfiles
  and unit templates. It defaults to a placeholder, `/home/NEEDINFO`, until the path is chosen.

## See also

- `handbook/BED_AUTH.md` — bearer-token protocol reference.
- `handbook/ARCHITECTURE.md` — dep graph + code map.
- `handbook/ADOPTERS.md` — per-game consumer pointers.
- `bbsengine6/handbook/specs/` — sibling spec tree.
