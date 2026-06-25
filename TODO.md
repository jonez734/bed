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
