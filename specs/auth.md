# bed AuthService — Specification

> **Audience:** implementers working on `bed` and downstream
> consumers (`zoid6`, `empyre`, `casino`, `murdermotel`, `mistermcfeely`,
> the `bbsengine6` TUI). This is the entry-point spec for bed's
> bearer-token authentication protocol.
>
> **See also:**
> - [`handbook/BED_AUTH.md`](../handbook/BED_AUTH.md) — bearer-token
>   protocol reference (wire-shape, claim schema, error codes,
>   security notes, v1/v2 multi-instance roadmap). This spec
>   covers the server + client + CLI architecture; BED_AUTH.md is
>   the authoritative wire-protocol document.
> - [`SPEC.md`](../SPEC.md) — bed daemon entry point (what works, what
>   doesn't, v1/v1.1/v1.2/v1.3/v1.4/v2 phase gates, code that moved
>   to `bbsengine6`, bbsengine6-side prerequisites for each bed
>   service).
> - [`README.md`](../README.md) — quick-start, CLI flags, routers,
>   console scripts, layout.
> - [`specs/message.md`](message.md), [`specs/ping.md`](ping.md),
>   [`specs/bank.md`](bank.md) — sibling service specs.
> - `bbsengine6.auth.access` — the per-op policy function
>   AuthService delegates to. Lives in bbsengine6; this spec does
>   not duplicate its policy.
> - [`CHANGELOG.md`](../CHANGELOG.md) — release history.

---

## 1. Overview

`bed.AuthService` is the bed-side runtime for the bearer-token
authentication protocol. It issues short-lived HMAC-SHA256 signed
tokens so clients can reconnect after a network blip or a `bed`
restart without re-asking for the password. Tokens are opaque,
URL-safe, and bound to the issuing `bed_instance_id`.

```json
C→S {"type":"auth", "moniker":"alice", "password":"…"}
S→C {"type":"auth_result", "success":true, "moniker":"alice",
     "is_sysop":false, "session_id":"…", "token":"…",
     "expires_at":"2026-08-15T22:00:00Z", "balance":42}
```

The whole primitive is one BED daemon process by design; for a
multi-instance deployment behind a load balancer, see "Out of scope
for v1" in `handbook/BED_AUTH.md`.

The default `bbsengine6.net.defaultrouter.DefaultRouter` is **not**
wired to `AuthService` — it stays a no-credential stub for `wscat`
smoke tests and local development. Any other router
(`zoid6.api.handler.MonikerAuthRouter`, a real game router, …)
**is** wired automatically.

### 1.1 What this spec covers

- Wire protocol: the four request types (`auth`, `reconnect`,
  `auth_refresh`, `auth_revoke`) and the response shapes.
- Token codec: HMAC-SHA256 over a JSON claims payload, URL-safe
  base64, opaque on the wire.
- Secret file: per-instance HMAC secret + UUIDv4 instance id,
  mode 0600, v1→v2 upgrade path.
- Token storage: in-memory (default), DB-backed (`--token-persistence=db`),
  and disabled (`--token-persistence=none`).
- Session registry: `bed.api.session.SessionRegistry` (the
  per-websocket state machine). AuthService is the primary writer
  but other services (BankService, MessageService) read from it.
- Service registration: where AuthService sits in `BED.start()`.
- Per-op authorization: `bbsengine6.auth.access(args, op, session,
  message)` is the policy function AuthService delegates to.
- Server runtime: `AuthService` lifecycle, the four per-op
  handlers, the token rotation invariant.
- Client runtime: `BedAuthServiceClient` (login / reconnect /
  refresh / revoke).
- CLI tool: `bed auth` (login / reconnect / refresh / revoke).
- Configuration: `--bed-secret`, `--token-ttl`,
  `--token-persistence`, `--credential-provider`,
  `--bed-instance-id`.
- Security: HMAC scheme, instance binding, token scrubbing, secret
  file permissions.
- Testing: `test_auth_service.py` (~1,955 lines) and
  `test_auth_tool.py` (~1,147 lines).

### 1.2 What this spec does NOT cover

- The bearer-token wire protocol details — see
  `handbook/BED_AUTH.md`
  for the full wire-shape reference, claim schema, error code
  table, and the v1/v2 multi-instance roadmap.
- Per-op policy decisions for non-auth services — `bank.access`,
  `message.access`, etc. live in their sibling specs.
- The credential provider's DB lookups — `PasswordCredentialProvider`
  delegates to `bbsengine6.member.checkpassword`,
  `bbsengine6.member.getcredits`, etc.; this spec treats it as a
  pluggable `CredentialProvider` Protocol.

---

## 2. Wire protocol

The full wire shape is documented in `handbook/BED_AUTH.md`. This
section is the architectural summary.

### 2.1 Client requests

| `type`           | Required fields               | Notes                                  |
|------------------|-------------------------------|----------------------------------------|
| `auth`           | `moniker`, `password`         | First-time login; binds session+token  |
| `reconnect`      | `token`                       | Rebind to new WS; replays pending req  |
| `auth_refresh`   | `token`                       | Rotate token on the original WS only   |
| `auth_revoke`    | `token`                       | Invalidate the token                   |

### 2.2 Server responses

| `type`               | Shape (success)                                                  |
|----------------------|------------------------------------------------------------------|
| `auth_result`        | `{success: true, moniker, is_sysop, session_id, token, expires_at, balance}` |
| `reconnect_result`   | `{success: true, moniker, is_sysop, session_id, token, expires_at, replayed, replayed_request_id?}` |
| `auth_result`        | (refresh shares the login envelope; `fresh: false`)             |
| `auth_revoke_result` | `{success: true}`                                                |

### 2.3 Error envelopes

Standard `{"type": "error", "code": ..., "message": ..., "recoverable": bool}`
shape. The full error code table is in `handbook/BED_AUTH.md`
("Errors" section).
Most relevant for clients:

| code                       | recoverable | meaning                                                                 |
|----------------------------|-------------|-------------------------------------------------------------------------|
| `missing_credentials`      | false       | `moniker` or `password` empty in `auth`                                 |
| `bad_credentials`          | false       | moniker not found, or password mismatch, or DB unavailable              |
| `token_invalid`            | false       | malformed token, bad signature, unparseable claims                      |
| `token_expired`            | **true**    | token past `expires_at`; client may re-`auth` and start over           |
| `token_revoked`            | false       | token was explicitly revoked, or evicted by `gc_expired`                 |
| `bed_instance_mismatch`    | false       | token was signed by a different `bed_instance_id` (e.g. load-balanced)  |
| `not_authenticated`        | true        | operation requires a live socket (e.g. `auth_refresh` after reconnect) |
| `bed_secret_insecure`      | false       | `--bed-secret` file is world/group readable; bed refused to start      |
| `database_error`           | false       | credential lookup against the DB failed                                 |

`recoverable: true` is the client's hint: it may re-`auth` (interactive
password prompt) and continue. `recoverable: false` is a hard failure
that requires the operator to investigate.

---

## 3. Token shape

The on-the-wire token is a single string:

```
<payload_b64>.<hmac_hex>
```

- **payload** is a JSON object, `urlsafe_b64encode`-ed (no padding),
  containing the claims:
  - `version`          (int, currently 1)
  - `moniker`          (string)
  - `issued_at`        (float, seconds since epoch)
  - `expires_at`       (float, seconds since epoch)
  - `session_id`       (UUID4 string, server-assigned, sticky across reconnects)
  - `is_sysop`         (bool)
  - `bed_instance_id`  (UUID4 string, server-assigned, baked in)
  - `websocket_id`     (UUID4 string, server-assigned, per-connection)
  - `loginid`          (string, optional; OS-level login name for diagnostics)
- **hmac** is `HMAC-SHA256(secret, payload_b64)`, hex-encoded.

The HMAC secret is 32 bytes of CSPRNG entropy generated on first run
and persisted at `~/.config/bed/bed.secret` (mode 0600). The file also
holds the per-instance UUID; rotating the secret invalidates all
outstanding tokens (acceptable: forces re-`auth`, doesn't leak
credentials).

### 3.1 Token codec

`bed.api.auth._encode_token(claims, secret)` and
`_decode_token(token, secret)` are the symmetric codec pair. The
codec is JSON-based so future claim additions are forward-compatible;
`SUPPORTED_TOKEN_VERSIONS = frozenset({1})` is the explicit version
gate. A token with an unknown `version` raises
`TokenError(CODE_TOKEN_INVALID, ...)` and the handler returns
`token_invalid`.

`TokenError` is the codec's structured exception; the handler
translates each subclass of failure (`malformed token`, `bad signature`,
`unparseable claims`, `unsupported token version`) into the same
`token_invalid` wire envelope.

### 3.2 Secret file format

`bed.api.secret` owns the secret file. Two formats are read:

- **v1** (legacy): 32 raw bytes. On read, upgraded in-place to v2.
- **v2** (current): JSON object `{"__bed_secret_version": 2, "hmac":
  "<64 hex chars>", "instance_id": "<UUID>"}`.

The v1→v2 upgrade is opportunistic: the v1 bytes are kept, a fresh
UUIDv4 is generated for `instance_id`, the file is rewritten at mode
0600, and a WARNING is logged. The upgrade is best-effort: a
write-failure leaves the original file intact (a v1-read can still
be served).

`_write_secret_file` uses `tempfile.mkstemp` + `os.replace` for
atomicity and `os.fchmod` on the open fd before close to avoid
umask-leak races on any platform. The directory is `mkdir -p 0700`'d
on first write.

### 3.3 Secret file permissions

`_read_secret_file` raises `InsecureSecretError` if the file is
world- or group-readable (`stat.S_IRWXG | stat.S_IRWXO` set). The
error message points the operator at the fix: `Run chmod 600 <path>`.
`bed` refuses to start in this state.

### 3.4 Per-instance UUID

`bed_instance_id` is generated as a UUIDv4 from
`secrets.randbits(128)` and persisted in the secret file. An
explicit `--bed-instance-id` flag overrides the file value (used
for tests and multi-instance hosts that want a stable per-node ID);
the override is *not* persisted back to disk.

The instance id is baked into every issued token's claims; a token
minted by `instance A` is rejected at `reconnect` / `auth_refresh`
on `instance B` with `bed_instance_mismatch`, and the matching
record is deleted from the local token store.

---

## 4. Token storage

`bed.api.token_store.TokenStore` is a Protocol with three
implementations selected via `--token-persistence`. The store is
shared between `AuthService` (writer) and the other services
(BankService, MessageService — readers via Gate 2/3 in their
`._check_access` flow).

### 4.1 `InMemoryTokenStore` (default)

In-process `Dict[token, TokenRecord]`, guarded by a `threading.Lock`.
Tokens are lost on `bed` restart; clients must re-`auth`. GC is lazy
(`get` evicts when expired) plus a `gc_expired(now=None)` method
that the daemon can call periodically.

Optional `now_factory` lets tests inject a fake clock so expiry can
be triggered deterministically. Production code should leave it None.

### 4.2 `DBTokenStore` (`--token-persistence=db`)

PostgreSQL-backed via `engine.__bed_token` (see
`bed/data/sql/bed_token.sql`). The store borrows connections from
the bbsengine6 connection pool per call; matches the
`bbsengine6.member` / `bbsengine6.bank` pattern.

Operations:

- `put(record)` — `INSERT … ON CONFLICT (token) DO UPDATE`.
- `get(token)` — `SELECT … WHERE token = %s AND expires_at > now()`.
  Returns `None` if the row is missing OR expired.
- `delete(token)` — `DELETE … WHERE token = %s`. Returns
  `bool(rowcount > 0)`.
- `gc_expired(now=None)` — `DELETE … WHERE expires_at <= …`.
  Returns row count.

### 4.3 `none` (`--token-persistence=none`)

Disables `AuthService` entirely. Equivalent to using
`DefaultRouter` regardless of the `--router` choice. The
`AuthService` is not constructed and the four wire types return 404
(or are routed by the loaded router if it has its own auth).

### 4.4 `TokenRecord` shape

```python
@dataclass
class TokenRecord:
    token: str
    moniker: str
    session_id: str
    issued_at: float
    expires_at: float
    is_sysop: bool
    bed_instance_id: str
    websocket_id: str
    claims: Dict[str, Any] = field(default_factory=dict)
    loginid: Optional[str] = None
```

`claims` is the full original JSON-claims dict, preserved verbatim
so `bbsengine6.auth.access` can re-encode / verify on round trips.
The other fields are promoted to the top level because every code
path that uses them (reconnect rebind, session lookup, pending-
request replay) needs the same hot path, and a dict indirection is
wasteful.

### 4.5 `MemberInfo` shape

```python
@dataclass
class MemberInfo:
    moniker: str
    is_sysop: bool = False
    balance: Optional[int] = None
    loginid: Optional[str] = None
```

Return value of a successful `CredentialProvider.authenticate()`
call. `balance` is best-effort — `None` means "unknown; the wire
envelope will emit `balance: 0`".

---

## 5. Credential providers

`bed.api.credential_provider.CredentialProvider` is a Protocol. The
default provider is `PasswordCredentialProvider` (real
`bbsengine6.member.checkpassword` match); the legacy alternative is
`MonikerOnlyCredentialProvider` (any non-empty password is accepted
once the moniker resolves). Both return `MemberInfo` on success or
`None` on any failure.

### 5.1 `PasswordCredentialProvider` (default)

`authenticate(args, moniker, password, *, pool)`:

1. Empty moniker / empty password → `None`.
2. `bbsengine6.member.checkpassword(args, password, membermoniker=moniker, pool=pool)`.
   Returns `None` if it raises (DB error) or returns False.
3. `bbsengine6.member.issysop(args, moniker=moniker, pool=pool)` —
   best-effort; defaults to False on failure.
4. `bbsengine6.member.getcredits(args, membermoniker=moniker, pool=pool)`
   — best-effort; defaults to None on failure.
5. `bbsengine6.member.getbymoniker(args, moniker, fields="loginid", pool=pool)`
   — best-effort; defaults to None on failure.

The provider deliberately returns `None` on every failure mode so
the wire response cannot be used to enumerate which monikers exist.

### 5.2 `MonikerOnlyCredentialProvider`

`authenticate(args, moniker, password, *, pool)`:

1. Empty moniker → `None`.
2. `bbsengine6.member.moniker_exists(args, moniker, pool=pool)` —
   `None` on any DB error (with an `io.echo(level="error")` trace).
   A `ValueError` (invalid moniker shape) is re-raised so the
   daemon's overall error reporting catches it.
3. `bbsengine6.member.issysop(...)` — best-effort.
4. `_lookup_loginid(args, moniker, pool=pool)` — best-effort.

Used for development, `wscat` smoke tests, or when the game will
gate sensitive actions through per-route password challenges. The
non-empty password requirement keeps a blank string from being
accepted.

### 5.3 `get_provider(name)`

Resolve `--credential-provider` to a provider instance. Accepted
values: `password` (default), `moniker-only`, `moniker`,
`moniker_only`. Unknown values raise `ValueError`.

### 5.4 `_lookup_loginid`

Resolve the OS-level `loginid` for a member. Best-effort: never
raises. Returns `None` if the row is missing, the column is NULL,
or the lookup fails for any reason. `loginid` is purely
informational (used in server-side debug logs) so a failure to
resolve it must never block authentication. Any DB failure is
surfaced via `io.echo_traceback` so the operator can see what went
wrong in the logs.

---

## 6. Session registry

`bed.api.session.SessionRegistry` maps WebSocket IDs and session
IDs to `SessionState`. AuthService is the primary writer but every
service reads from it.

### 6.1 `SessionState`

```python
@dataclass
class SessionState:
    session_id: str
    websocket_id: str
    moniker: str
    is_sysop: bool
    balance: Optional[int] = None
    request_id_counter: int = 0
    pending_request: Optional[Dict[str, Any]] = None
    auth_service_token: Optional[str] = None
    loginid: Optional[str] = None
    table_moniker: Optional[str] = None       # casino-only
    spectator_of: Set[str] = field(default_factory=set)  # casino-only
```

- `pending_request` is the *last* IO request the server pushed to
  the client that has not yet been acked. On reconnect,
  `AuthService._handle_reconnect` calls
  `SessionRegistry.take_pending(session_id)` and replays exactly
  this envelope (the `request_id` survives the socket switch) so
  the client can resume with a single ack.
- `auth_service_token` is the most recent token the server issued
  for this session. Defense-in-depth readers (BankService,
  MessageService) use it in their session-bound token gate when the
  wire payload has no `token` field.
- `table_moniker` / `spectator_of` are casino-only fields. The
  casino router reuses `SessionRegistry` as its session store.

### 6.2 `SessionRegistry` API

| Method                                     | Purpose                                       |
|--------------------------------------------|-----------------------------------------------|
| `bind(session_id, websocket_id, moniker, is_sysop, *, balance=None, loginid=None, table_moniker=None, spectator_of=None)` | Create or rebind a session |
| `rebind_websocket(old, new)`               | Move a session to a new WS id                 |
| `unbind_websocket(websocket_id)`           | Drop the WS-side mapping (session kept)       |
| `drop(session_id)`                         | Drop the session entirely                     |
| `get_by_session(session_id)`               | Lookup by session_id                          |
| `get_by_websocket(websocket_id)`           | Lookup by websocket_id                        |
| `next_request_id(session_id)`              | Increment + return `r<N>`                     |
| `record_pending(session_id, envelope)`     | Stash an un-acked request for replay          |
| `take_pending(session_id)`                 | Pop the pending request (returns + clears)    |
| `clear_pending(session_id)`                | Drop the pending request without reading      |
| `set_table_moniker(session_id, moniker)`   | Casino: bind / unbind the player's table seat |
| `get_table_moniker(session_id)`            | Casino: current table the player is seated at |
| `add_spectator(session_id, table_moniker)` | Casino: mark as spectating                    |
| `remove_spectator(session_id, table_moniker)` | Casino: drop a spectator entry            |
| `get_table_observers(table_moniker)`       | Casino: set of session_ids spectating         |
| `get_table_player_count(table_moniker)`    | Casino: count of sessions seated at table     |

The casino-only methods maintain an indexed view of the per-table
audience so `server.publish("casino:table:<X>", ...)` does not have
to scan every session. The `SessionState` mirror fields are the
source of truth; the index is rebuilt from them on demand if it
ever falls out of sync (via `_reindex`).

### 6.3 `next_request_id`

Monotonic `r<N>` counter scoped per session. `BankService` and
`MessageService` use this to tag envelopes they push so the client
can ack them. `request_id` is a string (not int) so v2 can grow the
format without breaking existing parsers.

---

## 7. Service registration

`AuthService` is one of the four services `BED.start()` registers
alongside any non-default router. See `bed/src/bed/main.py:461-494`.

### 7.1 Wiring order

`BED.start()` runs:

1. `await self._start_auth(db_args)` (only when auth is enabled —
   `token_persistence != "none"` AND router is not the bbsengine6
   no-credential stub).
2. `WebSocketServer(host, port)` constructed.
3. If `auth_service` was constructed, `auth_service.register_all(server)`.
4. If `MessageRouterClass` was provided, instantiate and
   `router.register_all(server)`.
5. If `--no-message-service` is NOT set, construct `MessageService`
   and `register_all(server)`.
6. If `--no-bank-service` is NOT set, construct `BankService` and
   `register_all(server)`.
7. **`PingService` is registered LAST** so its `["ping"]` registration
   wins over any router-side `["ping"]` (see [`specs/ping.md`](ping.md)
   § 3.2).

`AuthService` registers FIRST so the router and other services
register AFTER — meaning if the router registers a `["auth"]`
handler, `bbsengine6.net.transport.register_service` will log a
WARNING and overwrite. This is intentional: every bed instance
should expose the bearer-token protocol regardless of which router
is loaded.

### 7.2 Opt-out

`--token-persistence=none` disables `AuthService` entirely. The
bed startup banner prints `BED AuthService: instance=<UUID8>… token_ttl=900s`
only when the service is active.

### 7.3 `bed.json`

```json
"auth": {
  "bed_secret_path": "~/.config/bed/bed.secret",
  "token_ttl": 900,
  "token_persistence": "memory",
  "credential_provider": "password",
  "bed_instance_id": null
}
```

`bed_secret_path` is set by `_apply_bed_name_config` from `name` if
not already set; CLI `--bed-secret` always wins. The `bed_instance_id`
key is the issuing ID baked into new tokens; v2's allowlist (see
`handbook/BED_AUTH.md`) is a separate, new concept.

### 7.4 Token-aware wiring for downstream services

When auth is enabled, `BED.start()` passes the same `secret`,
`token_store`, and `instance_id` the auth service uses to the
`BankService` and `MessageService` constructors. This lets their
`_check_access` re-verify `state.auth_service_token` on every op
(defense-in-depth against a token revoked since the WS opened).
See [`specs/bank.md`](bank.md) § 5 and
[`specs/message.md`](message.md) § 4.2 for the per-service wiring
details.

When auth is disabled (legacy mode or `--token-persistence=none`),
the downstream services fall back to session-bound authorization
without token re-verification. `bbsengine6.<name>.access()` is
still called and still applies the same per-op rules.

---

## 8. Authorization — the two gates

AuthService is special: it is the only bed service that *issues*
authentication state. Its handlers do not consult `bbsengine6.auth.access`
the same way Bank / Message do (because there is no prior session
to consult yet). Instead, AuthService runs two gates in order:

1. **Wire-shape validation** — token decode + signature verify +
   expiry + instance match + store presence. Returns the existing
   per-op error codes (`token_invalid`, `token_expired`,
   `bed_instance_mismatch`, `token_revoked`). This stays in the
   handler because it touches bed's HMAC scheme.
2. **`bbsengine6.auth.access()` policy decision** — else
   `forbidden` for `reconnect` / `revoke`, else
   `not_authenticated` for `refresh` so the client can recover
   via `reconnect`. `login` always returns True — the credential
   provider decides.

### 8.1 Wire-type → domain verb

```python
_TYPE_TO_OP = {
    "auth":         "login",
    "reconnect":    "reconnect",
    "auth_refresh": "refresh",
    "auth_revoke":  "revoke",
}
```

The bbsengine6.auth package owns the verb vocabulary; this dict is
the only place the bed-side code needs to maintain the translation.

### 8.2 `_deny_envelope(op)`

Translates an `access()=False` decision into the wire-protocol
envelope. The choice of code preserves existing client semantics:

- `refresh` denial → `not_authenticated` (recoverable, client may
  try `reconnect` with its last good token).
- `reconnect` / `revoke` denial → `forbidden` (the request is
  structurally wrong for this session/token).

`login` never denies in the current policy — the credential
provider is the gate.

### 8.3 Per-op handler dispatch

```python
_OP_TO_HANDLER = {
    "login":     AuthService._handle_auth,
    "reconnect": AuthService._handle_reconnect,
    "refresh":   AuthService._handle_auth_refresh,
    "revoke":    AuthService._handle_auth_revoke,
}
```

`handle_message(server, websocket, path, message)` looks up the op
from `_TYPE_TO_OP`, returns `None` for unknown wire types (so other
services can handle them), and otherwise dispatches via the dict.

---

## 9. Server runtime — `AuthService`

File: `bed/src/bed/api/auth.py` (541 lines).

### 9.1 Construction

```python
def __init__(
    self,
    args: Any,
    session_registry: SessionRegistry,
    token_store: TokenStore,
    credential_provider: CredentialProvider,
    secret: bytes,
    instance_id: str,
    ttl_seconds: int = 900,
    *,
    clock: Optional[Any] = None,
) -> None:
```

- `args` — bed argparse namespace, threaded through to
  `bbsengine6.auth.access`.
- `session_registry` — shared `SessionRegistry`. BankService and
  MessageService read from the same instance.
- `token_store` — pluggable `TokenStore` (see Section 4).
- `credential_provider` — pluggable `CredentialProvider` (see
  Section 5).
- `secret` — HMAC bytes (32 bytes).
- `instance_id` — per-bed UUIDv4 baked into issued tokens.
- `ttl_seconds` — token validity window (default 900s = 15 minutes).
  `max(1, int(...))` so a 0 or negative value silently upgrades to 1.
- `clock` — injectable time source for deterministic expiry tests
  (mirrors `MessageService._clock`).

The parent `BaseService.__init__` wraps `session_registry` in a
`SessionManager()` (so the `BaseService.sessions` attribute stays a
`SessionManager` regardless of the registry type). `AuthService`
replaces it with the actual `SessionRegistry` because the handlers
need direct access to `bind` / `take_pending` etc. The Bank and
Message services use `SessionManager`-shaped accessors.

### 9.2 `_now`

Return the current UNIX timestamp, honoring `clock` if set.
`time.time()` (via `_now_ts`) is the production source.

### 9.3 `_authorize(op, claims, live_state)`

Delegate the per-op policy decision to `bbsengine6.auth.access`.

- `op` is the domain verb (`login` / `reconnect` / `refresh` /
  `revoke`).
- `claims` is the decoded token claims dict, or `{}` if the op
  doesn't need a token (e.g. `login`).
- `live_state` is the `SessionState` currently bound to the
  websocket (or `None` if unbound).

Returns `None` on allow, or an error envelope on deny.

### 9.4 `_mint_record(info, session_id, websocket_id, *, now=None)`

Mint a fresh `TokenRecord` with the standard claims shape:

```python
claims = {
    "version": TOKEN_CLAIM_VERSION,    # 1
    "moniker": info.moniker,
    "issued_at": ts,
    "expires_at": ts + self.ttl_seconds,
    "session_id": session_id,
    "is_sysop": bool(info.is_sysop),
    "bed_instance_id": self.instance_id,
    "websocket_id": websocket_id,
    "loginid": info.loginid,
}
```

Then `_encode_token(claims, self.secret)` produces the on-the-wire
string. `now=None` falls back to `self._now()`.

### 9.5 `_persist(record)`

`self.token_store.put(record)`. Exceptions are logged with
`scrub_token({'token': record.token})` so the token is redacted in
the log line, then re-raised so the caller can decide whether to
abort the op or roll back.

### 9.6 `_handle_auth(websocket, message)`

```json
{"type": "auth", "moniker": "alice", "password": "…"}
```

1. Empty moniker / password → `missing_credentials`.
2. `_authorize("login", {}, None)` → on deny, return the envelope
   (login never denies in the current policy, but the gate is
   checked for symmetry).
3. `self.credential_provider.authenticate(...)` → on `None`, return
   `bad_credentials`.
4. Mint a UUIDv4 `session_id`, bind the session to the websocket
   via `self.sessions.bind(...)` with `info.moniker`, `info.is_sysop`,
   `info.balance`, `info.loginid`.
5. `_mint_record(info, session_id, websocket_id)` + `_persist(record)`.
6. `state.auth_service_token = record.token`.
7. Log `"AuthService: issued token for moniker=… loginid=… session=…"`.
8. Return `_auth_result_envelope(record, info, fresh=True)`.

`_auth_result_envelope` includes `success: true`, `moniker`,
`is_sysop`, `session_id`, `token`, `expires_at` (ISO-8601), and
`balance` (`info.balance` if not None, else `0`). When `fresh=True`
it also includes `message: "Authenticated"`.

### 9.7 `_handle_reconnect(websocket, message)`

```json
{"type": "reconnect", "token": "…"}
```

1. `_decode_token(token, self.secret)` → on `TokenError`, return
   the envelope (the codec raises structured errors whose `.code`
   matches the wire `code`).
2. `token_store.get(token)` → on `None`, check if a session exists
   for the claims' `session_id` and drop its `pending_request`
   (defensive cleanup), then return `token_revoked`.
3. `store_record.bed_instance_id != self.instance_id` → delete the
   record, return `bed_instance_mismatch`.
4. `store_record.expires_at <= self._now()` → delete the record,
   return `token_expired` (recoverable).
5. `_authorize("reconnect", claims, live_state)` → on deny, return
   the deny envelope (`forbidden`).
6. Re-bind the session: `self.sessions.bind(store_record.session_id,
   new_websocket_id, store_record.moniker, store_record.is_sysop,
   loginid=store_record.loginid)`. The `session_id` survives the
   socket switch; only the websocket mapping moves.
7. Mint a rotated token (same `session_id`, new `websocket_id`).
8. `_persist(rotated)`; if it fails, delete the rotated record and
   re-raise. On success, `self.token_store.delete(token)` (delete
   the OLD record; the rotated one is now authoritative).
9. `state.auth_service_token = rotated.token`.
10. `self.sessions.take_pending(store_record.session_id)` →
    the un-acked request envelope, or `None`.
11. Log `"AuthService: reconnected moniker=… loginid=… session=…
    pending=yes|no"`.
12. Return `_reconnect_result_envelope(rotated, info, pending)` —
    the standard reconnect shape with `replayed` /
    `replayed_request_id` populated when `pending` was non-`None`.

### 9.8 `_handle_auth_refresh(websocket, message)`

```json
{"type": "auth_refresh", "token": "…"}
```

1. `websocket is None` → `not_authenticated` (recoverable).
2. Same token-decode / store-lookup / instance-match / expiry
   gates as `reconnect` (Section 9.7 steps 1-4). Failures return
   the same envelopes.
3. `_authorize("refresh", claims, live_state)` → on deny, return
   `not_authenticated` (the refresh-specific deny envelope, so the
   client knows to try `reconnect` instead of `auth`).
4. Build `MemberInfo` from the store record + claims.
5. Mint a rotated token (same `session_id`, same `websocket_id`
   because refresh keeps the original socket).
6. `_persist(rotated)`; on failure, delete the rotated record +
   re-raise. On success, `self.token_store.delete(token)`.
7. `live_state.auth_service_token = rotated.token`.
8. Return `_auth_result_envelope(rotated, info, fresh=False)`.

Refresh is the ONLY path that does NOT rebind to a new
`websocket_id`. The original socket stays live; only the token
rotates. If the original socket is gone (e.g. after a TCP reset
that the client never observed), `_authorize` will deny because
`live_state` is `None` (no session bound to this WS id), and the
client gets `not_authenticated` — the documented hint to try
`reconnect` instead.

### 9.9 `_handle_auth_revoke(websocket, message)`

```json
{"type": "auth_revoke", "token": "…"}
```

1. Empty token → `token_invalid` (with the regular error envelope
   shape, NOT the `auth_revoke_result` shape).
2. `_decode_token(token, self.secret)` → on `TokenError`, return
   `{"type": "auth_revoke_result", "success": False, "code":
   e.code, "recoverable": False}`. Note the special envelope
   shape: revoke has its own result type, not the generic `error`.
3. `_authorize("revoke", claims, None)` → on deny, return
   `{"type": "auth_revoke_result", "success": False, "code":
   CODE_FORBIDDEN, "recoverable": False}`.
4. `self.token_store.delete(token)` → returns bool.
5. Return `{"type": "auth_revoke_result", "success": bool(deleted),
   "code": None if deleted else CODE_TOKEN_REVOKED, "recoverable":
   not deleted}`.

### 9.10 Token rotation invariant

`auth_refresh` and `reconnect` both follow the same atomic
rotation sequence:

1. Build the rotated `TokenRecord` in memory.
2. `_persist(rotated)` — `token_store.put(rotated)`.
3. On `put` failure, `token_store.delete(rotated.token)` so a
   half-written rotation does not leak. Re-raise.
4. `token_store.delete(token)` — drop the OLD record. Only after
   step 2 succeeded.
5. `state.auth_service_token = rotated.token`.

This guarantees a slow client never sees "no token" between the
two events: the rotated record is live BEFORE the old one is
deleted. The DB-backed store's `INSERT … ON CONFLICT DO UPDATE`
makes step 2 atomic per row, but cross-row atomicity
(rotated-put + old-delete in one transaction) is documented as a
v2 cluster-mode fix in `handbook/BED_AUTH.md`.

### 9.11 `_auth_result_envelope` / `_reconnect_result_envelope`

`_auth_result_envelope` is the standard envelope for `auth` and
`auth_refresh`. Includes `balance: int(info.balance)` when the
provider resolved a balance, else `balance: 0`. When `fresh=True`
(only on initial `auth`), also includes `message: "Authenticated"`.

`_reconnect_result_envelope` is the reconnect-specific envelope.
Includes `replayed: pending` (the popped `pending_request` envelope,
or `None`). When `pending` was non-`None`, also includes
`replayed_request_id: pending.get("request_id")` so the client can
correlate the replay to its ack.

---

## 10. Client runtime — `BedAuthServiceClient`

File: `bed/src/bed/client/authservice.py` (209 lines).

### 10.1 Class shape

```python
class BedAuthServiceClient:
    def __init__(self, connection: BedConnection) -> None: ...
    async def login(self, moniker: str, password: str) -> Dict[str, Any]: ...
    async def reconnect(self, token: str) -> Dict[str, Any]: ...
    async def refresh(self, token: str) -> Dict[str, Any]: ...
    async def revoke(self, token: str) -> Dict[str, Any]: ...
```

Holds a `BedConnection` and translates high-level auth operations
into the auth wire protocol. Mirrors
`BedBankServiceClient` /
`BedMessageServiceClient`:

- Empty inputs are rejected locally with a soft-failure dict and no
  transport call (so the caller can branch on `code` without
  catching `BedUnavailable`).
- Server-side soft failures come back as `{"ok": False, "code": "...",
  "message": "..."}` so the caller can render a one-line error.
- Transport-level failures (no connection, timeout) are translated
  into `{"ok": False, "code": "bed_unavailable", "message": "..."}`
  rather than re-raising `BedUnavailable`.

### 10.2 `login(moniker, password)`

```python
{"type": "auth", "moniker": moniker, "password": password}
```

On success returns `{ok: True, moniker, is_sysop, session_id, token,
expires_at, balance}`. Soft failures (empty inputs, server
`error` envelope, transport down) return `{ok: False, code, message}`.

### 10.3 `reconnect(token)`

```python
{"type": "reconnect", "token": token}
```

On success returns `{ok: True, moniker, is_sysop, session_id, token,
expires_at, replayed, replayed_request_id?}`. The `replayed` field
is the popped `pending_request` envelope from `SessionRegistry`;
`replayed_request_id` is only present when `replayed` is non-None.

### 10.4 `refresh(token)`

```python
{"type": "auth_refresh", "token": token}
```

Same return shape as `login` (rotated token). The server returns
`not_authenticated` if the call is made on a different websocket;
the client surfaces that as a soft failure with
`code="not_authenticated"`.

### 10.5 `revoke(token)`

```python
{"type": "auth_revoke", "token": token}
```

Returns `{ok: True, token, code: None|str}` on success / soft
failure. The wire envelope uses the `auth_revoke_result` shape
(with a `success` flag, not `ok`); the client normalizes it to
`ok` so the caller branches on a single key.

### 10.6 Process-wide singleton

```python
_module_client: Optional[BedAuthServiceClient] = None

def get_auth_client(connection: BedConnection) -> BedAuthServiceClient:
    global _module_client
    if _module_client is None or _module_client._conn is not connection:
        _module_client = BedAuthServiceClient(connection)
    return _module_client

def reset_auth_client() -> None:
    global _module_client
    _module_client = None
```

`get_auth_client(connection)` returns a process-wide client bound
to the supplied `BedConnection`. A new connection replaces the old
client. `reset_auth_client` drops the cache (used by tests; does
not call `revoke` on the server).

---

## 11. CLI tool — `bed auth`

The `bed auth` console script is the operator-facing surface for
the bearer-token authentication protocol. It mirrors the bank /
message tool's two-backend shape but the auth surface is simpler:
there's no `direct` mode for auth (the credential provider talks
to the DB on the server side; the CLI doesn't need a local DB
lookup).

### 11.1 Entry point

```
pip install .
# registers 'auth' as a console-script entry point
```

| Script | Module                    | Purpose                                       |
|--------|---------------------------|-----------------------------------------------|
| `auth` | `bed.tools.auth:main`     | login / reconnect / refresh / revoke           |

File: `bed/src/bed/tools/auth.py` (354 lines).

### 11.2 Subcommand vocabulary

| Subcommand | Backend | Notes                                          |
|------------|---------|------------------------------------------------|
| `login`    | `bed`   | First-time login; writes token to `--token-file` |
| `reconnect`| `bed`   | Rebind saved token to a fresh WS                |
| `refresh`  | `bed`   | Rotate the saved token                         |
| `revoke`   | `bed`   | Invalidate the saved token                     |

There is no `direct` mode for any auth subcommand — auth always
talks to the bed daemon. The `_DIRECT_UNSUPPORTED_MSG` error is
returned when `--direct` is passed with any auth subcommand.

### 11.3 Backend selection

`main_with_args` (`auth.py:306-342`) resolves the backend before
any subcommand work:

1. Call `bed.tools._routing.select_backend(args)`. The bed probe is
   skipped only when `--direct` is explicitly passed (and auth
   rejects `--direct` immediately, so this branch is unreachable
   in normal use).
2. `_authenticate_ws(args)` is NOT called for auth — auth IS the
   auth flow, so the CLI handles the bearer-token read/write itself
   and does not bind a session.
3. Dispatch to the subcommand handler.

### 11.4 Token file

`auth.py` owns the token-file lifecycle:

- `_default_token_path()` — returns
  `$XDG_RUNTIME_DIR/bed.token` if set, else `/tmp/bed-<uid>/bed.token`.
- `_ensure_parent_dir(path, *, mode=0o700)` — `mkdir -p` the
  parent directory at mode 0700.
- `_check_token_file_perms(path)` — refuse to use a token file
  with group/world access (parallels `_read_secret_file`).
- `_write_token_file(path, token)` — write the token, mode 0600.
- `_read_token_file(path)` — return the token string or `""`.
- `_truncate_token_file(path)` — clear the file on `revoke` so a
  subsequent subcommand does not reuse a dead token.

### 11.5 CLI flags

```
--bed-host HOST                default: localhost
--bed-port PORT                default: 8765
--bed-path PATH                default: /
--bed-call-timeout SECONDS     default: 5.0
--bed-probe-timeout SECONDS    default: 0.25
--direct                       rejected for auth (auth always talks to bed)
--token-file PATH              default: $XDG_RUNTIME_DIR/bed.token or /tmp/bed-<uid>/bed.token
--debug                        enable debug logging
```

### 11.6 CLI subcommand handlers

- `auth_login(args)` (`auth.py:193-230`) — calls
  `BedAuthServiceClient.login(moniker, password)` (moniker / password
  prompted interactively), persists the returned token to
  `--token-file` (mode 0600), prints the standard
  `"logged in as alice (session=…)"` line.
- `auth_reconnect(args)` (`auth.py:232-260`) — reads the saved
  token, calls `BedAuthServiceClient.reconnect(token)`. On success
  persists the rotated token and prints the standard reconnect line;
  if the server returned a `replayed` envelope, prints it as a
  follow-up line so the operator can see what was replayed.
- `auth_refresh(args)` (`auth.py:262-288`) — reads the saved
  token, calls `BedAuthServiceClient.refresh(token)`. On success
  persists the rotated token and prints the standard refresh line.
- `auth_revoke(args)` (`auth.py:290-304`) — reads the saved token,
  calls `BedAuthServiceClient.revoke(token)`, truncates the
  token file so the next subcommand has to re-`auth`.

### 11.7 Token response validation

`_check_token_response(reply)` returns a list of missing fields.
The wire envelope must include `token`, `session_id`, `expires_at`
to be considered well-formed. `_reject_malformed_token_response(reply)`
is the gate that the handlers consult before persisting a token;
an envelope missing required fields is rejected with
`token_invalid` (the wire-side code, not a CLI-side one-liner).

### 11.8 No authorization on the CLI

The CLI is the operator-facing side of the auth flow; it does not
need to authorize itself. The server-side authorization is what
guards the protected ops.

---

## 12. Configuration

### 12.1 CLI flags

```
--bed-secret PATH              default: ~/.config/bed/bed.secret (or ~/.config/bed/<name>.secret)
--token-ttl SECONDS            default: 900  (15 minutes)
--token-persistence MODE       default: memory  (none | memory | db)
--credential-provider NAME     default: password  (password | moniker-only)
--bed-instance-id UUID         default: random UUIDv4 persisted with the secret
```

`--bed-secret` is the path to the HMAC-secret file (mode 0600).
`--token-ttl` is the validity window in seconds.
`--token-persistence` selects the token-store backend.
`--credential-provider` selects the credential-check backend.
`--bed-instance-id` overrides the per-bed UUID (the override is
NOT persisted back to disk — it's a one-off for tests / multi-instance
hosts).

### 12.2 `bed.json` equivalents

```json
"auth": {
  "bed_secret_path": "~/.config/bed/bed.secret",
  "token_ttl": 900,
  "token_persistence": "memory",
  "credential_provider": "password",
  "bed_instance_id": null
}
```

CLI > `bed.json` > argparse default. The `auth.*` block is merged
in the same way as `bind.*` and `database.*` via
`_apply_auth_config` (`bed/src/bed/main.py`).

### 12.3 Precedence

CLI > `bed.json` > argparse default, same as every other bed knob.

---

## 13. Error handling & failure modes

### 13.1 DB down at startup

`BED.start()` runs `db_args.pool.connection()` BEFORE constructing
`WebSocketServer`, so a DB outage at startup fails the daemon's
start sequence (the autorestart loop or systemd sees a real error).
Once the daemon is up, a DB outage during a credential lookup or a
`--token-persistence=db` `get`/`put` falls into the per-call error
envelope (`database_error`).

### 13.2 Token store unavailable

`_persist(record)` catches exceptions from `token_store.put` only
to log + scrub; it re-raises. The handler returns the standard
`database_error` envelope (the upstream token-store path raises a
generic exception, not a structured envelope).

### 13.3 Lazy GC

`InMemoryTokenStore.get` lazy-evicts expired records. A
`gc_expired(now=None)` background sweep is available for callers
that want to bound memory; the daemon does not run it by default
because the in-process store has bounded size by construction (one
record per live connection).

`DBTokenStore.gc_expired(now=None)` is a single DELETE; an explicit
`(now=...)` arg pins the cutoff so a test can drive deterministic
expiry.

### 13.4 Logout-on-TCP-reset does NOT invalidate the token

The token stays valid for `token_ttl` so reconnects can succeed.
Explicit `auth_revoke` invalidates. This is documented in the
BED_AUTH.md security notes.

### 13.5 v2 cluster-mode failure modes

See `handbook/BED_AUTH.md` § "Failure modes of a v1 cluster" for the
cross-node replay scenarios and the v2 Path A / Path B mitigations.
v1 is single-process by design.

---

## 14. Testing

### 14.1 `bed/src/bed/tests/test_auth_service.py` (~1,955 lines)

Coverage (see §13 Open work in `specs/message.md` Phase 7 plus the
bank/auth/casino-standard upgrade):

- Token codec: `_encode_token` / `_decode_token` round-trip;
  bad signature → `TokenError(CODE_TOKEN_INVALID)`;
  unparseable claims → `TokenError(CODE_TOKEN_INVALID)`;
  unsupported version → `TokenError(CODE_TOKEN_INVALID)`.
- `_handle_auth`: empty credentials → `missing_credentials`;
  bad password → `bad_credentials`; DB error → `bad_credentials`
  (defensive); success → `auth_result` envelope with `balance` from
  `member.getcredits`; `state.auth_service_token` is the new token.
- `_handle_reconnect`: invalid token → `token_invalid`;
  revoked token → `token_revoked` + drops session's
  `pending_request`; instance mismatch → `bed_instance_mismatch`
  + `token_store.delete`; expired token → `token_expired`
  (recoverable) + `token_store.delete`; success → rotated token,
  rebinds session to new `websocket_id`, replays `pending_request`
  on the envelope.
- `_handle_auth_refresh`: no live WS → `not_authenticated`;
  invalid / revoked / mismatched / expired token → same envelopes
  as reconnect; success → rotated token (same `websocket_id`),
  `live_state.auth_service_token` updated.
- `_handle_auth_revoke`: empty token → `token_invalid` envelope;
  invalid token → `auth_revoke_result {success: false, code: ...}`;
  success → `auth_revoke_result {success: true, code: None}`.
- Token rotation invariant: `put` failure deletes the rotated
  record before re-raising; success path puts rotated BEFORE
  deleting the old.
- `bbsengine6.auth.access()` delegation: `bbsengine6.auth.access`
  returns False on `session=None` (defensive); `reconnect` denial
  → `forbidden`; `refresh` denial → `not_authenticated`;
  `revoke` denial → `auth_revoke_result {success: false, code:
  "forbidden"}`.
- Secret file: `_read_secret_file` raises `InsecureSecretError`
  on group/world-readable; v1→v2 upgrade preserves the v1 bytes
  and adds a fresh UUIDv4 instance_id; `_write_secret_file` uses
  `tempfile.mkstemp` + `os.replace` for atomicity.
- Token store: `InMemoryTokenStore.get` lazy-evicts expired
  records; `DBTokenStore.put/get/delete/gc_expired` round-trip
  via a mocked connection pool.

### 14.2 `bed/src/bed/tests/test_auth_tool.py` (~1,847 lines)

CLI surface with `_auth_service` mocked:

- `buildargs` shape: `--token-file`, `--bed-*`, `--debug`,
  subcommand set `login` / `reconnect` / `refresh` / `revoke`.
- Token-file plumbing: `_default_token_path` returns the XDG /
  `/tmp/bed-<uid>` path; `_check_token_file_perms` refuses
  world/group-readable files; `_write_token_file` writes mode 0600.
- `--direct` guard: every auth subcommand rejects `--direct`
  with `_DIRECT_UNSUPPORTED_MSG`.
- `main_with_args` dispatch: `auth_login` writes the token;
  `auth_reconnect` reads + rotates + writes; `auth_refresh` reads
  + rotates + writes; `auth_revoke` reads + deletes + truncates.

### 14.3 `bed/src/bed/tests/test_auth_tool_integration.py` (~700 lines)

CLI end-to-end through real `BedAuthServiceClient` and real
token file: `auth_login` / `auth_reconnect` / `auth_refresh` /
`auth_revoke` driven through `BedServerContext` (a daemon-threaded
in-process bed server) plus `main_with_args` dispatch through
`select_backend` / `probe_bed`. Marked `@pytest.mark.integration`.

### 14.4 `bed/src/bed/tests/test_auth_integration.py` (~750 lines)

Wire-level end-to-end against a real in-process `WebSocketServer`
+ `AuthService` (login, reconnect, refresh, revoke),
`BedAuthServiceClient` envelope logic against a loopback transport,
optional live-daemon test. Marked `@pytest.mark.integration`.

### 14.5 Running the suite

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest src/bed/tests/test_auth_service.py -q
```

Integration-only tests are marked `@pytest.mark.integration` and
skipped in the default run.

---

## 15. Security

### 15.1 Threat model

- **Token theft**: a stolen token is valid until `expires_at`. A
  `reconnect` / `auth_refresh` from a different `bed_instance_id`
  is rejected (`bed_instance_mismatch`).
- **Token replay**: `bed_instance_id` baked into the token prevents
  cross-instance replay. `websocket_id` baked into the token
  forces re-`auth` after a TCP reset (the client must `reconnect`,
  which rebinds).
- **Token revocation**: explicit `auth_revoke` deletes the record.
  Background `gc_expired` keeps the store bounded.
- **Secret rotation**: rotating `--bed-secret` invalidates all
  outstanding tokens. Acceptable: forces re-`auth`, doesn't leak
  credentials.
- **Token scrub**: every debug/log site that may pass through a
  message containing a token is run through `scrub_token` first.
  See `bed/api/errors.py:52-75` for the recursive scrubber.
- **Secret file permissions**: mode 0600 enforced; `bed` refuses
  to start if the file is world- or group-readable.
- **No token logging**: tokens are never written to logs.
  `bed.api.errors.scrub_token` redacts `token`-keyed values from
  every envelope-shaped value before logging.

### 15.2 Out-of-scope (v1)

- TLS — depends on the WS deployment (TLS in reverse-proxy /
  systemd unit, plain WS in dev).
- Multi-instance load balancing (LB → different `bed_instance_id`
  → token invalidation). See `handbook/BED_AUTH.md` § "v2 roadmap:
  multi-instance load balancing" for the Path A / Path B design.
- IP / user-agent binding. Not implemented; `websocket_id`
  binding is the only binding layer.
- Sliding-window / refresh-token semantics. v1 is "one token per
  login, refreshed on demand, valid for `token_ttl`."
- Persistent session metadata (last-seen, last-IP, last-action).
  The pending-request table survives reconnects; nothing else
  does.

---

## 16. Open work

### 16.1 Phase 8 (open)

- A CLI subcommand `bed auth status` that prints the current
  `--token-file` path + the token's `expires_at` without issuing
  a request.
- A CLI subcommand `bed auth whoami` that prints the resolved
  `moniker` + `session_id` from the saved token (calls `auth`
  with the saved token and inspects the reply; does not bind a
  session).

### 16.2 Phase 9 (partial)

- Documentation that the bbsengine6 TUI can run without a local
  bed instance for read-only display, but most ops require bed.

### 16.3 v2 roadmap

- Path A (shared HMAC secret + allowlist + DB-backed token store)
  + Path B (shared `SessionRegistry` + per-connection UUID instead
  of `id(websocket)`) per `handbook/BED_AUTH.md`. Required for
  "no interactive password prompt on LB rebalance" with replay-
  on-reconnect preserved across nodes.

### 16.4 Tests (deferred)

- End-to-end secret rotation: write v1 → read v1 → upgrade to v2
  → read v2 → rotate secret → all tokens invalid.
- End-to-end clock skew: a token whose `expires_at` is in the past
  by 1 ms is rejected with `token_expired` regardless of the
  store's lazy GC.

---

## 17. File map

| File                                                   | Role                                      |
|--------------------------------------------------------|-------------------------------------------|
| `bed/src/bed/api/auth.py`                              | `AuthService`, `_encode_token`, `_decode_token`, 4 handlers, `_mint_record` |
| `bed/src/bed/api/secret.py`                            | HMAC secret file loader, v1→v2 upgrade, mode-0600 enforcement |
| `bed/src/bed/api/token_store.py`                       | `TokenStore` Protocol, `InMemoryTokenStore`, `DBTokenStore`, `TokenRecord`, `MemberInfo` |
| `bed/src/bed/api/session.py`                           | `SessionRegistry`, `SessionState`, per-table observer index |
| `bed/src/bed/api/credential_provider.py`               | `CredentialProvider` Protocol, `PasswordCredentialProvider`, `MonikerOnlyCredentialProvider`, `get_provider` |
| `bed/src/bed/api/errors.py`                            | `error_envelope`, `not_authenticated`, `forbidden`, `scrub_token`, error-code constants |
| `bed/src/bed/api/handler.py`                           | `BaseService` (shared parent)              |
| `bed/src/bed/main.py:445-535`                          | `BED.start()` wires AuthService            |
| `bed/src/bed/main.py:647-685`                          | `BED.stop()` / cleanup paths              |
| `bed/src/bed/main.py:695-744`                          | `_start_auth` (secret + token store + provider + AuthService) |
| `bed/src/bed/lib.py`                                   | `--bed-secret` / `--token-ttl` / `--token-persistence` / `--credential-provider` / `--bed-instance-id` |
| `bed/src/bed/data/bed.json:auth.*`                     | `auth` config block                        |
| `bed/src/bed/data/sql/bed_token.sql`                   | `engine.__bed_token` schema for DB-backed store |
| `bed/src/bed/client/authservice.py`                    | `BedAuthServiceClient` (login / reconnect / refresh / revoke) |
| `bed/src/bed/client/connection.py`                     | `BedConnection` (transport)                |
| `bed/src/bed/tools/auth.py`                            | `bed auth` CLI (login / reconnect / refresh / revoke) |
| `bed/src/bed/tools/_token.py`                          | Shared token-file helpers                  |
| `bbsengine6.auth.access`                               | Per-op policy function (lives in bbsengine6) |
| `bed/src/bed/tests/test_auth_service.py` (~1,955 LOC)  | Service unit tests                        |
| `bed/src/bed/tests/test_auth_integration.py` (~750 LOC) | Wire-level end-to-end (integration)      |
| `bed/src/bed/tests/test_auth_tool.py` (~843 LOC)       | CLI surface with mocked `_auth_service`   |
| `bed/src/bed/tests/test_auth_tool_integration.py` (~700 LOC) | CLI end-to-end with real WS (integration) |
| `bed/handbook/BED_AUTH.md`                             | Bearer-token protocol reference (wire-shape, error codes, v1/v2 roadmap) |
| `bed/SPEC.md`                                          | Bed daemon entry-point spec               |
| `bed/README.md`                                        | Quick-start, CLI flags, console scripts   |
| `bed/CHANGELOG.md`                                     | Release history                           |

---

## 18. Versioning

This spec tracks the bed daemon. Phase gates per `bed/SPEC.md`:

- **v1.0** (current stable) — daemon core, AuthService,
  MessageService, BankService, FHS install.
- **v1.1** (in flight) — MessageService GA + cross-repo adoption;
  AuthService rides along unchanged.
- **v1.2 / v1.3 / v1.4 / v2** — design-only; not affected by this
  spec beyond what is listed in Section 16. See `handbook/BED_AUTH.md`
  for the v2 cluster-mode roadmap.
