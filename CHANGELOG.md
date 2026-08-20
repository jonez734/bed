# Changelog

All notable changes to `bed` (BBS Engine Daemon) are recorded here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

The first 14 entries below were replayed from the meta-repo where `bed/` lived
as a plain directory (submodule-less) before being extracted into its own
repository on 2026-06-27. Commits are listed in reverse chronological order;
short hashes are the ones from this repository's history.

## Unreleased

### bed: `deploy-venv` honors `DEPLOY_EDITABLE`; `DEV` renamed to `EDITABLE`

Part of the cross-monorepo Phase 1 work in `deploytool`'s
`--editable` flag (see `deploytool/CHANGELOG.md` `[Unreleased]`).

The Makefile's install-mode variable is renamed from `DEV` to
`EDITABLE` (canonical, recommended) and now accepts three
names with documented precedence:

  1. `DEPLOY_EDITABLE=1` (set by `deploytool --editable`)
  2. `EDITABLE=1` (canonical, recommended for direct `make`
     invocations)
  3. `DEV=1` (legacy alias, kept for one release)

The cascade lives in a single `ifeq/else ifeq/else` block above
`clean-egg-info` (`bed/Makefile:51-66`); all downstream
references resolve to the canonical `$(EDITABLE)` variable.

Behavior change vs. the previous `DEV=1` form: editable mode
now installs into the **active** venv (the one that called
`make deploy`), not into the per-service `/var/lib/bed/venv`.
This matches the spirit of the dev/edit loop (test changes
against the venv you're already in) and avoids surprising the
operator with a separate install location during iteration.
The change is documented in the comment block above the
cascade (`bed/Makefile:39-50`).

Verified: `make -n -C bed deploy-venv` → wheel build + active-venv
install; `make -n -C bed deploy-venv EDITABLE=1`,
`make -n -C bed deploy-venv DEPLOY_EDITABLE=1`, and
`make -n -C bed deploy-venv DEV=1` all show
`$(MAKE) -C src install` (which `bed/src/Makefile:24-25`
resolves to `cd .. && pip install --no-cache-dir -e . &&
rm -rf src/bed.egg-info`) followed by
`bed installed into active venv in dev/editable mode`.

### bed: stop building `getdate_next`'s wheel; pip resolves it via bbsengine6's pyproject

`bed/Makefile` `install-venv` and `deploy-venv` no longer build
`getdate_next`'s wheel. The `GETDATE_DIR` variable and the
`PREPARE_BUILD` + `python -m build --wheel` lines for
`$(GETDATE_DIR)` were removed from both targets. They now build
only `bbsengine6` + `bed` wheels and pip install them.

The resolution model: `bbsengine6/py/pyproject.toml` declares
`getdate-next` as a runtime dep, so pip resolves it (from PyPI by
default) when the freshly-built bbsengine6 wheel is installed.
`deploytool` no longer orchestrates a separate `getdate_next`
install — the `OPTIONAL_DEPENDENCIES["bbsengine6"] = ["getdate_next"]`
entry and the `--with-deps` CLI flag were removed.

Same refactor applied to `zoid6/src/Makefile` (lines 27, 266-270)
and `mistermcfeely/Makefile` (lines 80, 90) — both were building
`getdate_next`'s wheel inline for the same reason; both no longer
do.

`getdate_next/Makefile deploy-venv` stays as the canonical local
build target but is no longer invoked by deploytool. Developers
testing local source changes run it manually before deploying
anything that pulls in bbsengine6:

```sh
make -C ../getdate_next deploy-venv
make -C bed deploy-venv   # bbsengine6.whl's pip install sees
                          # getdate-next already satisfied
```

`casino` is unchanged: its `Makefile` has no inline getdate_next
build (it relies on `pip install .` resolving `bbsengine6`, which
transitively pulls `getdate-next`).

### getdate_next: upgrade `PREPARE_BUILD` to match `bed`'s (foreign-owned rename + `chmod 1775`)

`getdate_next/Makefile`'s `PREPARE_BUILD` helper only had the
original partial fix from this changelog's
"bed: strip setgid on `build/` before `python -m build`" entry:
`mkdir -p build && chmod g-s build`. Two issues remained:

1. **Foreign-owned `build/` EPERMs the chmod.** When a prior build
   ran as a different uid (e.g. left over from a CI run as
   `minotaur`'s deploy user), the leftover `build/` is owned by
   that uid and the unprivileged `chmod` fails with `EPERM`. This
   is the failure mode the `deploy getdate_next` invocation on
   2026-08-20 hit. `bed/Makefile`'s `PREPARE_BUILD` already
   handled this by renaming a not-owned-by-us `build/` to
   `build.stale.$$` first; `getdate_next/Makefile` did not.
2. **`chmod g-s` is umask-dependent.** On a source tree whose
   umask drops the sticky bit on `mkdir`, `chmod g-s` produces a
   non-sticky `build/` — and then a concurrent rebuild in the
   shared group can stomp files inside. `chmod 1775` pins the
   mode idempotently.

`getdate_next/Makefile`'s `PREPARE_BUILD` was rewritten to match
`bed/Makefile`'s (sans the `$(1)` parameterization, since
`getdate_next` only has its own `build/`):

```make
PREPARE_BUILD = \
	if [ -d build ] && [ ! -O build ]; then \
		mv build build.stale.$$ 2>/dev/null || true; \
	fi; \
	mkdir -p build && chmod 1775 build
```

Rationale comment block above the definition was also copied from
`bed/Makefile` verbatim so future readers see the same
SELinux+NoNewPrivs / `CAP_FSETID` / `shutil.copystat` chain of
causes that justifies dropping the setgid bit on `build/`.

`deploytool/tests/test_deploy_bed_tui.py` already pins both
behaviours (`chmod 1775` present, `chmod g-s` absent, foreign-
owned rename present) — its assertions are text-based on
`PREPARE_BUILD` and pass for `bed`. A parallel
`test_deploy_getdate_next_tui.py` was added to pin the same
invariants on `getdate_next/Makefile`.

Tracked in `zoid6/TODO.md` ("`getdate_next` — has a stub at
`getdate_next/Makefile:32` ... Upgrade to match the bed
pattern."); that checkbox is now ticked.

`deploytool` sub-target chaining is now consistent end-to-end:
every tui consumer reaches `bbsengine6.tui` through `bed.tui`
(`casino.tui -> bed.tui -> bbsengine6.tui`,
`zoid6.tui -> bed.tui -> bbsengine6.tui`). The explicit
`("bbsengine6", "tui")` in `casino.tui`'s conditional deps was
dropped — it's transitively redundant with `bed.tui`.

### bed: chain `egg-info` cleanup into the `src/Makefile install` shell

`bed/src/Makefile install`'s second recipe line was
`rm -rf src/bed.egg-info`, which ran in a fresh shell after the
first line's `cd ..`. Because each Make recipe line is its own
shell, the `cd` from line 1 didn't carry to line 2, so line 2 ran
with `$(CURDIR) == bed/src/` (correct), but `src/bed.egg-info`
relative to the package source dir was being removed on every
install — fine on a clean tree, but a race + stray directory if
the prior `pip install -e .` had already cleaned it up, and the
intent was to chain both into one shell that ran after the
`pip install -e .` returned from the parent dir.

The two lines are now chained into one shell so the order is
deterministic and the cleanup is part of the same logical step:

```make
install: clean-egg-info
	cd .. && $(PIP) install --no-cache-dir -e . \
		&& rm -rf src/bed.egg-info
```

`clean-egg-info` is unchanged (still `find . -name '*.egg-info'
-type d -exec rm -rf {} +`) and runs before the chained shell, so
the install starts from a tree with no leftover egg-info.

### bed: `load_config` honors `autorestart` on transient FS / network errors

`bed.config.load_config` now distinguishes operator errors (which
still propagate) from transient FS / network errors (which previously
crashed the daemon unconditionally). The recoverable set is:

* `socket.gaierror` (DNS resolution failure, e.g. ENOTFOUND)
* `socket.timeout`
* `PermissionError` / `OSError` with `errno == EACCES`
* `OSError` with `errno` in `{EIO, ESTALE, ETXTBSY, ENETUNREACH,
  EHOSTUNREACH, ECONNREFUSED, ETIMEDOUT}`

When the primary path raises one of these, behavior depends on the
resolved `autorestart` value at startup (CLI `--autorestart` wins,
otherwise the value of `bed.autorestart` from a peek-read of the
JSON, otherwise `False`):

* `autorestart=True` -> `level="warning"` message naming the
  original failure and the full absolute path of the fallback JSON
  now in use, then load `bed/data/bed.json`.
* `autorestart=False` -> `level="error"` message naming the
  original failure and the full absolute path that failed, then
  raise `ConfigIORecoverableError`; `main_async` exits with status
  `3`. The systemd unit is updated to add `3` to
  `RestartPreventExitStatus` so this does not loop.

The SIGHUP reload path (`bed/src/bed/main.py:_reload_config_and_apply`)
keeps its existing semantics: log the error and return; the daemon
continues with the old config. A `ConfigIORecoverableError` raised
on SIGHUP is treated the same as any other `OSError` -- it does
NOT exit the daemon.

The new exit code `3` is distinct from `2` (permanent bind failure
without `restart_on_bind_failure`) and `1` (general load failure),
so operators and monitoring can tell the three failure modes apart.

Changes:

* `bed/src/bed/config.py` -- new `_RECOVERABLE_ERRNOS` frozenset,
  `_is_recoverable_load_error()` helper, `_peek_autorestart()` helper,
  `ConfigIORecoverableError` exception class. `load_config()` gains a
  keyword-only `autorestart=False` argument.
* `bed/src/bed/main.py` -- `main_async` resolves `autorestart` via
  CLI > peek > False before loading, passes it into
  `config.load_config`, and catches `ConfigIORecoverableError` ->
  `sys.exit(3)`. The SIGHUP handler's catch list gains
  `ConfigIORecoverableError` so reload failures stay non-fatal.
* `bed/src/bed/daemon/bed.service` -- `RestartPreventExitStatus=2 3`.
* `bed/src/bed/tests/test_bed.py` -- `TestConfigLoadFallback` covers
  both branches + every recoverable errno + operator-error
  propagation. `TestPeekAutorestart` covers the peek helper. New
  `TestMainAsyncConfigIOFallback` covers the `main_async` wiring
  (exit 3, peek+CLI resolution). `TestSighupConfigIORecoverableFallback`
  pins down SIGHUP keeps the old-config semantics.

### bed: `--config` resolves to wheel-shipped default when no flag is passed

The `--config` CLI flag is now optional. When the operator omits it,
`bed` walks this precedence (highest wins):

1. `--config <path>` on the command line (existing behavior)
2. `$BED_CONFIG` environment variable
3. `/etc/bed/bed.json` if present (FHS-installed config from
   `make install-etc`)
4. The packaged default shipped in the wheel
   (`bed/data/bed.json`, always present after `pip install bed`)

The systemd unit (`bed/src/bed/daemon/bed.service`) keeps passing
`--config /etc/bed/bed.json` so FHS hosts continue to use the
operator-edit surface. The fallback only fires for non-prod
invocations (`bed --foreground`, `make deploy-venv`, `bed --debug`,
anywhere `install-etc` has not been run).

This makes `bed` (no flags) a complete zero-config dev-mode
invocation, mirrors the existing `zoid6/main.py:_resolve_config_path`
resolver (`$ZOID6_CONFIG` → `/etc/zoid6/zoid6.json` → packaged), and
removes the last "sudo required to write `/etc/bed/bed.json`" coupling
between the wheel install and a usable default. The fallback chain
lands without breaking the FHS story: the factory default
(`bed/usr/share/factory/etc/bed/bed.json`) is byte-identical to the
packaged default (`bed/src/bed/data/bed.json`), so resolving to the
packaged default is semantically equivalent to "operator has not
customised the config".

Changes:

* `bed/src/bed/_configpath.py` (new) — `resolve_config_path()`,
  `CONFIG_ENV = "BED_CONFIG"`, `FHS_CONFIG = "/etc/bed/bed.json"`.
* `bed/src/bed/lib.py` — `--config` is `required=False, default=None`
  (was `required=True`); help text describes the fallback chain.
* `bed/src/bed/main.py` — `main_async` calls `resolve_config_path()`
  before loading; the explicit `if not os.path.isfile(args.config_file)`
  guard is dropped (the resolver always returns an existing path).
* `bed/src/bed/tests/test_bed.py` — `test_config_flag_required` is
  replaced by `test_config_flag_default_is_none`; four new
  `test_resolve_config_path_*` tests cover each precedence rung and
  a `test_main_async_resolves_packaged_default_when_no_config_flag`
  integration test confirms `bed` (no flags) walks the resolver
  through to the packaged default without exiting on a missing
  `--config` path.

Back-compat note: callers that were relying on `--config`'s
`required=True` to fail loud on a missing flag now get the packaged
default silently. Two operator-visible behaviours shift:

* `bed` (no `--config`) used to exit 1 with
  `Config file not found: None`; it now boots from the packaged
  default.
* Operators who explicitly pass `--config /nonexistent.json` still
  see the same `Config file not found` exit-1 path (the resolver
  does not paper over explicit paths).

See `bed/FHS.md` "## Default config path" for the rationale and
`bed/TODO.md` "## CLI `--config` flag" for the precedence table.

### bed: `deploy` target is now non-sudo (alias for `deploy-venv`)

- `bed/Makefile:223-226` — `deploy: deploy-venv` (was
  `deploy: install`). Direct `make deploy` invocations now run
  the non-sudo `deploy-venv` path (build wheels for
  `bbsengine6` + `bed`, then `pip install` into the active
  venv) instead of the sudo umbrella `install` chain.
  `getdate_next` is no longer built inline — pip resolves
  `getdate-next` as a transitive runtime dep of bbsengine6
  via `bbsengine6/py/pyproject.toml`. `make deploy-prod`
  remains the sudo path.
- `bed/Makefile:34` — `help` text updated to reflect the new
  default.
- This matches the parallel split in zoid6: `zoid6.tui` is
  non-sudo (`make deploy-tui` → `install` → `pip install -e .`)
  and `zoid6.prod` is the sudo umbrella (`make deploy-prod` →
  `install-fhs`).
- **Back-compat note**: callers that were doing `make deploy`
  expecting the full prod install must update to
  `make deploy-prod`. deploytool users were unaffected — the
  auto-drop rule at `deploytool/src/deploytool/lib.py:296` had
  already removed the implicit sudo path for `deploytool
  deploy bed`. See `bed/TODO.md` "## `bed.venv` (non-sudo) /
  `bed.prod` (sudo) deploy split" for the rationale.

### bed: apply `{var:promptcolor}` / `{var:inputcolor}` to all CLI interactive prompts

The `bed auth`, `bed bank`, and `bedping` console scripts read
user input via `bbsengine6.io.inputstring`, `bbsengine6.io.inputpassword`,
and `bbsengine6.io.inputinteger`. Eight prompts were missing the
`{var:promptcolor}` / `{var:inputcolor}` markup that the rest of
bed's CLI (and the established `bbsengine6.io` contract) expects,
so the prompt and input area rendered without the skin's colors.
All free-form prompts now include the color tags; the single
`inputchoice` call (`bed bank` menu) was already correct.
`bedping` is also migrated off Python's raw `input()` so it stops
bypassing `bbsengine6.io` markup — it was the last remaining
raw-`input()` call site in bed's CLI.

* `bed/src/bed/tools/auth.py:198` — `io.inputstring("moniker: ")` →
  `"{var:promptcolor}moniker: {var:inputcolor}"`.
* `bed/src/bed/tools/auth.py:204` — `inputpassword("password: ")` →
  `"{var:promptcolor}password: {var:inputcolor}"`; now passes
  `args=args` so `inputpassword`'s `**kwargs` forwards the same
  args context the door-mode loop uses.
* `bed/src/bed/tools/bank.py:559, 581, 603, 607, 654, 678` — six
  `io.inputinteger` / `io.inputstring` prompts wrapped in the
  same markup. Prompt text lowercased (`"Amount to add: "` →
  `"amount to add: "`) for consistency with the existing
  `auth` prompts.
* `bed/src/bed/tools/ping.py:54` — raw `input("moniker: ")` →
  `io.inputstring("{var:promptcolor}moniker: {var:inputcolor}")`.

Tests: `bed/src/bed/tests/test_ping_tool.py`
`test_ping_auth_round_trip_returns_zero` and
`test_invalid_pong_is_not_silenced` swap their `builtins.input`
stub for a `ping_tool.io.inputstring` mock so the prompt is
read through `bbsengine6.io` like every other bed CLI prompt.
No regression assertions on color-tag presence — tests verify
behavior only; color tags live exclusively in production code.

The kwarg additions for the bank prompts (`args=args`) are
deferred to `TODO.md` "Interactive prompts: pass `args=args` to
bed CLI input calls" so this change stays focused on the
color-tag bug and the `ping.py` migration.

### bed: ping-friendly-error pattern is now shared across all bin scripts

The friendly "connection refused" / "host unreachable" / "timed out"
rendering that `bedping` already produced is now driven by a single
helper in `bbsengine6.net.ping` so every bbsengine6-based bin script
renders the same one-line message via `bbsengine6.io.echo(level="error")`
and exits non-zero without a Python traceback when the daemon is not
listening.

Changes in `bed`:

* `bed.tools.ping` is now a thin module around the shared helper. It
  re-exports `PingUnavailable` (class identity preserved:
  `bed.tools.ping.PingUnavailable is bbsengine6.net.ping.PingUnavailable`)
  and delegates the WebSocket connect to
  `bbsengine6.net.ping.connect(host, port, prog="bedping")`. The
  ping/auth round-trip is kept (the existing happy-path test still
  sends both a ping and an auth frame). The `bin/bedping` shim is
  unchanged.
* `bed.tools.auth`, `bed.tools.bank`, and `bed.tools.message` now wrap
  the `BedUnavailable`-raising dispatch in a top-level `try/except
  BedUnavailable` and render the failure via
  `bbsengine6.io.echo(level="error")`, returning a non-zero exit
  status. Previously a stopped bed daemon produced a raw
  `ConnectionRefusedError` traceback on `bed auth`, `bed bank`,
  `bed message`.
* `bed.startup.ensure_startup` wraps `startuplib.runmodule`,
  `database.getpool`, and the `database.connect` block in
  `try/except (ConnectionError, TimeoutError, OSError,
  psycopg.OperationalError)`, rendering one-line friendly messages
  via `bbsengine6.io.echo(level="error")` and returning `False` so
  `bin/bed-startup` exits cleanly when Postgres is unreachable. The
  internal handling inside `bbsengine6.startup.main` is unchanged;
  this is defense-in-depth at the `ensure_startup` boundary.

Tests: `src/bed/tests/test_ping_tool.py` patches
`bbsengine6.net.ping.websockets` (where the connect actually happens
now) and pins the friendly-error path on `ConnectionRefusedError`,
`OSError`, `asyncio.TimeoutError`, and `WebSocketException`. The
happy round-trip regression guard still exercises the ping/auth
sequence end-to-end. The protocol-bug guard
(`test_invalid_pong_is_not_silenced`) confirms only transport-level
failures are swallowed — a wrong `{"type": "wat"}` reply still raises.

The shared helper lives in `bbsengine6/py`; see the bbsengine6
changelog for the helper itself.

### bed: strip setgid on `build/` before `python -m build`

On SELinux-enforcing hosts (Fedora, RHEL) and inside `NoNewPrivs`
containers, `python -m build` failed with
`Errno 1: Operation not permitted` when the source tree carried
the setgid bit (mode `0o2775`). The failure originates in
`shutil.copystat` (called from `setuptools.bdist_wheel.egg2dist`
via `shutil.copytree(<pkg>.egg-info, <pkg>.dist-info)`): the final
step `copystat(src_dir, dst_dir)` calls `os.chmod(dst,
stat.S_IMODE(src.st_mode))`, and the source `<pkg>.egg-info/` has
mode `0o2775` because setgid was inherited from the project tree.
The build process lacks `CAP_FSETID`, so the `chmod` raises EPERM
and the wheel build aborts.

The `Makefile` now runs a `PREPARE_BUILD` helper before every
`python -m build` invocation — `build`, `install-venv` (three
sites), and `deploy-venv` (three sites). The helper is:

```make
PREPARE_BUILD = mkdir -p $(1)/build && chmod g-s $(1)/build
```

`chmod g-s` (not `chmod 0755`) is the right primitive because
the build process lacks `CAP_FSETID`, so on a setgid parent only
*stripping* the setgid bit is permitted; `chmod 0755` on an
`0o2775` dir raises EPERM. Without this, setuptools
`bdist_wheel` EPERMs in SELinux-enforcing + `NoNewPrivs`
containers when `shutil.copystat` mirrors the in-tree
`<pkg>.egg-info/` mode `0o2775` onto the freshly-created
`<pkg>.dist-info/`.

Affected targets:

- `build`, `install-venv`, `deploy-venv` in `bed/Makefile`
- `build` in `getdate_next/Makefile`

(`bbsengine6/py/Makefile` has no `python -m build` target of its
own; its wheel is built from `bed/Makefile`'s `install-venv` /
`deploy-venv` calls.)

### bed: multi-bind (`--bind` CLI + JSON `bind` list)

Operators can now declare multiple listening addresses with one
daemon. A single host name like `localhost` fans out to one IPv4
listener and one IPv6 listener; explicit literals (`--bind 127.0.0.1`
+ `--bind ::1`) work too.

* New CLI flag `--bind HOST:PORT`, repeatable. Bracketed IPv6
  literals are supported (`--bind '[::1]:8765'`). Port range
  `[1, 65535]` is enforced at parse time.
* `bed.json` accepts `"bind": [{"host": "...", "port": ...}, ...]`
  (canonical list shape) and the legacy `"bind": {"host": ...,
  "port": ...}` dict shape.
* Precedence: `--bind` CLI > JSON `bind` list > JSON `bind` dict >
  `--host`/`--port` > argparse default. Documented in
  `SPEC.md §3` and `README.md`.
* `BED.start()` passes the resolved list to `WebSocketServer(binds=)`
  in `bbsengine6`. State (services, channel state, session manager,
  pre/post dispatch hooks) is shared across every listener, so a
  service registered once reaches every bind.
* Logging: a multi-bind start logs one line per listener with the
  address family (`inet` / `inet6`) plus a summary line. The
  single-bind case keeps the historical "BED started on HOST:PORT"
  line shape so existing log scrapers keep working.
* `restart_on_bind_failure` semantics extend to multi-bind: any of
  the N binds failing with `EADDRINUSE`/`EACCES` (or a typo'd host
  producing `gaierror`) triggers the exit-2 path. The error message
  distinguishes "free the port" from "check /etc/hosts".
* SIGHUP reload detects changes to the bind list as structural
  (restart required).

Files touched:

- `src/bed/lib.py` — `_bind_spec()` argparse type, `--bind` flag.
- `src/bed/main.py` — `_apply_bind_list_config()`, `_resolve_binds()`,
  `BED._final_binds()`, multi-bind log line, `gaierror` handling.
- `src/bed/tests/test_bed.py` — `TestBindMulti` (15 tests) and
  `TestBindMultiStart` (5 tests).

Dependency on `bbsengine6 >= 0.0.1.dev202608191019` for the new
`binds=` keyword on `WebSocketServer`. Older bed installs against
older bbsengine6 still work; the legacy `host=`/`port=` keyword
remains the 1-element shortcut.

### bed: `restart_on_bind_failure` knob, exit 2 on EADDRINUSE/EACCES

A permanent bind failure (port already in use, or permission denied)
used to make `bed` exit 1, which the systemd unit then retried
forever via `Restart=on-failure` — so a stuck port looked
indistinguishable from a transient crash loop, even when the
operator had explicitly set `autorestart: false`.

- New config key: `bed.restart_on_bind_failure` (default `false`)
  and matching CLI flag `--restart-on-bind-failure`. Independent of
  `autorestart` so a bind-stuck port does not enable general crash
  restarts.
- When the listening port fails with `EADDRINUSE` (errno 98) or
  `EACCES` (errno 13) and `restart_on_bind_failure` is `false`,
  `bed` exits with status **2** and logs a clear "refusing to
  restart on bind failure" message. systemd's
  `RestartPreventExitStatus=2` keeps the unit stopped.
- When `restart_on_bind_failure` is `true`, the in-process loop
  retries with the configured `restart_delay`, capped at
  `max_restarts`, exactly like the general `autorestart` loop.
- `daemon/bed.service` now also sets `StartLimitBurst=10` and
  `StartLimitIntervalSec=300s` so any unclassified crash loop
  eventually leaves the unit in `failed` state, visible to the
  operator, instead of silently retrying forever.
- `src/bed/lib.py` adds the `--restart-on-bind-failure` CLI flag.
- New test class `TestRestartOnBindFailure` in
  `src/bed/tests/test_bed.py` covers the EADDRINUSE/EACCES path.

### bed: add `casino` section to default `bed.json`

`src/bed/data/bed.json` now carries a top-level `casino` block
with the per-casino nested layout (`casinos.<name>.blackjack.*`):

```json
{
  "casino": {
    "blackjack": {
      "surrender_allowed": "early",
      "surrender_multiplier": 0.5
    }
  }
}
```

`surrender_multiplier=0.5` is the universal standard in regulated
casinos (Las Vegas Strip / Atlantic City / Macau; Wizard of Odds
and Vegas Advantage). Casino's `services.game.GameService.surrender`
reads the value via `casino.config.get_surrender_multiplier(args)`
so the per-table `net` in `dal.table.get_table_stats` stays
consistent with what the settle path actually credited the player.

BED's `main_async` does not yet apply this block to
`args._casino_config` explicitly. Casino's
`MessageRouter._bootstrap_casino_config(args)` auto-discovers
the section from `args.config_file`, so door-mode / standalone
tests work without a bed wiring change. A future bed commit will
add `_apply_casino_config(args, cfg)` that mirrors the existing
`_apply_database_config` / `_apply_auth_config` blocks and
forwards `args._casino_config` to `db_args` before constructing
the casino `MessageRouter`.

### bed: `ensure_startup` also drives `casino.startup.main`

After `bbsengine6.startup` and the `bed` role setup, `bed.startup.ensure_startup`
now dispatches to `casino.startup.main` via `bbsengine6.module.runmodule`,
so the casino package is loaded lazily — not a hard install-time
dependency on `bed`. The dispatch runs after the bed role setup commits
and the pool connection is released; casino owns its own pool/conn
lifecycle inside `casino.startup.main`.

`casino.startup.main` is idempotent (`database.classexists`,
`schemaexists`, `extensioninstalled`, `manage_schema_priv`), so
repeated invocations of `ensure_startup` are safe. Operators who want
to bootstrap the engine + bed role but not the casino schema can run
`python -m bbsengine6.startup` + the bed role setup directly; the
casino step is only added to the convenience entry point
`bed.startup.ensure_startup`.

### bed message: CLI tool, with auto-direct mode for DB-only subcommands

Adds the `bed message` console script (registered as `message` in
`pyproject.toml`). The tool is the operator-facing surface for the
unified message system: it drives `bed.api.message.MessageService`
for the WS-bound ops and `bbsengine6.message.*` for the DB-backed
ops, mirroring the bank tool's two-backend shape.

Seven subcommands:

| Subcommand       | Backend  | Notes                                            |
|------------------|----------|--------------------------------------------------|
| `subscribe`      | `bed`    | binds the bed WS to a moniker for NOTIFY fanout |
| `unsubscribe`    | `bed`    | drops the bed WS binding for a moniker          |
| `watch`          | `bed`    | subscribe + tail live pushes until interrupted |
| `pending`        | either   | backend-aware; WS or DB                          |
| `send`           | direct   | store a new message in the local DB              |
| `mark_read`      | direct   | mark a message as read for a recipient           |
| `mark_delivered` | direct   | mark a message as delivered for a recipient      |

`send` / `mark_read` / `mark_delivered` are forced to direct mode
inside `main_with_args` (`bed/src/bed/tools/message.py:835-838`)
regardless of whether the operator passed `--direct`. Bed's
`MessageService` registers only `subscribe` / `unsubscribe` /
`list_pending`; new messages flow through the local DB and surface
to bed via the `engine_message_recipient` NOTIFY trigger, so there
is no server-side wire handler for the DB-only ops. Forcing
`args.direct = True` before `select_backend` runs means the bed
probe is skipped entirely, the operator never has to pass `--direct`,
and the "bed unreachable; rerun with --direct" exit no longer fires
for these subcommands. The single source of truth is
`_DIRECT_ONLY_SUBCMDS` (`message.py:80`); adding a new DB-only
subcommand means adding it to the set.

Authorization on the CLI runs the same per-op policy the server
does, just on the client side. WS-bound ops (`subscribe` /
`unsubscribe` / `pending` / `watch`) delegate to
`bbsengine6.message.access()` via `_check_access`
(`message.py:256-300`); DB-only ops use a self-or-sysop gate
(`_check_self_or_sysop`, `message.py:302-336`) because
`bbsengine6.message.access` only recognizes the three wire-protocol
verbs. The CLI mirrors the WS handler's session-bound gate so the
two surfaces agree on what "unauthenticated" means.

Token lifecycle: in `bed` mode the CLI reads the bearer token from
`--token-file` (default `$XDG_RUNTIME_DIR/bed.token` or
`/tmp/bed-<uid>/bed.token`), uses `auth reconnect` to bind the
session to the WS, stashes the claim-derived `moniker` /
`is_sysop` on `args` so `_check_access` can use them, and persists
a rotated token back to the file at mode 0600. In `direct` mode
no token is required; the actor moniker is resolved from `--moniker`
or the local DB.

`send` accepts `--to MONIKER` (repeatable), `--channel NAME`,
`--urgency {ROUTINE,IMPORTANT,URGENT,CRITICAL}`, and either
`--content BODY` or `--template BODY` (mutually exclusive). The
template body is rendered via `bbsengine6.message.render_template`.
The CLI surfaces rate-limit / system-disabled / no-recipients cases
as one-line errors and exits non-zero.

Spec coverage: new Section 14 ("CLI tool — `bed message`") in
`bed/specs/message.md` covers subcommand vocabulary, backend
selection, the auto-direct-mode behavior, CLI flags, authorization,
token lifecycle, and per-handler dispatch. README's console-scripts
table now lists `message` and links to the spec.

### bed: add `bed.name` instance identity + identity-aware `ping`

A bed instance now has a `name` (default `"bed"`, override via
`--bed-name` or `bed.name` in bed.json) that flows through three
places:

- **Default secret filename**: `_default_secret_path(name)` returns
  `~/.config/bed/<name>.secret`. With the default `name = "bed"` this
  resolves to the historical `~/.config/bed/bed.secret` path, so
  existing installs are unaffected. Custom names (e.g. `mybbs`)
  yield `~/.config/bed/mybbs.secret`, letting multiple bed daemons
  share one host without colliding on the HMAC secret file. An
  explicit `--bed-secret` still wins.
- **`ping` reply**: a new `bed.api.ping.PingService` registers
  `["ping"]` and returns `{"type": "pong", "name": <bed_name>,
  "version": <bed.__version__>, "timestamp": <echoed>}`. Clients
  can probe a bed's identity with no `auth` round-trip.
- **`PingService` always wins**: `BED.start()` registers
  `PingService` LAST, after the router's own `register_all`. The
  router's `["ping"]` registration is overwritten; bbsengine6's
  `WebSocketServer.register_service` emits a WARNING on the
  overwrite (see `py/src/bbsengine6/net/transport.py:register_service`)
  so the swap is visible in the log. The swap is intentional: every
  bed instance surfaces its own `name` + `version` regardless of
  which router is loaded.

The `_apply_bed_name_config` helper applies `bed.name` from the
JSON config when the CLI did not set `--bed-name`. Empty / missing
/ whitespace-only names fall back to the default `"bed"` so the
secret-path derivation stays sane.

SIGHUP reload treats `bed_name` as a structural change (warns
"restart required") because changing it would move the secret file.

New tests:
- `bed/tests/test_ping_service.py` — handle_message shape,
  registration order, end-to-end `ping` over a real WebSocket with
  the router's plain `pong` overwritten by `PingService`'s enriched
  one.
- `bed/tests/test_bed.py::TestBEDParseArgs` — default `bed_name`,
  `--bed-name` override, `_default_secret_path` substitution.
- `bed/tests/test_bed.py::TestConfigFlag` — `bed.name` config
  override, empty / whitespace fallback, CLI-vs-config precedence.
- `bed/tests/test_bed.py::TestSighupReload::test_sighup_warns_on_structural_changes`
  — extended to assert `bed_name=` shows up in the "restart
  required" warning.

### bed bank: render bottombar with version, moniker+balance, host:port

The `bed bank` CLI now paints a status bar while the menu loop is
running. Left side reads `bed.bank (<version>)`; right side is two
fragments registered through `bbsengine6.bottombar`:

- a live `<moniker>: <balance>` fragment that refreshes after every
  successful `bank_balance` / `bank_add` / `bank_remove` and re-queries
  the bank service after `bank_transfer_*` / `bank_approve` /
  `bank_reject` (where the new balance is unknown until the next
  render), and
- a `<host>:<port>` fragment that flips to `direct` when the CLI is
  run with `--direct`.

On `menu()` entry the tool calls `bbsengine6.io.screen.init()` once
per process (mirroring the `_screen_initialized` flag pattern in
`bbsengine6.ed.common.ui`) so the scroll region — top/bottom margins
— is set up before any `setbottombar()` call lands; without it the
bottom row would scroll off the visible area when the user types
past the bottom of the screen. On exit the `finally` block emits an
`io.echo()` carrying the `{savecursor}{curpos:{height},0}{el}{reset}
{restorecursor}` escape sequence (the same cleanup sequence used by
`empyre/__main__.py`) so the bottom row is erased and the cursor is
restored to where it was when `menu()` was entered.

Fragments are registered on `menu()` entry and unregistered in a
`finally` block so the registry stays clean across `KeyboardInterrupt`
and `EOFError`. New tests in `test_bank_tool.py`
(`TestBankBottombarFragments`, `TestBankBalanceCacheWiring`,
`TestBankMenuBottombarLifecycle`) cover both fragment callables, the
balance cache + dirty-flag flow on every `bank_*` op, the
register/unregister/setbottombar lifecycle, and the once-per-process
screen-init + cleanup-echo behavior.

### bed: fix inverted install dependency (`sudo -u zoid6`, shared venv)

`bed/Makefile` was running `sudo -u zoid6 …` and installing into
`/var/lib/zoid6/venv`, but bed is the foundational daemon and must
not depend on zoid6. The dep direction is `zoid6 → bed`, not
`bed → zoid6`.

- `bed/Makefile` `VENV_DIR`, `VENV_OWNER`, `VENV_GROUP` reverted
  to `bed` (i.e. `/var/lib/bed/venv`, owner `bed:bed`).
  `VENV_SHARED` removed.
- `bed/Makefile` `uninstall-venv` no longer has the
  `VENV_FORCE_UNINSTALL` guard; per-service venv ownership means
  removing `/var/lib/bed/venv` only affects bed.
- `deploytool/src/deploytool/lib.py:57` `VENV_LAYOUT["bed"]` updated
  to `/var/lib/bed/venv`. (Other zoid6-venv consumers — `zoid6`,
  `casino`, `empyre`, `murdermotel`, `mistermcfeely`, `bbsengine6`,
  etc. — still own `/var/lib/zoid6/venv` legitimately.)
- `zoid6/src/Makefile` `install-venv` now also builds and installs
  the bed wheel, so `import bed` keeps working from inside the
  zoid6 daemon.
- `bed/FHS.md`, `bed/README.md` updated to reflect the corrected
  per-service topology.
- `bed/src/bed/tests/test_bed.py` `test_moniker_auth_router_resolves`
  rewritten as `test_external_router_resolves` using
  `bbsengine6.net.defaultrouter.DefaultRouter` (a bed dependency)
  instead of `zoid6.api.handler.MonikerAuthRouter`. The test no
  longer requires zoid6 to be installed.
- `bed/src/bed/main.py` / `lib.py` docstrings and argparse help
  text scrubbed of zoid6 references; examples use generic names.

Reverts the design intent of commits `1b3027e` (shared-venv switch)
and `90ea05a` (VENV_FORCE_UNINSTALL guard); preserves their history.

### bed: add `commit-version` target; factory: disable `autorestart`, enable `message_service`

- `Makefile`: new `commit-version` target that `git add`s the freshly
  stamped `src/bed/_version.py` and commits it with a generated
  subject (`bed: bump version to <ver> (githash <hash>)`).
- `usr/share/factory/etc/bed/bed.json`: `autorestart` is now `false`
  so systemd owns the restart loop. `message_service.enabled` is
  now `true` so the in-process MessageService is wired by default.
- The systemd unit is unchanged; operators relying on its default
  autorestart behaviour should re-check their environment.

### bed: `make install` runs `version` and rebuilds with `--no-cache-dir`

- `Makefile` `install` now depends on `version` (closes a gap where
  `bed --version` reported a stale date if the operator invoked
  `install` without first running `make version`).
- The venv rebuild step now passes `--no-cache-dir` so wheel reuse
  from a previous build can never silently regress the install.

### bed: add `SPEC.md` entry-point spec and link from `README.md`

Adds [`SPEC.md`](SPEC.md) as the canonical entry point for
understanding bed: v1 status, what doesn't work yet, the phase gates
beyond v1, the bed→bbsengine6 migration map, and the bbsengine6-side
prerequisites for each bed service. `README.md` now opens with a
pointer to `SPEC.md` and explains the role of each doc
(`SPEC.md`, `TODO.md`, `docs/BED_AUTH.md`, `CHANGELOG.md`,
`FHS.md`).

### bed: native `BankService` (parallels `MessageService`)

Adds a bed-native server-side bank handler and a high-level client
wrapper, both modelled on the `MessageService` /
`BedMessageServiceClient` pair. `bed.api.bank.BankService` registers
four wire types (`bank_balance` / `bank_add` / `bank_remove` /
`bank_history`) against the WebSocket server and delegates to the
existing `bbsengine6.bank.BankService` for the actual ledger work.
`bed.client.bankservice.BedBankServiceClient` is the matching
high-level convenience client (`get_balance` / `add_funds` /
`remove_funds` / `get_history`) with the same soft-failure envelope
shape as the message-service client.

- New file `bed/api/bank.py` — `BankService` class with lazy
  bbsengine6.bank construction, `missing_moniker` /
  `invalid_amount` / `database_error` error envelopes, empyre wire
  shape (`{type: bank_*, moniker, amount/balance/transactions}`).
- New file `bed/client/bankservice.py` — `BedBankServiceClient` +
  `get_bank_client` / `reset_bank_client` singleton helpers.
- `bed.api.__init__` re-exports `BankService`; `bed.client.__init__`
  re-exports `BedBankServiceClient` and the helpers.
- `bed.main.BED.start()` auto-registers `BankService` after the
  message service; opt out with `--no-bank-service`. `_BED_DEFAULTS`
  tracks the new default so SIGHUP reload detection stays consistent.
- New tests `bed/src/bed/tests/test_bank_service.py` (31 tests
  covering registration, every handle_* path, missing-moniker /
  invalid-amount envelopes, lazy bbsengine6 construction, and the
  client wrappers + singletons).

### bed: Phase 1-6 hardening (multi-phase plan)

Cumulative correctness + lifecycle + asyncio + refactor + packaging +
test-hardening pass. Six commits in this repo (`42bc741`, `67dabbf`,
`c5b59c2`, `249df06`, `16883e0`) plus two in `zoid6/` (`3621d4e`,
`df70299`).

#### Phase 1 — API correctness fixes (`42bc741`)

- `bed.api.auth`: `expires_at` is read from the rotated record instead
  of being recomputed; token rotation in `_handle_reconnect` and
  `_handle_auth_refresh` now rolls back on persist failure; the
  revoke envelope includes a `recoverable` flag; tokens carry a
  `version: 1` claim enforced on decode.
- `bed.api.token_store`: `DBTokenStore.gc_expired(now=None)` honours
  the `now` argument via `to_timestamp(%s)`.
- `bed.api.secret`: `os.fchmod(fd, 0o600)` is called on the open fd
  before close (umask cannot leak perms). The v1→v2 upgrade logs a
  warning and preserves the v1 file on write failure.

#### Phase 2 — daemon lifecycle hardening (`67dabbf`)

- `bed.main`: DB connection failure now raises (was silent `return`).
  `_session_registry` + auth are constructed BEFORE `WebSocketServer`
  and `_cleanup_partial_start` is invoked on any failure in
  `start()`. Generic `_apply_config_section` / `_diff_config_section`
  helpers consolidate the per-section apply logic.
- `bed.main`: SIGHUP actually applies live knobs (`token_ttl`,
  `autorestart`/`restart_delay`/`max_restarts`) and warns (no apply)
  on structural changes (`bind.*`, `database.*`, `token_persistence`,
  `credential_provider`, `bed_secret_path`, `bed_instance_id`).
- `bed.main`: signal-handler race fix — the running bed is reachable
  via a one-element list so the handler never sees `None`.

#### Phase 3 — message service + client asyncio (`c5b59c2`)

- `bed.api.message`: `import psycopg` and `make_dsn` hoisted to
  module top; new idempotent `_close_async_conn()` helper (no more
  double-close in `stop_listener` + `_listen_loop`'s `finally`); the
  broad `except Exception` is narrowed to `(psycopg.Error, OSError)`
  so `CancelledError` propagates cleanly.
- `bed.client.connection`: `asyncio.get_event_loop()` replaced with
  `get_running_loop()` in `_recv_match` and `_dispatch_push`
  (Python 3.10+ friendly).
- `bed.client.__init__`: private `_RequestId` and
  `_expected_result_type` no longer leak through the public API.
- `bed.client.singleton`: cache keyed by `weakref.ref(args)` instead
  of `id(args)` to avoid GC-reuse aliasing.
- `bed.client.messageservice`: unused `get_event_loop()` /
  `running_loop` removed; `_push` and `set_local_unread_count` errors
  are swallowed (best-effort local cache).

#### Phase 4 — zoid6 refactor onto bed's provider (`249df06` bed, `3621d4e` zoid6)

- `bed.api.credential_provider.MonikerOnlyCredentialProvider`: now
  re-raises `ValueError` (still swallows generic exceptions) so callers
  can map invalid monikers to distinct wire responses.
- `zoid6.api.monikerrouter.MonikerAuthRouter`: delegates auth to
  bed's provider via an injectable `provider=` constructor arg.
  `register_all` registers only `["ping"]` (bed's AuthService owns
  `auth`). `_handle_auth` is kept as a documented private helper that
  preserves the historical wire-level distinctions.
- `zoid6.api.handler`: `print(...)` → `io.echo(..., level=...)` for
  module-load failures. New `_validate_config` rejects non-dict
  configs at startup so misconfiguration fails loudly.

#### Phase 5 — packaging consolidation (`df70299` zoid6)

- `zoid6/src/Makefile` `version` target now writes `__githash__` and
  `__datestamp__` (matching `bed`, `bbsengine5`, `bbsengine6`)
  instead of the unprefixed `githash`/`datestamp` fields.
- `zoid6/src/zoid6/_version.py`: regenerated by the new target.

#### Phase 6 — test hardening (`16883e0`)

- `bed.api.message._dispatch_notification`: rejects payloads that
  decode to non-dict JSON (null, arrays, scalars) instead of crashing
  on `.get()`. Logged + dropped.
- New `TestDecodeTokenFuzz`: empty/garbage/bytes inputs and
  wrong-secret tokens must all raise `TokenError`.
- New `TestSecretFilePathSafety`: umask-safe 0600 mode and atomic
  symlink replacement semantics.
- New `TestDispatchNotificationFuzz`: a battery of adversarial
  NOTIFY payloads (empty, null, arrays, non-string moniker, raw
  garbage, oversized junk) that must all be dropped without raising.

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
