# TODO — bed (BBS Engine Daemon)

## Bearer token: short-lived reconnect credentials for BED

### Problem
`bed` currently has only a stub `auth` (in `bbsengine6.net.defaultrouter.DefaultRouter` / `bed.api`). Real games (empyre, casino, mistermcfeely, murdermotel, zoid6) need to authenticate users with moniker+password, then let the client **reconnect** after a network blip or `bed` restart without re-asking for the password. A short-lived signed bearer token is the standard primitive.

### Goals
- One `auth` round-trip at login; reconnects use a token, not a password.
- Token lifecycle (issue, refresh, revoke, expire) is owned by **`bed`**, not by individual game routers.
- All game routers use the **same** `AuthService`; only the credential backend differs per game.
- Backward compatible: the existing stub `auth` keeps working for development and ping.
- Pure JSON wire format (matches the rest of BED's protocol).

### Token shape
- Opaque, URL-safe, 256 bits of entropy (`secrets.token_urlsafe(32)`).
- HMAC-SHA256 signed with a per-instance secret (new flag `--bed-secret PATH`; default `~/.config/bed/bed.secret`, auto-created mode 0600 on first run).
- Encoded claims: `moniker`, `issued_at`, `expires_at`, `session_id`, `is_sysop`, `bed_instance_id`.
- Default TTL: **900 seconds** (15 min, matches `bbsengine6.session.updatelastactivity`).
- Bound to the issuing `websocket_id`; reuse from a different socket requires `auth_refresh` (which rebinds).

### Wire protocol additions
```json
# Login
C→S {"type":"auth","moniker":"alice","password":"…"}
S→C {"type":"auth_result","success":true,"moniker":"alice","is_sysop":false,
     "session_id":"…","token":"…","expires_at":"2026-06-25T11:32:00Z"}

# Reconnect (no password)
C→S {"type":"reconnect","token":"…"}
S→C {"type":"reconnect_result","success":true,"session_id":"…",
     "token":"…","expires_at":"…"}

# Refresh (still-valid token → new token, fresh TTL)
C→S {"type":"auth_refresh","token":"…"}
S→C {"type":"auth_result","success":true,"token":"…","expires_at":"…"}

# Logout
C→S {"type":"auth_revoke","token":"…"}
S→C {"type":"auth_revoke_result","success":true}

# Errors
S→C {"type":"error","code":"token_expired","message":"…","recoverable":true}
S→C {"type":"error","code":"token_invalid","message":"…","recoverable":false}
S→C {"type":"error","code":"token_revoked","message":"…","recoverable":false}
S→C {"type":"error","code":"bed_instance_mismatch","message":"…","recoverable":false}
```

### Reconnect flow
1. Client detects socket close.
2. Client reads last good token from memory.
3. Client opens new socket; first message is `reconnect`.
4. Server validates signature, expiry, `bed_instance_id`.
5. On success: server rebinds the prior per-router session to the new `websocket_id`, sends `reconnect_result` with a fresh token, then **replays any in-flight IO request** (`request_id` is monotonic per session and survives reconnects).
6. On failure: server sends `error{code:…}` and closes; client falls back to `auth` (interactive: prompt for password; headless: fail).

### Storage
- **In-process** `Dict[token, TokenRecord]` keyed in `bed.api.auth.TokenStore` (default).
- **Optional DB persistence** via a new table — see "DB persistence" below.

### `bed` package changes
- [ ] Add `bed/api/auth.py` with `AuthService` (registers `auth`, `reconnect`, `auth_refresh`, `auth_revoke`, `logout`).
- [ ] Add `bed/api/token_store.py` with `TokenStore` (in-process dict + optional DB backend) and `TokenRecord` dataclass.
- [ ] Add `bed/api/credential_provider.py` defining the `CredentialProvider` protocol:
      `def authenticate(args, moniker, password) -> Optional[MemberInfo]`. Each game supplies one.
- [ ] Add `bed/api/errors.py` with the `error` envelope helpers and code constants (`token_expired`, `token_invalid`, `token_revoked`, `bed_instance_mismatch`).
- [ ] Extend `bed.main.parse_args` with:
  - `--bed-secret PATH` (default `~/.config/bed/bed.secret`, auto-create 0600 if missing).
  - `--token-ttl SECONDS` (default 900).
  - `--token-persistence {none,memory,db}` (default `memory`).
  - `--bed-instance-id STRING` (default: random UUID generated on first run, persisted in the secret file).
- [ ] Extend `bed.main.BED.start` to wire `AuthService` into the `WebSocketServer` before any game router is loaded, so `auth` is the first thing every new connection sees.
- [ ] Add a generic pending-request table in `bed.api.session` keyed by `session_id` with monotonic `request_id` counter; replay on reconnect.
- [ ] Keep the existing `bed.api.default.DefaultRouter._handle_auth` as a no-credential stub for development and `wscat` smoke tests.
- [ ] Document `zoid6.api.handler.MonikerAuthRouter` (in `zoid6/api/monikerrouter.py`) in `bed/README.md` and `--router` help as the next-step example: validates the moniker exists in the database via `bbsengine6.member.moniker_exists`; password still not checked.
- [ ] Document `AuthService` in `bed/README.md` and add `bed/docs/BED_AUTH.md` covering wire format, TTL knobs, secret rotation, and threat model.
- [ ] Add `bed/tests/test_auth_service.py`: issue/validate/expire/refresh/revoke/replay/cross-instance.
- [ ] Add `bed/tests/test_bed_token_persistence.py` for the `db` storage mode.

### DB persistence (opt-in, behind `--token-persistence=db`)
- [ ] Add `sql/bed_token.sql` (in `bed/data/` or the consuming game's schema): `engine.__bed_token(token, moniker, session_id, issued_at, expires_at, is_sysop, bed_instance_id, websocket_id)`.
- [ ] `TokenStore` DB backend: `INSERT` on issue, `SELECT` on validate, `DELETE` on revoke/expiry, periodic GC of expired rows.
- [ ] Housekeeping: `bed.api.token_store.gc_expired(args)` callable from a cron-style background task or `bed`'s own scheduler.
- [ ] DB persistence is **optional**. v1 default is in-memory; tokens are lost on `bed` restart and all clients must re-`auth`. This is the safer/cheaper default for single-node deployments.

### Security notes
- HMAC secret is per-instance; rotating it invalidates all outstanding tokens (acceptable: forces re-`auth`, doesn't leak credentials).
- `bed_instance_id` baked into the token prevents cross-instance token replay. If a user is load-balanced to a different `bed`, they must re-`auth`.
- Bind token to `websocket_id` at issue time; the `reconnect` flow explicitly rebinds.
- Tokens are never logged. `bed.api.auth` must scrub tokens from any debug/error output.
- `--bed-secret` file must be mode 0600; refuse to start if it's world-readable.

### Decisions
- [ ] v1 default: in-memory token store, 15-min TTL, HMAC-SHA256, token bound to `websocket_id`.
- [ ] v1 default: `bed_instance_id` mismatch → reject + force re-`auth`.
- [ ] v1 default: keep `DefaultRouter` stub `auth` working for development.
- [ ] v1 default: logout on TCP-reset does **not** invalidate the token (it stays valid for `token_ttl` so reconnects succeed). Explicit `auth_revoke` invalidates.
- [ ] Future: shared-secret across `bed` instances behind a load balancer (out of scope for v1).

### `bed.json` configuration keys
- [ ] `auth.bed_secret_path` (default `~/.config/bed/bed.secret`, auto-create 0600 on first run).
- [ ] `auth.token_ttl` (default 900 seconds; matches `bbsengine6.session.updatelastactivity`'s 15-min window).
- [ ] `auth.token_persistence` (`none` | `memory` | `db`; default `memory`).
- [ ] `auth.bed_instance_id` (default: random UUID generated on first run, persisted in the secret file).
- [ ] CLI equivalents: `--bed-secret PATH`, `--token-ttl SECONDS`, `--token-persistence MODE`.

---

## `echo` and `echo_ack` — generic push-based text channel

### Why `bed` owns this, not individual game routers
Every BED-hosted game (empyre, casino, mistermcfeely, murdermotel, zoid6) needs a
way to push a render fragment to the connected client and to know that the
fragment was displayed before continuing (e.g. before issuing the next IO
request). If each game rolls its own `echo`/`echo_ack` pair, the wire shapes
will drift and BED's transport will end up carrying N slightly-different
protocols. Owning the pair in `bed` gives every game:
- a stable, documented fragment envelope,
- a uniform backpressure / flow-control primitive (`echo_ack`),
- free transport-level framing (chunking, ordering, reconnect-resume),
- one place to add tracing, metrics, and rate limiting.

`bed` defines the **envelope and transport contract**. Games define the
**content schema** (`text`, `style`, `style_color`, `mci`, etc.) inside
`echo.payload`.

### Wire shape
```json
# Server → client: push one render fragment (or a batch via echo_batch)
S→C {"type":"echo",
     "request_id":"r42",
     "stream":"main",
     "seq":17,
     "payload":{
       "text":"Welcome to Empyre.\n",
       "style":{"fg":"white","bg":"black","bold":false,"underline":false},
       "mci":{"code":"{f6}","args":{}}},
     "flush":true,
     "ts":"2026-06-25T11:30:01.123Z"}

# Server → client: a batch of fragments sharing one request_id (chunking)
S→C {"type":"echo_batch",
     "request_id":"r42",
     "stream":"main",
     "seq_start":17,
     "fragments":[
        {"seq":17,"text":"Welcome "},
        {"seq":18,"text":"to "},
        {"seq":19,"text":"Empyre.\n","flush":true}]}

# Client → server: I rendered the fragment / batch
C→S {"type":"echo_ack",
     "request_id":"r42",
     "last_seq":19,
     "rendered_at":"2026-06-25T11:30:01.456Z"}

# Client → server: I can't render this fragment (e.g. unknown MCI code)
C→S {"type":"echo_nack",
     "request_id":"r42",
     "last_seq":18,
     "reason":"unknown_mci_code",
     "detail":"code={f99}"}

# Server → client: cancel a pending echo (e.g. menu redraw replaced it)
S→C {"type":"echo_cancel",
     "request_id":"r42",
     "reason":"superseded"}
```

### Semantics
- **At-least-once, in-order delivery.** `seq` is monotonic per `stream` per
  session. `request_id` is monotonic per session (see `bed.api.session` in the
  bearer-token plan above — the same `request_id` counter is reused here).
- **One outstanding `echo_ack` per session.** The server may have multiple
  `echo`s in flight across streams (`main`, `bottombar`, `statusline`) but a
  single client only ever owes one `echo_ack` for the most-recently-pushed
  fragment.
- **`flush:true` means "I'm about to ask the client for input next".** The
  client must render every prior `seq` for that stream before sending
  `echo_ack`. `flush:false` means "more fragments coming, no need to ack yet,
  but feel free to render streaming".
- **Reconnect resume.** On `reconnect`, the server replays any unacked
  `echo`/`echo_batch` from the persistent request table (see bearer-token
  plan: pending-request table keyed by `session_id`). The client renders the
  replay, then sends a single `echo_ack` with the highest `seq` it actually
  showed. Server resumes from `last_seq + 1`.
- **Cancellation.** When the game replaces a screen (e.g. menu redraw, the
  player hits `^C`, or a state transition fires), the server sends
  `echo_cancel` for any in-flight `request_id` that is no longer relevant.
  The client must drop those fragments and may send `echo_ack{last_seq: <prior
  visible seq>}` to confirm the cancellation point.

### Streams
A session has up to three named render streams, each with its own `seq`:
- `main` — the primary game UI (menus, listboxes, prompts).
- `bottombar` — the BBS-style status bar (registered fragments in
  `bbsengine6.io.screen`). Pushed by `setbottombar` / `register_*` calls.
- `statusline` — optional, for in-game top-of-screen status (turn count,
  bank balance, unread mail). Pushed by empyre's `lib.init` bottom-bar-style
  fragments if/when migrated.

Each stream is independent: `main` can be paused waiting on an
`inputchoice_reply` while `bottombar` continues to receive updates (turn
count ticking, new mail arriving, etc.).

### `bed` package additions
- [ ] Add `bed/api/echo.py` with `EchoService` (registers `echo`, `echo_batch`,
      `echo_ack`, `echo_nack`, `echo_cancel`).
- [ ] Add `bed/api/fragment.py` with `Fragment` dataclass
      (`request_id`, `stream`, `seq`, `text`, `style`, `mci`, `flush`, `ts`)
      and `FragmentQueue` (per-session, per-stream ordered queue with
      `last_acked_seq` cursor).
- [ ] Add `bed/api/style.py` defining the canonical style schema
      (`fg`, `bg`, `bold`, `underline`, `inverse`, `blink`, palette indices)
      and an MCI codec stub that round-trips the legacy `{f6}` / `{labelcolor}`
      tokens used by `bbsengine6.io.echo`.
- [ ] Extend `bed.api.session` to add a per-session monotonic `request_id`
      counter and a per-session `pending_ack` future (shared with the
      bearer-token plan's pending-request table).
- [ ] Extend `bed.main.BED.start` to register `EchoService` after `AuthService`
      and before any game router is loaded, so the first fragment after
      `auth_result` is an `echo` (e.g. login banner) — not a game-specific
      frame.
- [ ] Document `EchoService` in `bed/docs/BED_ECHO.md` and add a
      `bed/protocol/ECHO.md` reference page.
- [ ] Add `bed/tests/test_echo_service.py`: in-order delivery, batch chunking,
      `flush` semantics, `echo_cancel` supersession, reconnect-resume from
      `last_seq`, `echo_nack` handling, multi-stream independence.
- [ ] Add `bed/tests/test_echo_mci_roundtrip.py` for the MCI codec
      (parse `{f6}` / `{labelcolor}` / `{var:valuecolor}` into structured
      style, and back).

### `echo_ack` backpressure rules
- The server **may** issue IO requests (`inputstring`, `inputchoice`, etc.)
  only after the matching `echo_ack` arrives for the most-recent
  `flush:true` echo. This guarantees the client has rendered the prompt
  before the server blocks on input.
- The server **may** push `flush:false` echoes as fast as it likes; the
  client acks them at its own cadence (e.g. on natural render boundaries
  every N fragments, or on a 50ms timer).
- The client **may** send a single `echo_ack` for a batch by setting
  `last_seq` to the highest `seq` it actually rendered. The server
  treats every `seq` ≤ `last_seq` as acked.
- If the server times out waiting for `echo_ack` (configurable, default
  30s), it sends `echo_cancel{reason:"ack_timeout"}` and proceeds with an
  error envelope to the client.

### Style / MCI compatibility
- The `payload.style` field is the **canonical** form. `payload.mci` is a
  **legacy escape hatch** for fragments that originate from a `bbsengine6.io`
  shim and haven't been transcoded yet.
- The MCI codec MUST be a strict superset of `bbsengine6.io.echo`'s
  tokenizer — i.e. anything that works in the door mode of empyre must
  render identically in a thin client that uses the same `style` schema.
- v1 default: the thin client renders `text` only and ignores `style` /
  `mci`; the server is responsible for any pre-transcoding it wants to do
  (e.g. the headless test client asserts the raw `text` and a separate
  TUI client applies the `style` field).

### Decisions
- [ ] v1 default: at-least-once delivery with reconnect-resume; in-order per
      stream; one outstanding `flush:true` per session.
- [ ] v1 default: `echo` and `echo_ack` are mandatory on every connection —
      no game may use a different text-push primitive.
- [ ] v1 default: `flush:false` echoes may be dropped by the server under
      memory pressure (low watermark); `flush:true` echoes are always
      delivered. The server sends `echo_cancel{reason:"dropped"}` for any
      dropped non-flush fragment.
- [ ] v1 default: `payload.mci` round-trips through the codec; future
      versions may require structured `payload.style` only.
- [ ] v1 default: 30s default `ack_timeout`, configurable via
      `--echo-ack-timeout SECONDS` in `bed.main.parse_args`.
- [ ] `bed.json` key: `echo.ack_timeout` (default 30 seconds; CLI flag
      `--echo-ack-timeout` overrides).
- [ ] Future: per-game style palettes (so empyre can use its own colour
      scheme without redefining the wire shape).

### Adoption
- [ ] empyre: adopt `EchoService` as the first vertical slice of the
      thin-client BED conversion (Phase 1 of `empyre/TODO.md`).
- [ ] casino: adopt `EchoService` for lobby chat and table-event pushes.
- [ ] mistermcfeely (postoffice): adopt `EchoService` for "new mail"
        notifications and folder rendering.
- [ ] murdermotel: adopt `EchoService` for narrative pushes and
        status-line updates.
- [ ] zoid6: adopt `EchoService` for dashboard tiles and shared-wallet
        notifications.
- [ ] bbsengine6 bank service: adopt `EchoService` for transaction
        notifications.

---

## Games and apps that will benefit from `bed`'s bearer token

When `bed`'s `AuthService` is implemented, the following games/apps in the monorepo
should adopt it. Each entry links to that project's TODO where a cross-reference
note has been added.

| App | Repo / path | Benefits from bearer token |
|---|---|---|
| **empyre** | `/home/opencode/data/work/empyre` | **Primary driver.** Thin-client BED conversion plan already references `AuthService` in `empyre/TODO.md` Phase 0a. Reconnect across network blips and `bed` restarts is critical for long empyre sessions (turns can take minutes). |
| **casino** | `/home/opencode/data/work/casino` | **Strong fit.** Lobby browsing, spectator mode, multi-table clients, bot accounts. See `casino/TODO.md` for the cross-reference note. |
| **mistermcfeely** | `/home/opencode/data/work/mistermcfeely` | **Strong fit.** Token-bounded IMAP-style sessions, mail-client reconnection without re-entering IMAP password. See `mistermcfeely/TODO.md`. |
| **murdermotel** | `/home/opencode/data/work/murdermotel` | **Strong fit.** Long-lived investigation/investment sessions; reconnect mid-night without re-login. See `murdermotel/TODO.md`. |
| **zoid6** | `/home/opencode/data/work/zoid6` | **Strong fit.** Dashboard / shared-wallet / chat clients that stay open for hours. See `zoid6/TODO.md`. |
| **achilles** | `/home/opencode/data/work/achilles` | No TODO.md; will benefit once the game grows a network surface. Add cross-reference when one is created. |
| **vulcan** | `/home/opencode/data/work/vulcan` | Same — note added when TODO.md is created. |
| **socrates** | `/home/opencode/data/work/socrates` | Same. |
| **letteredolive** | `/home/opencode/data/work/letteredolive` | Same. |
| **moneyday** | `/home/opencode/data/work/moneyday` | Same. |
| **rgs** | `/home/opencode/data/work/rgs` | Same. |
| **mhc** | `/home/opencode/data/work/mhc` | Same. |
| **teos** | `/home/opencode/data/work/teos` | Same. |
| **zoidoffice** | `/home/opencode/data/work/zoidoffice` | Same. |
| **postoffice** (lives inside mistermcfeely) | `/home/opencode/data/work/mistermcfeely` | **Strong fit.** Mail client holds a token instead of an IMAP password. Covered by mistermcfeely's TODO. |
| **bank service** (`bbsengine6.bank.api.handler.MessageRouter`) | `/home/opencode/data/work/bbsengine6` | **Strong fit.** Financial clients should not re-send credentials on reconnect. Tracked in `bbsengine6/TODO.md` Phase 7.2.1 (AccountService + LedgerService). |
| **BBS door mode (legacy TUI)** | n/a | Not affected — door mode is host-driven, no token involved. |

### Cross-reference convention
When `bed`'s `AuthService` is implemented, each of the four game repos with a
`TODO.md` (casino, mistermcfeely, murdermotel, zoid6) gets a one-line note:

> See `bed/TODO.md` "Bearer token" — adopt `bed.api.auth.AuthService` for BED-mode
> authentication and reconnect. Replaces per-game `auth` implementations.

This note has been added to: empyre (Phase 0a already references it), casino,
mistermcfeely, murdermotel, zoid6. See individual TODOs for the wording.

---

## `menu` — single-pick option list, server-side hotkeys

### Why `bed` owns this
The casino menu (Blackjack "Hit / Stand / Double / Split", Poker "Fold /
Check / Call / Raise", Roulette "Inside / Outside / …"), most empyre
sub-menus (Town "Bank / Train / Tax / …", Combat "Attack / Spy / Diplomat
/ …"), and the murdermotel lobby / playground / rabidwolf menus all want
the same primitive: **show a labelled list of options with one keystroke
per option, get the pick back, and let the server define the hotkeys.**
A generic `menu` message type sits between the low-level `inputchoice`
(which is one keystroke with no menu layout) and a full `listbox` (which
supports paging, cursors, multi-column rendering, KEY_INSERT, etc.). The
casino and murdermotel menus do not need any of that listbox machinery;
they are flat, single-pick, hotkey-driven.

Owning `menu` in `bed` gives every game:
- one canonical wire shape for "display this option list, get one pick",
- one place to handle `KEY_ENTER` / unknown-key / `noneok` /
  `rewriteprompt` semantics the same way door mode does,
- one place to hook into the shared `help` (F1) and `key_f2`
  (session-level) message types,
- one place to add tracing, metrics, and rate limiting.

### `F1` and `F2` are NOT part of the `menu` envelope

`bbsengine6.io.inputchoice` accepts a `help=<callable>` kwarg (F1) and
a `f2_handler=<callable>` kwarg (F2). Neither of these belongs on the
`menu` envelope:

- **`F1` (`help`)** is per-menu. It is pulled out into the `help` /
  `help_result` / `help_error` message types (see "Help on demand (F1)"
  below). The client pulls help text on demand by sending `help{
  request_id, sub_request_id }`; the server invokes the callable
  on-demand and ships the rendered text in `help_result`. This keeps
  `menu` envelopes small (no help text shipped by default) and lets
  the help text reflect live state at the moment `F1` is pressed.

- **`F2` is NOT a per-menu help callback.** In this monorepo, `F2` is
  the **session-level** "list new messages from subscribed channels"
  key — analogous to a mail-client inbox refresh. It is not bound to
  any particular `menu`; it is bound to the session. `F2` is pulled
  out into the `key_f2` / `key_f2_result` / `key_f2_empty` /
  `key_f2_error` message types (see the `## key_f2` section below).
  The `inputchoice` `f2_handler` kwarg is **not** projected to the
  wire at all; no game in this monorepo actually passes `f2_handler=`
  to `inputchoice`, so dropping it loses nothing.

The thin client's `KEY_F1` handler sends `help{request_id, sub_request_id}`;
its `KEY_F2` handler sends `key_f2`. Two independent code paths, two
independent server-side services, no shared envelope.

### Argument compatibility: `menu` is the wire form of `bbsengine6.io.inputchoice()`

The real `bbsengine6.io.inputchoice` signature (in
`bbsengine6.io.inputchoice.py`) is:

```python
def inputchoice(
    prompt: str,
    options: str,                # a string of valid single-char hotkeys, e.g. "HSDPQ"
    default: str | None = "",
    **kwargs,                    # only: noneok, help, f2_handler, rewriteprompt
) -> str | None
```

The `menu` envelope is a **1:1 projection** of the *positional and
unconditional* parts of this signature. The two kwarg-only parts (`help`
and `f2_handler`) are pulled out into their own message types; the
remaining kwargs (`noneok`, `rewriteprompt`) stay on the envelope.

| Envelope field | `inputchoice` parameter | Notes |
|---|---|---|
| `prompt` | `prompt` | The literal prompt string. The server is responsible for formatting it (banner + `[HSDPQ]` + `(S)` markers); `rewriteprompt` only does a one-shot substitution (see below). |
| `options` | `options` | A **string** of valid single-character hotkeys. The client uppercases the keystroke and tests `ch in options`. Non-matches ring the bell and re-prompt. |
| `default` | `default` | Uppercased. The hotkey returned on `KEY_ENTER` (or on `menu_timeout` server-side). |
| `noneok` | `noneok` (kwarg) | Boolean. If `true`, `KEY_ENTER` returns `noneok_picked:true` instead of `default`. |
| `rewriteprompt` | `rewriteprompt` (kwarg) | Boolean. If `true`, the client does the one-shot `[HSDPQ] → [(H)SDPQ]` substitution. |
| ~~`help`~~ | `help` (kwarg) | **NOT on the menu envelope.** Pulled out into the `help` / `help_result` / `help_error` message types. See "Help on demand (F1)" below. |
| ~~`f2_handler`~~ | `f2_handler` (kwarg) | **NOT on the menu envelope, NOT on the wire at all.** `F2` is a session-level key; see the `## key_f2` section below. |

Anything not in this table is **out of scope** for the `menu` envelope.
There is no `enabled`, no per-option `style`, no per-option `hint`, no
`[Q]uit` / `[X]it` / `[B]ack` auto-convention, no `ESC`/`^C` binding.
Door-mode `inputchoice` has none of those things; `menu` matches door
mode exactly.

### Wire shape (v1)
```json
# Server → client: present a menu and wait for a single keystroke
S→C {"type":"menu",
     "request_id":"r100",
     "prompt":"{var:promptcolor}Blackjack — Hand #42 — Your move [{HSDPQ}] ({S}): {var:inputcolor}",
     "options":"HSDPQ",
     "default":"S",
     "noneok":false,
     "rewriteprompt":false,
     "timeout":60,
     "ts":"2026-06-25T11:31:00.000Z"}

# Client → server: the user picked a hotkey in `options`
C→S {"type":"menu_reply",
     "request_id":"r100",
     "hotkey":"H"}

# Client → server: the user hit Enter on a `noneok=true` menu
C→S {"type":"menu_reply",
     "request_id":"r100",
     "noneok_picked":true}

# Server → client: the menu timed out — informational; the client does
# NOT need to ack. The server has already resolved the future as if the
# user had picked `default` (or `noneok_picked` if noneok=true, or
# cancelled if default is empty).
S→C {"type":"menu_timeout",
     "request_id":"r100",
     "hotkey":"S"}

# Server → client: cancel a pending menu (e.g. round ended, player
# disconnected from the table, a higher-priority state transition
# fired). The client must drop it; a late `menu_reply` is a no-op.
S→C {"type":"menu_cancel",
     "request_id":"r100",
     "reason":"round_ended"}
```

### Semantics (mirrors `inputchoice` line-by-line)
- **One keystroke per pick.** The client renders the menu and waits for
  exactly one keystroke. On a keystroke:
  - `KEY_ENTER` →
    - if `noneok=true`: client sends `menu_reply{noneok_picked:true}`.
    - else if `default != ""`: client sends `menu_reply{hotkey:<default>}`.
    - else: client rings the bell and re-prompts; no `menu_reply` is sent.
  - `KEY_HELP` or `KEY_F1` → client sends `help{request_id, sub_request_id}`
    (see "Help on demand (F1)" below). The menu stays pending. No
    `menu_reply` is sent.
  - `KEY_F2` → client sends `key_f2` (session-level, see the
    `## key_f2` section below). The menu stays pending. No `menu_reply`
    is sent.
  - Other keys: client uppercases the keystroke. If `ch in options`,
    client sends `menu_reply{hotkey:<uppercased ch>}`. Otherwise, bell
    + re-prompt; no `menu_reply` is sent.
- **No `ESC` / `^C` handling.** `inputchoice` does not handle `ESC` or
  `^C`; `menu` matches that. The thin client does NOT send
  `cancelled:true` on `ESC` — that would be a protocol error. The only
  way the server ever sees a cancelled menu is via server-side timeout
  (`default == ""` and `noneok == false`) or via an explicit
  `menu_cancel` from the server itself.
- **Default on Enter / timeout.** The server treats `KEY_ENTER` and
  `menu_timeout` identically: resolve to `default` (or `noneok_picked`
  if `noneok=true`, or `cancelled` if `default == ""` and `noneok` is
  false). The thin client never resolves the future; the server does.
- **Server-side timeout.** `timeout` is enforced by the server via
  `asyncio.Timer` (BED-side), not by the thin client. The thin client
  treats `timeout` as a hint and never enforces it. If the server timer
  fires, the server sends `menu_timeout` and resolves the future as
  described above. A `menu_reply` that arrives after `menu_timeout` is a
  late reply; the server drops it with a `logentry` debug message.
- **Hotkey collisions are a server error.** The server MUST NOT ship a
  menu with two valid single-character hotkeys in `options`; the
  `MenuService` validates this on the server side and raises
  `DuplicateHotkeyError` at send time, surfacing as
  `error{code:"menu_duplicate_hotkey"}` to the client. This matches
  `inputchoice`'s `ch in options` test (line 56 of `inputchoice.py`):
  duplicate hotkeys would silently mask the second option.
- **Disabled options are not modelled.** `inputchoice` has no `enabled`
  concept. If a server wants to disable an option, it omits the hotkey
  from `options` and (if it cares) prints a hint banner as a separate
  `echo` frame before the `menu` envelope. The casino's
  "Double-down not available" / "Split only on a pair" hints are
  rendered as `echo` frames, not as part of the `menu` envelope.
- **Reconnect resume.** On `reconnect`, the server replays any unacked
  `menu` from the pending-request table (same table as `echo` /
  bearer-token plans). The client renders it, the user picks, and the
  reply is delivered to the in-memory future.
- **Cancellation.** The server may send `menu_cancel` to withdraw a
  pending menu. The client must drop it and NOT send a `menu_reply` for
  that `request_id` (the server treats a late `menu_reply` after
  `menu_cancel` as a protocol error and discards it).

### Help on demand (F1)

`F1` (a.k.a. `KEY_HELP`) is the per-menu help key. The `help` text is
**not** shipped in the `menu` envelope; it is pulled on demand via the
`help` round-trip. This is a deliberate bandwidth optimization: most
users never press `F1`, and the rendered help text (especially for
murdermotel's `playgroundhelp` / `lobbyhelp` / rabidwolf `help`
callables) can be 1–4 KB per menu. Shipping it eagerly would waste
bytes on every menu the user sees.

#### Wire shape
```json
# Client → server: I need the help text for menu request_id r100
C→S {"type":"help",
     "request_id":"r100",
     "sub_request_id":"r-help-7",
     "ts":"2026-06-25T11:31:42.000Z"}

# Server → client: here is the rendered help text
S→C {"type":"help_result",
     "request_id":"r100",
     "sub_request_id":"r-help-7",
     "text":"[G]ame instructions\n[C]redits\n[Q]uit",
     "rendered_at":"2026-06-25T11:31:42.123Z"}

# Server → client: the help request can't be served
S→C {"type":"help_error",
     "request_id":"r100",
     "sub_request_id":"r-help-7",
     "code":"menu_resolved" | "no_help" | "help_rate_limited",
     "message":"the menu for request_id r100 has already been answered"}
```

#### Semantics
- `help` is keyed by the outer `menu.request_id` plus a `sub_request_id`
  that is locally unique to the help exchange. The client may pipeline
  multiple help requests for the same outer `request_id` (each with a
  different `sub_request_id`); the server processes them in order.
- The server rejects `help` for a `request_id` that is not currently
  pending (the menu has already been answered, timed out, or
  cancelled) with `help_error{code:"menu_resolved"}`.
- The server rejects `help` for a `request_id` that has no help
  configured (the call site did not pass `help=<callable or string>`)
  with `help_error{code:"no_help"}`.
- The server rate-limits `help` per session (default 10 req/s,
  configurable via `bed.json` `help.rate_limit` and CLI flag
  `--help-rate-limit`); over-limit requests get
  `help_error{code:"help_rate_limited"}`.
- `help` does **not** resolve the menu's outer `request_id`. The user
  still has to press a hotkey (or `KEY_ENTER`) to answer the menu.
- The `MenuAdapter` invokes the `help=<callable>` **on demand** at
  `help` time, not eagerly at menu-send time. The callable is called
  server-side with the forwarded `**kwargs` and its `io.echo` output
  is captured into the `help_result.text` string. Staleness window
  is **zero** (this is strictly better than door mode, where the
  staleness window is non-zero in theory but effectively zero in
  practice because the player is blocked in `inputchoice`).
- A `help_request` that arrives after the menu has been resolved
  (timeout, cancel, or `menu_reply`) is a `help_error{code:"menu_resolved"}`.
  A late `help_request` is dropped with a `logentry` debug message.

### Style and layout
- v1 default: the thin client renders the `prompt` string verbatim. The
  server is responsible for any banner / hint / label rendering via
  `echo()` frames *before* the `menu` envelope. The `menu` envelope
  itself is "render the prompt, accept a keystroke in `options`,
  return the uppercased character or `default` on Enter."
- v1 default: the `rewriteprompt` field, when `true`, causes the client
  to do a one-shot `[HSDPQ] → [(H)SDPQ]` substitution on `prompt` (so
  the server can send `"… [HSDPQ]:"` and have the client render
  `"… [(H)SDPQ]:"` highlighting the default). This matches
  `inputchoice`'s `rewriteprompt=True` behavior exactly.
- The wire shape is **layout-agnostic** — a future web client may
  render the same envelope as a `<select>` or a list of buttons; the
  server doesn't care.

### `bed` package additions
- [ ] Add `bed/api/menu.py` with `MenuService` (registers `menu`,
      `menu_reply`, `menu_timeout`, `menu_cancel`).
- [ ] Add `bed/api/menu_validator.py` with `validate_menu(envelope)`
      enforcing: `options.isalpha()`,
      `len(options) == len(set(options.upper()))`,
      `default == "" or default.upper() in options.upper()`,
      `timeout >= 0`, `noneok in (true, false)`,
      `rewriteprompt in (true, false)`. No help or f2_handler
      validation (those are not on the envelope).
- [ ] Add `bed/api/menu_timeout.py` with the `asyncio.Timer`-based
      server-side timeout enforcement and the three resolution paths
      (default picked / noneok_picked / cancelled).
- [ ] Add `bed/api/help.py` with `HelpService` (registers `help`,
      `help_result`, `help_error`). Owns the per-outer-`request_id`
      help future; on `help` request, invokes the stashed callable
      on-demand and captures the `io.echo` output.
- [ ] Extend `bed.api.session` (the shared pending-request table from
      the bearer-token and echo plans) to track `menu` and `help`
      requests the same way: monotonic `request_id`, replay on
      reconnect, `cancel` on disconnect.
- [ ] Extend `bed.main.BED.start` to register `MenuService` and
      `HelpService` after `EchoService` and before any game router
      is loaded.
- [ ] Document `MenuService` in `bed/docs/BED_MENU.md` and `HelpService`
      in `bed/docs/BED_HELP.md`. Add `bed/protocol/MENU.md` and
      `bed/protocol/HELP.md` reference pages.
- [ ] Add `bed/tests/test_menu_service.py`: pick (each of the option
      hotkeys) / Enter-with-default / Enter-on-noneok / Enter-on-no-
      default-no-noneok (bell + re-prompt, no reply) / KEY_F1-sends-
      help (does NOT resolve menu) / KEY_F2-sends-key_f2 (does NOT
      resolve menu) / unknown-key (bell + re-prompt) / duplicate-
      hotkey error / server-side timeout-default / server-side
      timeout-noneok / server-side timeout-cancelled / late-reply
      after timeout is a no-op / reconnect-resume / `menu_cancel`
      after late `menu_reply` is a no-op.
- [ ] Add `bed/tests/test_menu_validator.py` for the static validator.
- [ ] Add `bed/tests/test_help_service.py`: pull help for a pending
      menu (string help) / pull help for a pending menu (callable
      help, invoked on demand) / error on resolved menu
      (`menu_resolved`) / error on no-help menu (`no_help`) / error
      on rate-limit (`help_rate_limited`) / pipelined help requests
      for distinct menus / pipelined help requests for the same menu
      (different `sub_request_id`s) / late `help` after `menu_cancel`
      is `menu_resolved`.
- [ ] Add `bed/tests/test_help_callable.py` for the murdermotel case:
      `playgroundhelp(**kwargs)` invoked at `help` time, captures
      `io.echo` output, ships as `help_result.text`. Assert callable
      output reflects LIVE state (mutate `player.weapons()` between
      menu-send and `help` request; assert new help text reflects
      the mutation).
- [ ] Add `bed/tests/test_menu_timeout_server_side.py` for the three
      `timeout` resolution paths and the late-reply no-op.

### Relationship to other primitives
- `menu` is the **wire form of `inputchoice`** (positional and
  unconditional kwargs only): same arguments, same semantics, same
  edge cases. A game's `MenuAdapter` builds a `menu` envelope by
  extracting the call-site's
  `inputchoice(prompt, options, default=..., **kwargs)` arguments.
  The kwargs the adapter forwards to the `menu` envelope are exactly
  the unconditional ones the real function accepts: `noneok`,
  `rewriteprompt`. The conditional kwargs (`help`, `f2_handler`) are
  pulled out: `help` becomes a server-side stashed callable (served
  on `help` round-trip), `f2_handler` is dropped (not used in this
  monorepo; `F2` is session-level).
- `menu` is **not** a substitute for `inputstring` (free text),
  `inputinteger` (numeric), `inputboolean` (yes/no), or `listbox`
  (paged, multi-column, editable). The casino still uses those for
  bet amounts, hand selection from a long list, etc.
- `menu` and `echo` are complementary: a `menu` is preceded by one or
  more `echo` frames (e.g. "Blackjack — Hand #42" banner + the
  visible hand) and is followed by more `echo` frames (the result of
  the pick). The `flush:true` echo just before the `menu` is the
  prompt cursor's anchor; the client MUST NOT send `menu_reply` before
  the matching `echo_ack`.
- `help` and `menu` are complementary: `help` is a sub-request of a
  pending `menu`; the client sends `help{request_id, sub_request_id}`
  while the menu is still pending, the server invokes the callable
  on demand, and the result is rendered then the menu re-prompts.
- `key_f2` and `menu` are independent: `key_f2` is a session-level
  query, not bound to any `menu`. The user can press `F2` at any
  time; the menu (if any) stays pending.

### Decisions
- [ ] v1 default: `options` is a single uppercase-string of valid
      single-character hotkeys (the server is responsible for the
      banner / hint / label rendering via `echo` frames before the
      `menu`).
- [ ] v1 default: `default` is the hotkey returned on `KEY_ENTER` and
      on `menu_timeout`. Matches `inputchoice`'s `default=` semantics
      exactly.
- [ ] v1 default: `noneok=true` + `KEY_ENTER` →
      `menu_reply{noneok_picked:true}`. Distinct from
      `menu_reply{hotkey:<default>}` so the server can log structured
      `logentry` lines.
- [ ] v1 default: the `menu` envelope does NOT include `help` or
      `f2_handler` fields. Help is pulled on demand via the `help`
      round-trip. `F2` is a session-level message type (see
      `## key_f2` below).
- [ ] v1 default: `help` invokes the callable **on demand** at
      request time (not eagerly at menu-send time). Staleness
      window is zero.
- [ ] v1 default: `help` is rate-limited per session at 10 req/s
      (configurable via `bed.json` `help.rate_limit` and CLI flag
      `--help-rate-limit`).
- [ ] v1 default: `timeout` is server-side enforcement via
      `asyncio.Timer`. The thin client never enforces timeouts. Default
      0 (no timeout) if omitted.
- [ ] v1 default: `rewriteprompt=true` causes the client to do the
      one-shot `[HSDPQ] → [(H)SDPQ]` substitution on `prompt`.
- [ ] v1 default: `menu` does NOT support multi-pick. A future
      `menu_multi` primitive may (e.g. casino's "select your lucky
      numbers"); for v1, a multi-pick UI uses `listbox` with
      `mode:"multi"`.
- [ ] v1 default: `menu_cancel` after a late `menu_reply` is a
      silent no-op on the server (the reply has already been
      delivered to the in-memory future). The server logs a
      `logentry` debug message with the `request_id` and the
      `late_reply_at` timestamp.
- [ ] v1 default: the thin client never sends `cancelled:true`. The
      only sources of cancellation are server-side timeout
      (`default == ""` and `noneok == false`) and explicit
      `menu_cancel` from the server.
- [ ] Future: per-game style palettes (so empyre can use its own
      colour scheme without redefining the wire shape).

### Adoption
- [ ] **casino** (primary driver): replace every
      `bbsengine6.io.inputchoice` call in
      `src/casino/games/blackjack/`, `src/casino/games/poker/`,
      `src/casino/games/roulette/`, `src/casino/lobby/`, and
      `src/casino/api/handler.py` with a `menu` envelope. The casino
      `MessageRouter` exposes a `casino_menu` service for non-menu
      consumers (bots, lobby clients) that wraps the same envelope.
      Casino's per-game `MenuAdapter` stashes `help=` server-side
      (most call sites pass a string `help=`; some pass nothing).
- [ ] **murdermotel** (second primary driver — `help` is a callable
      in three call sites: `lobby.py:74` `help=lobbyhelp`,
      `play.py:446` `help=playgroundhelp`, `rabidwolf.py:531`
      `help=help`). The murdermotel `MenuAdapter` stashes the
      callable + `kwargs` server-side. On `help` request, the
      `HelpService` invokes the callable **on demand** (not at
      menu-send time), captures its `io.echo` output, and ships
      `help_result.text`. See `murdermotel/TODO.md` "BED `menu`
      message type" for the per-call-site mapping.
- [ ] **empyre**: `Phase 2 — Router rework` in `empyre/TODO.md` adopts
      `menu` for the top-level menu (`I`nstructions / `M`aintenance /
      `N`ews / `P`lay / `T`own / `Y`our Status / `Q`uit) and the
      sub-menus in `town/`, `combat/`, `dock/`, `shipyard/`,
      `investments/`, etc. The `bbsengine6.io.inputchoice` calls in
      the empyre modules are swapped for `menu` envelopes via the
      IO shim in `empyre/io_bridge.py`. The shim's `inputchoice`
      wrapper extracts the unconditional kwargs (`noneok`,
      `rewriteprompt`) and forwards them as envelope fields; the
      `help` kwarg is stashed server-side; the `f2_handler` kwarg
      is filtered out (with a `logentry` warning if passed, since
      it's not used in this monorepo).
- [ ] **zoid6**: adopt `menu` for the dashboard "switch to casino /
      switch to empyre / check bank / log out" picker.
- [ ] **mistermcfeely (postoffice)**: adopt `menu` for the
      "read / reply / forward / delete / next" folder menu.
- [ ] **bbsengine6 bank service**: adopt `menu` for the
      "balance / deposit / withdraw / transfer / history" picker.
- [ ] External consumers (third-party clients, mobile apps): the
      `menu` envelope is the most consumer-friendly primitive; bots
      that drive games should prefer it over `inputchoice`.

---

## `key_f2` — session-level new-messages query

### Why this is its own message type
In this monorepo, `KEY_F2` is **not** a per-menu help callback. It is
the **session-level "list new messages from subscribed channels" key** —
analogous to a mail-client inbox refresh or a chat-client "new
messages" indicator. The user can press `F2` at any time (on the login
screen, mid-menu, mid-prompt, mid-input) and the server returns a list
of unread items across whatever channels / mailboxes / feeds the
member is subscribed to.

Because `F2` is session-level (not bound to any `menu` `request_id`),
it has its own message type and is not a field on the `menu` envelope.
The `inputchoice` `f2_handler` kwarg is **not** projected to the wire
at all; no game in this monorepo passes `f2_handler=` to
`inputchoice`.

### Wire shape
```json
# Client → server: I want the list of new messages
C→S {"type":"key_f2"}

# Server → client: here are the new messages (capped at
# bed.json key_f2.max_items, default 50; excess silently dropped
# with a "+N more" footer in `excess`)
S→C {"type":"key_f2_result",
     "count":3,
     "excess":0,
     "items":[
        {"channel":"postoffice:check_mail",
         "from":"sysop",
         "subject":"Welcome to the BBS",
         "date":"2026-06-25T10:00:00Z",
         "preview":"Welcome! …"},
        {"channel":"empyre:island:42",
         "from":"rex",
         "subject":"island discovered",
         "date":"2026-06-25T11:15:00Z",
         "preview":"…"},
        {"channel":"system:announcements",
         "from":"sysop",
         "subject":"maintenance window",
         "date":"2026-06-25T11:30:00Z",
         "preview":"…"}]}

# Server → client: nothing new
S→C {"type":"key_f2_empty",
     "count":0}

# Server → client: the query can't be served
S→C {"type":"key_f2_error",
     "code":"not_authenticated" | "channel_unavailable" | "key_f2_rate_limited",
     "message":"…"}
```

### Channel-source resolution
The server queries all channels the member is subscribed to, filtered
by per-channel `key_f2_visible: true | false` (column on
`engine.__channel`; default `true` for new channels). The default
behavior is **uniform across all games**: any subscribed channel with
`key_f2_visible=true` is included in the query.

A per-`bed.json` allow-list (`key_f2.channel_allow_list`) can be set
to restrict the query to a specific list of channels (e.g. just
`postoffice:check_mail` and `system:announcements`, omitting
game-specific channels). Default: empty list = no restriction (all
subscribed channels).

In v1, the result is a **flat list** (not a paged `listbox` envelope).
If the list grows past `key_f2.max_items` (default 50), the excess is
silently dropped and the `excess` field reports the count. A future
version may wrap the result in a `listbox` envelope for paging
support; v1 does not need it.

### Semantics
- `key_f2` is a **session-level** message type. It is not bound to
  any `request_id` and does not resolve any pending IO request.
- The user can press `F2` at any time — on the login screen, while
  blocked on a `menu` or `inputstring` or `listbox`, or with no
  pending IO at all. The result is rendered, then the user is
  returned to wherever they were (or to the main screen if nothing
  was pending).
- The server requires an active `auth` / `reconnect` session. A
  `key_f2` from an unauthenticated session returns
  `key_f2_error{code:"not_authenticated"}`.
- The server rate-limits `key_f2` per session (default 5 req/s,
  configurable via `bed.json` `key_f2.rate_limit` and CLI flag
  `--key-f2-rate-limit`); over-limit requests get
  `key_f2_error{code:"key_f2_rate_limited"}`.
- The thin client may pipeline multiple `key_f2` requests (each is
  independent). The server processes them in order and the client
  matches by arrival order (there is no `sub_request_id` for `key_f2`
  because there is no outer `request_id` to correlate against).
- `key_f2` does **not** clear the "unread" state of the returned
  items. Marking items as read is a separate operation (e.g. the
  `postoffice:check_mail` channel has its own "mark as read" message
  type, owned by the postoffice service). v1 of `key_f2` is
  read-only.

### Decisions
- [ ] v1 default: `key_f2` queries all subscribed channels with
      `key_f2_visible=true`, optionally restricted by
      `bed.json` `key_f2.channel_allow_list` (default: no
      restriction).
- [ ] v1 default: result is a flat list, capped at `key_f2.max_items`
      (default 50), with `excess` reporting the dropped count.
- [ ] v1 default: `key_f2` requires an active auth session.
      Unauthenticated `key_f2` returns
      `key_f2_error{code:"not_authenticated"}`.
- [ ] v1 default: `key_f2` is rate-limited per session at 5 req/s
      (configurable via `bed.json` `key_f2.rate_limit` and CLI
      flag `--key-f2-rate-limit`).
- [ ] v1 default: `key_f2` does not clear unread state. Marking
      items as read is owned by each channel's service.
- [ ] Future: wrap result in a `listbox` envelope for paging when
      `count > key_f2.max_items`.
- [ ] Future: per-channel `key_f2_priority` for ordering items in
      the result list (default: chronological).

### `bed` package additions
- [ ] Add `bed/api/key_f2.py` with `KeyF2Service` (registers `key_f2`,
      `key_f2_result`, `key_f2_empty`, `key_f2_error`). Owns the
      channel-query logic and the per-session rate limiter.
- [ ] Add `bed/api/key_f2_channels.py` with the channel-source
      resolution: `resolve_channels(args, member) -> List[str]`
      applying the `key_f2.channel_allow_list` filter and the
      per-channel `key_f2_visible` flag.
- [ ] Add `bed/api/key_f2_items.py` with the per-channel item
      builder: `build_items(args, member, channel) -> List[Item]`
      that queries each channel's source (postoffice, message_delivery,
      game-specific) and returns the unread list.
- [ ] Extend `bed.main.BED.start` to register `KeyF2Service` after
      `HelpService` and before any game router is loaded.
- [ ] Document `KeyF2Service` in `bed/docs/BED_KEY_F2.md` and add
      `bed/protocol/KEY_F2.md` reference page.
- [ ] Add `bed/tests/test_key_f2_service.py`: empty result
      (`key_f2_empty`) / single-item result / multi-channel result
      / not-authenticated error / channel-unavailable error /
      rate-limit error / no-impact-on-pending-menu (sending `key_f2`
      while a menu is pending does not resolve the menu) / result
      respects `key_f2.max_items` cap / result respects
      `key_f2.channel_allow_list` / result respects per-channel
      `key_f2_visible` flag.
- [ ] Add `bed/tests/test_key_f2_channels.py` for the
      `resolve_channels` function.
- [ ] Add `bed/tests/test_key_f2_items.py` for the per-channel
      `build_items` function (using a stub postoffice / message_delivery
      backend).

### Adoption
- [ ] **murdermotel**: `F2` is the natural "what's happened in the
      motel overnight" key. The murdermotel `MessageRouter` registers
      a `key_f2_items` callback that queries the murdermotel
      `events` channel and merges it with the default subscribed
      channels. Tracked in `murdermotel/TODO.md` "BED `key_f2`
      message type".
- [ ] **empyre**: `F2` is the natural "what's happened on my
      islands" key. The empyre `MessageRouter` registers a
      `key_f2_items` callback that queries the empyre island
      channels and merges with the default subscribed channels.
      Tracked in `empyre/TODO.md` "Phase 2 — Router rework".
- [ ] **casino**: `F2` is the natural "tournament announcements /
      open tables" key. The casino `MessageRouter` registers a
      `key_f2_items` callback that queries the casino lobby channel.
      Tracked in `casino/TODO.md` "BED `menu` message type" (the
      same section will also adopt `key_f2`).
- [ ] **mistermcfeely (postoffice)**: `F2` is the natural
      "new mail" key. The postoffice `MessageRouter` registers a
      `key_f2_items` callback that queries the postoffice mailbox.
      Tracked in `mistermcfeely/TODO.md` (future).
- [ ] **zoid6**: `F2` is the natural "dashboard notifications"
      key. The zoid6 `MessageRouter` registers a `key_f2_items`
      callback that queries the zoid6 dashboard feed.
- [ ] External consumers (third-party clients, mobile apps): the
      `key_f2` envelope is the documented way to fetch new-messages
      for the current user; the result merges all subscribed
      channels and respects the per-channel `key_f2_visible` flag.

### `bed.json` additions
- [ ] Add `key_f2` section to `bed.json`:
      `{ "key_f2": { "rate_limit": 5, "max_items": 50,
                     "channel_allow_list": [] } }`.

---

## Implementation order
1. `bed/api/auth.py` + `bed/api/token_store.py` (in-memory) + `bed/api/credential_provider.py` protocol.
2. `bed.main.BED.start` wires `AuthService` first; `DefaultRouter` keeps its stub `auth`.
3. Tests: `bed/tests/test_auth_service.py` (issue/validate/expire/refresh/revoke/replay/cross-instance).
4. `bed/api/echo.py` + `bed/api/fragment.py` + `bed/api/style.py` for the `echo` / `echo_ack` push channel.
5. `bed/api/menu.py` + `bed/api/menu_validator.py` + `bed/api/menu_timeout.py` for the `menu` envelope (positional + unconditional kwargs from `inputchoice`).
6. `bed/api/help.py` for the `help` / `help_result` / `help_error` round-trip (F1, per-menu help pulled on demand).
7. `bed/api/key_f2.py` + `bed/api/key_f2_channels.py` + `bed/api/key_f2_items.py` for the `key_f2` / `key_f2_result` / `key_f2_empty` / `key_f2_error` round-trip (F2, session-level new-messages query).
8. Wire the `menu` + `help` + `key_f2` pieces into casino (primary menu driver; primary F2 driver for tournament announcements).
9. Wire into murdermotel (primary callable-`help` driver; primary F2 driver for motel events).
10. Wire into empyre (primary echo / listbox / inputstring driver).
11. Wire into mistermcfeely, zoid6, bank service — same `CredentialProvider` swap, no protocol changes.
12. (Optional) DB-backed `TokenStore` for `--token-persistence=db`.

---

## BED `Sink` integration with `bbsengine6.io`

This section describes how `bed` consumes the sink infrastructure
defined in `bbsengine6/TODO.md` "`bbsengine6.io` sink infrastructure
for thin-client BED conversion". The bbsengine6 work is foundational;
this work is the consumer. The dependency direction is:
`bbsengine6` → `bed` → game repos.

### Phase 0 — `BEDSink` for the BED process

- [ ] Add `bed/sinks/bed_sink.py` with `BEDSink(websocket, server,
      router)`. The `BEDSink` is a per-connection adapter that
      implements the `bbsengine6.io.sink.Sink` protocol.
- [ ] The `BEDSink` holds three references:
  - `self.websocket`: the per-connection WebSocket (so it can call
    `server.send_to(websocket, envelope)`).
  - `self.server`: a reference to the `WebSocketServer` (so it can
    call `send_to`).
  - `self.router`: a reference to the per-process `MessageRouter`
    (from `bbsengine6/net/router.py`, gained the `MessageRouterMixin`
    API in bbsengine6 Phase 5).
- [ ] Each `BEDSink` method builds the appropriate BED envelope and
  calls `await self.server.send_to(self.websocket, envelope)`:
  - `echo(text, **kwargs)`: calls `bbsengine6.io.echo_render(text,
    **kwargs)` to get the rendered string, builds an `echo` envelope
    (per the `echo` / `echo_ack` section above), sends it.
  - `inputchoice(prompt, options, default="", **kwargs)`: builds a
    `menu` envelope, awaits `menu_reply`. The `request_id` is
    allocated via `router.next_request_id(websocket)`; the future
    is `router.get_pending_request(websocket, request_id)`.
  - `inputstring(prompt, default="", **kwargs)`: builds an
    `inputstring` envelope, awaits `inputstring_reply`.
  - `inputboolean`, `inputinteger`, `inputchar`, `inputdate`,
    `inputfilename`, `inputpassword`: analogous.
  - `screen_setbottombar(text)`: pushes an `echo` frame on
    `stream="bottombar"`.
  - `screen_register_bottombar_fragment(name, text)`: same.
  - `screen_unregister_bottombar_fragment(name)`: same with
    `unregister=true`.
- [ ] The `BEDSink` does NOT own the message loop. It only owns the
  outgoing-send side. Incoming `*_reply` messages are dispatched by
  `WebSocketServer.dispatch_message` → `MessageRouter.handle_message`
  → the right service handler (e.g. `IOServiceHandler` for
  `menu_reply`, `inputstring_reply`, etc.). The `IOServiceHandler`
  calls `router.resolve_pending_request(websocket, request_id,
  value)` to resolve the future in the `BEDSink`.
- [ ] **No new `MessageRouter` is created.** The `BEDSink` is a
  per-connection writer-adapter that uses the existing per-process
  `MessageRouter` (loaded via `--router`) for session access and
  pending-request resolution. The `MessageRouter` is the
  incoming-dispatch side; the `BEDSink` is the outgoing-send side.
  This is the cleanest separation.
- [ ] **Backward compat check**: door-mode game routers (which run
  in a process without `WebSocketServer` / `BED`) don't install a
  `BEDSink`; they get the default `DefaultSink` behavior. The
  `bbsengine6/tests/test_io_backward_compat.py` suite passes.
- [ ] Add `bed/tests/test_bed_sink.py`:
  - `BEDSink.echo` builds an `echo` envelope and calls
    `server.send_to` (not a write to stdout).
  - `BEDSink.inputchoice` builds a `menu` envelope, records the
    pending request, and (when the `IOServiceHandler` resolves the
    `menu_reply`) returns the hotkey.
  - `BEDSink.inputstring` builds an `inputstring` envelope and
    returns the value.
  - `BEDSink.screen_setbottombar` pushes an `echo` frame on
    `stream="bottombar"`.
  - `BEDSink` does not own the message loop; it only sends
    outgoing envelopes.

### Phase 1 — `BEDSink` installed via `WebSocketServer.on_connect_hook`

- [ ] In `bed/main.py`, when constructing the `WebSocketServer`,
  register an `on_connect_hook` that:
  1. Builds a per-connection `BEDSink(websocket, server, router)`.
  2. Installs the sink via `token = set_io_sink(bed_sink)` (from
     `bbsengine6.io.sink`).
  3. Runs the message loop (reads envelopes from the WebSocket,
     dispatches to `router.handle_message`, sends responses).
  4. In the `finally` block, calls `reset_io_sink(token)` and
     `router.cleanup_session(websocket)`.
- [ ] The hook signature is
  `async def on_connect_hook(websocket, router)`. The `router` is
  the per-process `MessageRouter` (passed in by the
  `WebSocketServer`).
- [ ] This is **option (e)** in the prior plan: the hook owns the
  message loop. The `WebSocketServer.on_connect` delegates the
  message loop to the hook when one is registered; otherwise it
  runs the existing message loop (backward compat).
- [ ] **Backward compat**: door-mode game routers (which run in a
  process without `WebSocketServer`) don't install a sink; they get
  the default `DefaultSink` behavior.
- [ ] Add `bed/tests/test_bed_sink_on_connect.py`:
  - The `BEDSink` is installed via the hook.
  - The `BEDSink` persists for the connection lifetime.
  - The `BEDSink` is reset on disconnect.
  - The `BEDSink` doesn't leak across connections (two connections
    get two different `BEDSink` instances).
  - `router.cleanup_session(websocket)` is called on disconnect.

### Phase 2 — Thin-client `IOSink` for `bed/client/`

- [ ] In `bed/client/io_sink.py` (shared across all thin clients),
  add `ThinClientIOSink(websocket)` — a client-side `Sink`
  implementation. Each method builds the appropriate envelope and
  sends it over the WebSocket to the BED process; the response is
  awaited and returned to the caller.
- [ ] The thin client uses this `IOSink` to replace the existing
  `sys.modules['bbsengine6.io']` swap in `empyre/io_bridge.py` (and
  equivalent in casino / murdermotel / etc.). The `sys.modules` swap
  continues to work as a v1 default; the `IOSink` is a future
  option. See `empyre/TODO.md` "Phase 1 — IO shim" for the
  migration plan.
- [ ] **Backward compat**: the `sys.modules` swap is unchanged; the
  `IOSink` is an additive alternative.
- [ ] Add `bed/tests/test_thin_client_io_sink.py`:
  - `ThinClientIOSink.echo` sends an `echo` envelope over the
    WebSocket.
  - `ThinClientIOSink.inputchoice` sends a `menu_reply` envelope
    and awaits `menu_reply` (with `request_id` matching).
  - `ThinClientIOSink` does not own the WebSocket; it only uses the
    one passed in.

### Phase 3 — Thin client uses `echo_render` for the `text` field

- [ ] In `bed/sinks/bed_sink.py`, the `BEDSink.echo` method calls
  `bbsengine6.io.echo_render(text, **kwargs)` to get the rendered
  string, then ships it in the `echo` envelope's `text` field.
- [ ] The thin client renders `text` verbatim. No client-side MCI
  rendering.
- [ ] **Backward compat**: door-mode callers (who don't install a
  sink) get the current behavior. The `echo_render` call is internal
  to the sink.
- [ ] Add `bed/tests/test_bed_sink_echo_render.py`:
  - `BEDSink.echo("{f6}hello")` builds an `echo` envelope with
    `text` = the MCI-substituted string.
  - The same input in door mode produces the same stdout output
    (verified by `bbsengine6/tests/test_io_backward_compat.py`).

### Phase 4 — Thin client uses `mci.parse` for the `mci` field

- [ ] In `bed/sinks/bed_sink.py`, the `BEDSink.echo` method calls
  `bbsengine6.io.mci.parse(text)` to get the token list, then ships
  it in the `echo` envelope's `payload.mci` field.
- [ ] The `mci` field is optional in v1 (a future client can ignore
  it). The `text` field is always populated.
- [ ] **Backward compat**: door-mode callers don't see the `mci`
  field. The `parse` call is internal to the sink.
- [ ] Add `bed/tests/test_bed_sink_mci.py`:
  - `BEDSink.echo("{f6}hello {var:foo}")` builds an `echo` envelope
    with `payload.mci` = the parsed token list.
  - The same input in door mode produces the same stdout output
    (verified by `bbsengine6/tests/test_io_backward_compat.py`).

### Decisions (sink integration)
- [ ] v1 default: `BEDSink` is installed via the
  `WebSocketServer.on_connect_hook` (option e: the hook owns the
  message loop). The hook signature is
  `async def on_connect_hook(websocket, router)`.
- [ ] v1 default: the `BEDSink` does not own the message loop. It
  only owns the outgoing-send side. The `MessageRouter` is the
  incoming-dispatch side. The `IOServiceHandler` (a service on the
  `MessageRouter`) resolves pending-request futures via
  `router.resolve_pending_request(...)`.
- [ ] v1 default: the thin-client `IOSink` lives in
  `bed/client/io_sink.py` (shared across all games). The
  `sys.modules` swap continues to work as the v1 default; the
  `IOSink` is a future option.
- [ ] v1 default: `BEDSink.echo` populates the `echo` envelope's
  `text` field via `bbsengine6.io.echo_render` (door-mode
  byte-for-byte parity) and the `mci` field via
  `bbsengine6.io.mci.parse` (structured token list for future
  clients).

### Cross-references
- [ ] The bbsengine6 sink infrastructure is defined in
  `bbsengine6/TODO.md` "`bbsengine6.io` sink infrastructure for
  thin-client BED conversion" (Phases 0–5). This section is the
  consumer.
- [ ] Game-repo adoption (empyre, casino, murdermotel, mistermcfeely,
  zoid6) is tracked in each repo's `TODO.md` cross-reference
  section.

---

## `bed.client.messages` — shared base for per-project message-family clients

**Status:** v1 base in `bed/src/bed/client/messages.py`
(`BedMessageClient`). v1 message-family client:
`bed.client.bank.BedBankClient` (empyre shape).

### Why a base class

The five-line pattern

    async def _request(self, message):
        reply = await self._conn.send(message)
        if reply.get("type") == "error":
            raise BedUnavailable(f"...")
        return reply

was being copy-pasted into every per-project message-family client
(empyre's old `BedBankClient` and `_BedPlayerClient`, casino's
planned `BedBankClient`). Promoting it to
`bed.client.messages.BedMessageClient` cuts each method down to one
line and makes the error-translation policy live in one place.

### Contract: `not_found=` and `default=`

`BedMessageClient._request(message, *, not_found=(), default=_NO_DEFAULT)`
takes two optional kwargs that work together:

- `not_found` is a tuple of error codes that should NOT raise
  `BedUnavailable` — typically `("not_found",)` for soft-404 lookups.
- `default` is what to return when the server's error code matches
  `not_found`. If unset, `None` is returned. A `_NO_DEFAULT` sentinel
  distinguishes "no default" from "default is None".

Transport-level failures (no connection, timeout, JSON parse error)
always raise `BedUnavailable` regardless of `not_found`.

### Per-project message-family clients

bed owns the wire protocol; each project owns the message-family
client that speaks it. `bed.client.bank.BedBankClient` is the
empyre-shaped bank client (operates on a per-account `moniker`).
Casino's table-bank `BedBankClient` is a separate class in
`casino/src/casino/services/bank_client.py` — same name, different
shape (operates on `table_moniker` and wraps the
table→owner-moniker translation in its methods). Both subclass
`BedMessageClient`.

### Adopted

- [x] `bed/src/bed/client/messages.py` — `BedMessageClient` base
- [x] `bed/src/bed/client/bank.py` — `BedBankClient` (empyre shape)
- [x] `empyre.bed_client` no longer defines `BedBankClient`; call
      sites in `empyre.services.player` import directly from
      `bed.client.bank`

### Follow-up

- [ ] `_BedPlayerClient` in
      `empyre/src/empyre/services/player.py:56-109` still has the
      per-method envelope pattern copy-pasted. Convert it to
      subclass `BedMessageClient`. Its `info()` method is the
      natural first user of
      `not_found=("not_found", "player_not_found")`.
- [ ] Casino's `BedBankClient`
      (`casino/src/casino/services/bank_client.py`, planned in
      `casino/TODO.md` "Adopt `bed.client` for WebSocket transport")
      subclasses `BedMessageClient`. See casino TODO for the five
      wire messages it wraps.

---

## Legacy
(none yet — first entry in this file)

---

## CLI `--config` flag

`bed` accepts a `--config PATH` argument. **The default value is the
`bed.json` shipped in the bed package's `data/` subdirectory**
(`bed/data/bed.json`, resolved via `bed.config.get_package_data_path`).
Passing `--config` overrides the default and points bed at an external
JSON file. The file's keys are deep-merged on top of the packaged
defaults, and the result is consulted during startup.

### Syntax
```
bed --config /opt/zoid6/src/zoid6/data/bed.json --router zoid6.api.handler.MessageRouter
```

### Priority order (highest wins)
1. CLI flag (e.g. `--host`, `--no-autorestart`, `--databasename`).
2. The file passed via `--config`.
3. `BED_*` environment variables.
4. Packaged `bed/src/bed/data/bed.json` defaults.

### Recognized top-level keys
- `bed.autorestart` (bool), `bed.restart_delay` (int), `bed.max_restarts` (int)
  — drive `bed`'s in-process restart loop.
- `bind.host` (str), `bind.port` (int) — applied to the WebSocket
  server **only if** the user did not pass `--host` / `--port` on the
  CLI (detected by comparing the parsed value against the argparse
  default).
- `database.name` / `database.host` / `database.port` / `database.user` /
  `database.password` — same precedence rule against
  `--databasename` / `--databasehost` / etc. The zoid6 `bed.json`
  carries only `name` / `host` / `port`; `user` and `password` stay on
  the CLI or in `BED_DATABASEUSER` / `BED_DATABASEPASSWORD` env.

### `services.*` is intentionally NOT consumed by `bed`
The zoid6 `bed.json` carries a `services` map that the **router**
(`zoid6.api.handler.MessageRouter`) iterates to load enabled module
routers. `bed` itself stays router-agnostic. The systemd unit (or
shell invocation) must pass `--router zoid6.api.handler.MessageRouter`
to select the unified router.

### Missing / unreadable config file
A missing `--config` path causes `bed` to exit 1 with
`Config file not found: <path>`. There is no silent fallback to the
packaged defaults — fail loud so the systemd unit catches the typo
immediately.

### Path resolution
No `~` expansion, no `Path.resolve()`, no relative-path magic. Whatever
the user (or the systemd unit) passes is used verbatim. Pass an
absolute path from the systemd unit (`/opt/zoid6/src/zoid6/data/bed.json`).

### Systemd invocation shape (no unit file in this commit)
The systemd unit is out of scope for this change, but the expected
shape is:
```
ExecStart=/usr/bin/bed \
  --config /opt/zoid6/src/zoid6/data/bed.json \
  --router zoid6.api.handler.MessageRouter \
  --no-autorestart
Restart=on-failure
User=zoid6
WorkingDirectory=/opt/zoid6
```
`--no-autorestart` lets systemd own the restart loop; `bed` exits on
crash and `Restart=on-failure` brings it back.

Note: with the default `--config` now pointing at the packaged
`bed/data/bed.json`, the systemd unit does **not** need to pass
`--config` at all to use bed's own defaults. A zoid6 deployment
wanting the unified router still needs to pass
`--config /opt/zoid6/src/zoid6/data/bed.json` (or its own service
file under zoid6's package). See the "Systemd deployment" section
below.

### zoid6 dependency
`zoid6/src/pyproject.toml` declares `bed>=0.0.1.dev2026` as a runtime
dependency so the unified router can rely on `bed`'s CLI being
installed alongside it.

---

## Systemd deployment

A `bed.service` unit ships with the package at
`bed/daemon/bed.service` (also included in wheel sdist via
`tool.setuptools.package-data`). It is `Type=simple`, runs as
`User=zoid6`/`Group=zoid6`, and is the **generic** bed service —
it uses the packaged `bed/data/bed.json` config (via the new
`--config` default) and the default `bbsengine6` router.

```
ExecStart=/usr/bin/bed --no-autorestart
```

This unit is intentionally minimal so it works out-of-the-box for
operators who just want "bed, with its defaults, supervised by
systemd." It does **not** load any zoid6 module routers, and it
does not pass `--config` — the packaged default is used.

For the zoid6 deployment, ship a separate `zoid6-bed.service` (in
the zoid6 repo, not bed) that does:
```
ExecStart=/usr/bin/bed \
  --config /opt/zoid6/src/zoid6/data/bed.json \
  --router zoid6.api.handler.MessageRouter \
  --no-autorestart
```
The zoid6 unit will be a thin customization of the bed unit and
reuses the same `EnvironmentFile=`, `User=`, `Group=`, and
hardening settings.

### Install
```
cd /opt/bed         # or wherever the bed repo is checked out
sudo make install-systemd
sudo systemctl enable --now bed
sudo systemctl status bed
```

The Makefile lives at the **bed repo root** (`bed/Makefile`,
sibling to `bed/pyproject.toml`) because bed's `pyproject.toml` is
not under `bed/src/` like empyre/zoid6's are. All paths inside the
Makefile are relative to the project root (`src/bed/...`).

The `install-systemd` target copies `bed/src/bed/daemon/bed.service`
to `/etc/systemd/system/bed.service` (mode 0644) and runs
`systemctl daemon-reload`. It does **not** start or enable the
service — that is left to the operator so they can review the
installed unit first.

`make uninstall-systemd` stops, disables, and removes the unit.

### Environment file
The unit loads `/etc/zoid6/bed.env` (the leading `-` means missing
file is non-fatal). Create it on the deploy host with the database
credentials that are intentionally absent from `bed.json`:
```
# /etc/zoid6/bed.env
BED_DATABASEUSER=zoid6
BED_DATABASEPASSWORD=...
```
`BBSENGINE6_DB*` env vars are also honored by `bbsengine6`'s
`databasebuildargs` (e.g. `BBSENGINE6_DBNAME`, `BBSENGINE6_DBHOST`,
`BBSENGINE6_DBPORT`).

### Reload
```
sudo systemctl reload bed     # sends SIGHUP; bed logs a config reload
```
bed wires `SIGHUP` to a `config.reload_config()` call (informational
log only — the live WebSocket server keeps the args it was started
with). A true config-change-then-restart is `systemctl restart bed`.

### Logs
```
journalctl -u bed -f
```
The unit sets `SyslogIdentifier=bed`, so `journalctl` filters
cleanly. `bed` writes via `bbsengine6.io.echo`, which goes to
stdout/stderr (captured by `StandardOutput=journal`).

### Why `Type=simple` (not `Type=notify`)
The unit uses `Type=simple` so it works without the `systemd`
Python package. This means systemd considers the service "started"
as soon as `bed` is exec'd, *before* the WebSocket server has
finished its database connection probe. In practice this is a few
hundred milliseconds of "active (running)" status where the WS
socket is not yet accepting connections.

If/when we want exact "ready" signaling, switch to `Type=notify` and
add a `sd_notify("READY=1")` call (via the `systemd` Python package)
in `bed.main.main_async` immediately after `await self.server.start()`.
The trade-off: a soft dependency on the `systemd` PyPI package and a
check at startup that the `NOTIFY_SOCKET` env var is present before
calling `sd_notify` (so dev runs without systemd keep working).

