# Changelog

All notable changes to `bed` (BBS Engine Daemon) are recorded here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

The first 14 entries below were replayed from the meta-repo where `bed/` lived
as a plain directory (submodule-less) before being extracted into its own
repository on 2026-06-27. Commits are listed in reverse chronological order;
short hashes are the ones from this repository's history.

## Unreleased

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
