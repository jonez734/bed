# TODO — bed (BBS Engine Daemon)

Live work only. Closed sections have moved to the relevant spec
under `specs/` (echo → `specs/echo.md`, menu → `specs/menu.md`,
help → `specs/help.md`, key_f2 → `specs/key_f2.md`, sink →
`specs/sink.md`, postoffice → `specs/postoffice.md`, message
service 9-phase plan → `specs/message.md` §13).

Cross-repo file:line call-outs have been stripped. Each
relationship is now a one-line pointer at the consumer repo.

---

## `_apply_auth_config` overwrites CLI `--bed-secret` with literal `~`

### Problem (summary)

`bed/src/bed/main.py:_apply_auth_config` uses an
`args.x == defaults["x"]` pattern to detect "user did not pass
this flag". The detection **fails** when the user's CLI value
happens to expand to the same path as the default — the JSON's
literal `~` overwrites the CLI override. Same bug class lives in
`_apply_bind_config` and `_apply_database_config`.

The 2026-06-28 bed+casino bring-up hit this with:

```
PermissionError: '/home/opencode/data/work/~/.config/bed/.bed-secret-3sy3k2_c'
```

### Fix (Option D — tilde-expand the JSON value)

In `_apply_auth_config` / `_apply_bind_config` /
`_apply_database_config`, normalize the JSON value the same way
the argparse default is normalized, so the two sides of the
comparison are in the same form. Two-line change per call site.

### Tasks

- [ ] Apply Option D to `_apply_auth_config` (wrap
      `auth["bed_secret_path"]` in `os.path.expanduser(...)`
      before assigning).
- [ ] Apply the same fix to `_apply_bind_config` (expand
      `bind.host`) and `_apply_database_config` (expand
      `database.host`).
- [ ] Regression tests in `bed/src/bed/tests/test_bed.py::TestConfigFlag`:
      - `test_config_does_not_overwrite_explicit_bed_secret`
      - `test_config_expands_tilde_in_bed_secret_path`
      - `test_config_default_bed_secret_is_preserved`.
- [ ] Document the fix in `README.md` under "Configuration" —
      the `bed_secret_path` JSON value is tilde-expanded by bed
      at load time.

### Cross-references

- `bed/src/bed/main.py:_apply_auth_config`,
  `_apply_bind_config`, `_apply_database_config` — buggy
  functions.
- `bed/src/bed/lib.py` `_default_secret_path` — the default
  comparator.
- `zoid6/src/zoid6/data/bed.json` — the JSON carrying the
  literal `~` value today.

### Future

- Option C — sentinel-based explicit-set detection (cleaner,
  fixes the bug class for every flag at once). Deferred.

---

## SIGTERM graceful shutdown + SIGKILL fallback gap

### Why SIGTERM is the right primary signal

`SIGKILL` (signal 9) cannot be caught, blocked, or handled by any
process — the kernel terminates immediately, no Python or asyncio
code runs. `SIGTERM` (signal 15) is catchable, and `bed` already
handles it correctly at `bed/src/bed/main.py` signal handlers.
`kill <pid>` (no signal specified) sends SIGTERM by default.

### Cleanup concerns SIGKILL skips

1. **Pidfile removal.** A SIGKILL'd daemon leaves the pidfile
   orphaned. The next start must detect the stale pid (via
   `kill -0`) and either remove it or refuse to start.
2. **WebSocket connection cleanup.** Active clients see a TCP
   reset on SIGKILL. In-flight `request_id` futures are abandoned.
3. **Database connection close.** The psycopg pool detects dead
   connections on next use and recycles them.
4. **In-memory auth tokens** (`--token-persistence=memory`). Every
   issued token is lost on SIGKILL. Every client must re-`auth`.

### Tasks

- [ ] **Stale-pidfile detection** at startup (Option A): `kill -0`
      check at the top of `main_async`; `O_EXCL` on the pidfile
      open so a racing second start errors out instead of
      overwriting.
- [ ] **Process-group cleanup for `bed --foreground`** (Option C):
      `bed/tests/scripts/stop_bed.sh` sends SIGTERM to the bed
      pid, waits 5s for graceful shutdown, falls back to SIGKILL
      on the process group.
- [ ] **Document `--token-persistence=db` as recommended
      production default** in `README.md` under the auth section
      (5-minute docs change).
- [ ] **Defer linger tuning** (Option B) to a future
      `## WebSocket socket options` section (its own change).

### Cross-references

- `bed/src/bed/main.py` signal handlers — graceful shutdown runs
  independently of the pidfile work; the pidfile removal is in
  the `finally` so it runs after the graceful stop completes.
- `bed/src/bed/daemon/bed.service` — `KillSignal=SIGTERM` +
  `TimeoutStopSec=30s`.
- `bed/tests/scripts/stop_bed.sh` — SIGTERM-then-SIGKILL test
  helper.
- `kill(1)` — without `-N`, sends SIGTERM. `kill -9` is SIGKILL;
  `kill -HUP` is SIGHUP (config reload).

---

## Sink infrastructure — bed-side

See `specs/sink.md` for the full design. The bbsengine6-side
prerequisites (Phases 0–5) are blocking; bed-side work starts
once those land. Per `handbook/ARCHITECTURE.md` §4, the pending
prerequisites are:

- `bbsengine6/io/sink.py` — `Sink` protocol, `DefaultSink`,
  `set_io_sink` / `reset_io_sink`.
- `bbsengine6/io/echo_render.py`.
- `bbsengine6/io/mci.py` — `mci.parse` / `mci.render`.
- `bbsengine6/io.echo` returns the rendered string.
- Sink-based variants for other primitives.
- `WebSocketServer.on_connect_hook`.

### Tasks (bed-side, after prerequisites land)

- [ ] `bed/sinks/bed_sink.py` — `BEDSink` class.
- [ ] `bed/client/io_sink.py` — `ThinClientIOSink`.
- [ ] `bed/main.py` — register `on_connect_hook` that installs
      the sink and owns the message loop.
- [ ] `bed/tests/test_bed_sink.py`,
      `bed/tests/test_bed_sink_on_connect.py`,
      `bed/tests/test_thin_client_io_sink.py`,
      `bed/tests/test_bed_sink_echo_render.py`,
      `bed/tests/test_bed_sink_mci.py`.

### Cross-references

- `specs/sink.md` — full design + phased plan.
- `handbook/ARCHITECTURE.md` §4 — bbsengine6-side prerequisites.
- `bbsengine6/TODO.md` — sink infrastructure phases.

---

## See also

- `SPEC.md` — entry-point spec, phase gates.
- `handbook/ARCHITECTURE.md` — dep graph, prerequisites.
- `handbook/BED_AUTH.md` — bearer-token protocol reference.
- `specs/` — per-service specs (each owns its own open
  follow-ups section).
- `CHANGELOG.md` — release history.
