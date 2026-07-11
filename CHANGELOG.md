# Changelog

All notable changes to `bed` (BBS Engine Daemon) are recorded here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

The first 14 entries below were replayed from the meta-repo where `bed/` lived
as a plain directory (submodule-less) before being extracted into its own
repository on 2026-06-27. Commits are listed in reverse chronological order;
short hashes are the ones from this repository's history.

## Unreleased

### bed: startup module for database bootstrap

New `bed.startup` module runs `bbsengine6.startup` (database schema,
core roles, functions) and then creates the `bed` PostgreSQL role with
LOGIN and USAGE on the `engine` schema.  Invoked via
`python -m bed.startup` or `bed-startup`.  The `bed` role is no longer
created by `bbsengine6` core — it is owned by the bed package.

### bed: --host default is 127.0.0.1

The `--host` argparse default is now `127.0.0.1` (was `localhost`,
which can resolve to multiple addresses and is therefore ambiguous
for a server bind). The README and tests are updated to match.
Production deployments that need all-interfaces binding should set
`bind.host` in `bed.json` (the `--config` file) or pass
`--host 0.0.0.0` on the command line. The systemd unit is unchanged;
operators relying on its default bind should verify their `bed.json`
or environment file.

### bed: --pidfile handles stale and colliding pids

- `_write_pidfile` now distinguishes a write failure (warn,
  continue without the pidfile) from a live-pid collision
  (exit 1). Stale pids (process gone) are overwritten with a
  warning, so a SIGKILL'd predecessor does not block the
  next start.
- The pidfile is opened with `O_EXCL` and retried once on a
  TOCTOU race.
- New `TestPidfileIntegration` class covers the end-to-end
  `main_async` lifecycle (writes on start, removes on exit,
  refuses on live-pid collision, overwrites on stale pid).

### bed: add .gitignore for __pycache__, *.pyc, *.egg-info, build, dist, caches (da59f81) - 2026-06-27

Adds a minimal `.gitignore` matching the patterns the Makefile's `clean`
target removes. The historical commits (now cleaned of binaries) did not
have a `.gitignore`, so any local Python build artifacts would leak into
the next commit.

### bed: add ping CLI (8faf22b) - 2026-06-27

Adds a small developer utility under `bed/tools/ping.py` (renamed from
`bedping.py`). Opens a WebSocket to a running BED, sends a `ping`, prints
the `pong`, prompts for a moniker, sends an `auth` message, prints the
result. Useful for sanity-checking that the daemon is alive and that the
auth round-trip works without needing `wscat`. Registered as a console
script in `pyproject.toml`: `pip install .` now also installs a `ping`
command alongside `bed`.

### bed: test: cover MonikerAuthRouter resolution via load_router_class (14acadd) - 2026-06-27

Sanity check that `--router zoid6.api.handler.MonikerAuthRouter` still
resolves to the real `MonikerAuthRouter` class after dynamic loading,
since the AuthService integration silently relies on this round-trip.

### bed: Makefile: build depends on version target (f77d0ea) - 2026-06-27

Without this, `make build` would run with whatever `_version.py` happens
to be in the working tree, not the freshly stamped version. The `release`
target already chains `clean version build rename-sdist sign` so this
also closes a gap if anyone invokes `build` directly.

### bed: docfix: --autorestart default is False, not True (66f389f) - 2026-06-27

The committed help text said `or True` but the actual default behavior
is `False`. Correct the help text to match the behavior.

### bed: add bed.client subpackage (transport, message base, bank client) (b881ee0) - 2026-06-27

Adds `bed.client` — a small synchronous client for talking to a running
BED from another Python program. Includes transport, message base class,
bank client (read balance / move coins), connection helpers, exceptions,
a probe utility, and a singleton wrapper.

### bed: document autorestart default as off (9ec6554) - 2026-06-27

Documents that `--autorestart` defaults to `off` (False) unless the
packaged `bed.json` enables it. (Corrected in 66f389f — this entry
describes the original wording; later corrected to `(default: from
bed.json, or False)`.)

### bed: bearer-token AuthService (auth, reconnect, refresh, revoke) (54ef363) - 2026-06-27

The headline feature. Adds a full bearer-token authentication service:

- `bed.api.auth.AuthService` — issue / verify / refresh / revoke tokens
- `bed.api.secret` — per-instance HMAC secret loader (auto-create
  `~/.config/bed/bed.secret` mode 0600 on first run)
- `bed.api.token_store` — in-memory and DB-backed token stores
- `bed.api.session` — session registry bound to tokens
- `bed.api.credential_provider` — pluggable credential backends
  (moniker-only, password)
- `bed.api.errors` — standard error envelopes
- `bed.data.sql.bed_token.sql` — optional DB token-store schema
- Wire protocol documented in `docs/BED_AUTH.md`
- New CLI flags: `--bed-secret`, `--token-ttl`, `--token-persistence`,
  `--credential-provider`, `--bed-instance-id`
- `bed.json` gains an `auth` block
- Comprehensive test suite in `tests/test_auth_service.py`

### bed: add build Makefile in src/ modeled on bbsengine6 (e35f587) - 2026-06-26

Adds `src/Makefile` mirroring bbsengine6's build pattern, so building
from inside `src/` works the same as building from the repo root.

### bed: relocate Makefile from src/ to repo root (bfee466) - 2026-06-26

Moves the primary Makefile up one level, matching bbsengine6's
convention. `src/Makefile` is reintroduced in e35f587 as a thin shim
for `src/`-relative builds.

### bed: --config flag, dynamic version, systemd unit, install helpers (2f5cde9) - 2026-06-26

- `--config PATH` CLI flag + packaged `bed/data/bed.json` default
- `bed._version.__version__` driven by `make version` (date + git hash)
- `bed/src/bed/daemon/bed.service` systemd unit
- `make install-systemd` / `uninstall-systemd` targets
- Expanded TODO.md with the bearer-token plan
- Comprehensive test additions in `test_bed.py`

### Add BED Sink integration with bbsengine6.io plan (88afc6a) - 2026-06-25

Adds the BED Sink integration plan (BBS Engine Daemon's TCP sink that
terminates the BBS-Engine I/O protocol on behalf of a game router) to
`TODO.md`. No code changes.

### Separate help (F1) and key_f2 (F2) into distinct message types; add bed.json config keys (0210e6e) - 2026-06-25

Splits the previously-coupled help and F2 message types into independent
push channels, each with its own rate-limit and queue config keys
(`help.rate_limit`, `key_f2.rate_limit`, `key_f2.max_items`,
`key_f2.channel_allow_list`). Plan written up in `TODO.md`.

### Add menu message type: single-pick option list with hotkeys (c131b08) - 2026-06-25

Design doc for a new `menu` message type: a single-pick option list with
single-character hotkeys, rendering on the client as a numbered list with
the hotkey highlighted. Plan written up in `TODO.md`.

### Add echo/echo_ack generic push-based text channel plan (adb66e3) - 2026-06-25

Design doc for a generic push-based text channel (server pushes a line,
client optionally acks) with an ack timeout. Plan written up in `TODO.md`.

### Add bearer token plan and adoption map (e4291ca) - 2026-06-25

High-level plan for the bearer-token auth system that lands in 54ef363,
including the per-game adoption map (empyre, casino, mistermcfeely,
murdermotel, zoid6). Plan written up in `TODO.md`.

### Move buildargs to lib.py for bbsengine6 consistency (0ef1022) - 2026-06-24

Moves the `buildargs` argparse function out of `main.py` and into
`bed.lib`, matching the bbsengine6 layout where the same function lives
in `bbsengine6.lib`.

### Rename parse_args to buildargs for bbsengine6 consistency (be5a962) - 2026-06-24

Renames `bed.main.parse_args` → `bed.main.buildargs` for consistency
with `bbsengine6.lib.buildargs` (which `bed` now also calls). The
function will be moved into `bed.lib` in 0ef1022.

### Add bed project - BBS Engine Daemon with dynamic router loading (63011bc) - 2026-06-24

Initial commit of `bed`. A small WebSocket daemon that sits in front of
a `bbsengine6` game router, terminates JSON-over-WebSocket, and lets the
game own the wire protocol. Dynamic router loading via dotted-name
import (`--router FQCN`). Includes:

- `bed.main` — BED daemon entry point
- `bed.api.handler` — router loader and connection handler
- `bed.config` — `bed.json` loader
- `bed.data.bed.json` — packaged default config
- `bed.tests.test_bed` — initial test suite (parse_args, session
  manager, handler)
- `pyproject.toml` — setuptools build, `bed` console script entry
