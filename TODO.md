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

## Implementation order
1. `bed/api/auth.py` + `bed/api/token_store.py` (in-memory) + `bed/api/credential_provider.py` protocol.
2. `bed.main.BED.start` wires `AuthService` first; `DefaultRouter` keeps its stub `auth`.
3. Tests: `bed/tests/test_auth_service.py` (issue/validate/expire/refresh/revoke/replay/cross-instance).
4. Wire it into empyre (drives the design; covers the most complex use case first).
5. Wire it into casino (validates the design with a different game + spectator/bot patterns).
6. Wire it into mistermcfeely, murdermotel, zoid6, bank service — same `CredentialProvider` swap, no protocol changes.
7. (Optional) DB-backed `TokenStore` for `--token-persistence=db`.

---

## Legacy
(none yet — first entry in this file)
