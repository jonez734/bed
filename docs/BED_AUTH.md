# BED Auth — Bearer Tokens for the BBS Engine Daemon

## What it is

`bed` ships an `AuthService` that issues short-lived signed bearer
tokens so clients can reconnect after a network blip or `bed` restart
without re-asking for the password. Tokens are HMAC-SHA256, opaque, and
URL-safe. The whole primitive is one BED daemon process by design; for
a multi-instance deployment behind a load balancer, see "Out of scope
for v1" below.

The default `bbsengine6.net.defaultrouter.DefaultRouter` is **not**
wired to `AuthService` — it stays a no-credential stub for `wscat`
smoke tests and local development. Any other router
(`zoid6.api.handler.MonikerAuthRouter`, a real game router, …)
**is** wired automatically.

## Wire protocol

### Login

```json
C→S {"type":"auth","moniker":"alice","password":"…"}
S→C {"type":"auth_result","success":true,"moniker":"alice","is_sysop":false,
     "session_id":"…","token":"…","expires_at":"2026-06-25T11:32:00Z",
     "balance":42}
```

`balance` is included in the `auth_result` envelope for backward
compatibility with thin clients that already parse the legacy
`DefaultRouter._handle_auth` shape (it was always `0` there). It is
populated from `bbsengine6.member.getcredits` when the credential
provider can resolve it.

### Reconnect (no password)

```json
C→S {"type":"reconnect","token":"…"}
S→C {"type":"reconnect_result","success":true,"moniker":"alice",
     "is_sysop":false,"session_id":"…","token":"…",
     "expires_at":"…","replayed":null|"…",
     "replayed_request_id":"rN"|null}
```

`replayed` and `replayed_request_id` are non-`null` only when there
was an un-acked IO request on the previous socket; the client should
re-render the replayed envelope, then send a single ack for it.

### Refresh (still-valid token → new token, fresh TTL)

```json
C→S {"type":"auth_refresh","token":"…"}
S→C {"type":"auth_result","success":true,"token":"…","expires_at":"…"}
```

`auth_refresh` requires the **original** socket. A `reconnect` from a
new socket is the only path that rebinds.

### Logout

```json
C→S {"type":"auth_revoke","token":"…"}
S→C {"type":"auth_revoke_result","success":true}
```

### Errors

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

`recoverable:true` is the client's hint: it may re-`auth` (interactive
password prompt) and continue. `recoverable:false` is a hard failure
that requires the operator to investigate.

## Token shape

The on-the-wire token is a single string:

```
<payload_b64>.<hmac_hex>
```

- **payload** is a JSON object, `urlsafe_b64encode`-ed (no padding),
  containing the claims:
  - `moniker`           (string)
  - `issued_at`         (float, seconds since epoch)
  - `expires_at`        (float, seconds since epoch)
  - `session_id`        (UUID4 string, server-assigned, sticky across reconnects)
  - `is_sysop`          (bool)
  - `bed_instance_id`   (UUID4 string, server-assigned, baked in)
  - `websocket_id`      (string, `str(id(websocket))` of the issuing socket)
- **hmac** is `HMAC-SHA256(secret, payload_b64)`, hex-encoded.

The HMAC secret is 32 bytes of CSPRNG entropy generated on first run
and persisted at `~/.config/bed/bed.secret` (mode 0600). The file also
holds the per-instance UUID; rotating the secret invalidates all
outstanding tokens (acceptable: forces re-`auth`, doesn't leak
credentials).

## CLI flags

```
--bed-secret PATH              default: ~/.config/bed/bed.secret
--token-ttl SECONDS            default: 900  (15 minutes)
--token-persistence MODE       default: memory  (none | memory | db)
--credential-provider NAME     default: password  (password | moniker-only)
--bed-instance-id UUID         default: random UUIDv4 persisted with the secret
```

`bed.json` equivalents live under the `auth` key:

```json
"auth": {
  "bed_secret_path": "~/.config/bed/bed.secret",
  "token_ttl": 900,
  "token_persistence": "memory",
  "credential_provider": "password",
  "bed_instance_id": null
}
```

CLI > `bed.json` > argparse default. The `auth.*` block is merged in
the same way as `bind.*` and `database.*`.

## Token storage

- **memory** (default): in-process `Dict[token, TokenRecord]`, guarded
  by a `threading.Lock`. Tokens are lost on `bed` restart; clients
  must re-`auth`. GC is lazy (a `get` evicts the record when it
  expires) plus a 60s background `gc_expired` sweep.
- **db**: a `bed_token.sql` schema in `engine.__bed_token` (see
  `bed/data/sql/bed_token.sql`); `INSERT ... ON CONFLICT DO UPDATE`
  on issue, `SELECT WHERE expires_at > now()` on validate, `DELETE`
  on revoke/expiry. The DB is applied lazily on first use; no
  separate migration step.
- **none**: disables `AuthService` entirely. Equivalent to using
  `DefaultRouter` regardless of the `--router` choice.

## Adopting AuthService in a custom router

```python
# In a game's MessageRouter, you don't need to subclass AuthService.
# `bed.main.BED.start` registers it before your router runs, so
# `auth` / `reconnect` / `auth_refresh` / `auth_revoke` are already
# handled. Your router only needs to assume the websocket is
# authenticated.

from bed.api import SessionRegistry  # shared with AuthService

class GameRouter:
    def __init__(self, args):
        self.args = args
        self.sessions: SessionRegistry = args.bed_session_registry

    def register_all(self, server):
        server.register_service(self, ["bet", "hit", "stand"])
```

If you want to log a player out, or issue per-action challenges,
import `AuthService` and call `auth_service.token_store.delete(token)`.
The token is also stored on the per-session state at
`SessionState.auth_service_token`.

## Security notes

- HMAC secret is per-instance; rotating it invalidates all outstanding
  tokens.
- `bed_instance_id` baked into the token prevents cross-instance token
  replay. If a user is load-balanced to a different `bed`, they must
  re-`auth`.
- Tokens are bound to the issuing `websocket_id`; `reconnect`
  explicitly rebinds. `auth_refresh` does **not** rebind (and will
  return `not_authenticated` if the original socket is gone).
- Tokens are never logged. `bed.api.errors.scrub_token` redacts
  `token`-keyed values from every debug/log site.
- `--bed-secret` file is mode 0600. `bed` refuses to start if the file
  is world- or group-readable (`InsecureSecretError`).
- Token rotation is **atomic**: `auth_refresh` and `reconnect` write
  the new record before deleting the old, so a slow client never sees
  "no token" between the two events.
- Logout-on-TCP-reset does **not** invalidate the token; the token
  stays valid for `token_ttl` so reconnects can succeed. Explicit
  `auth_revoke` invalidates.

## Out of scope for v1

- Shared HMAC secret across multiple `bed` processes behind a load
  balancer. Use sticky sessions on the load balancer, or assign each
  `bed` a unique `--bed-instance-id` and accept that load-balanced
  traffic will need to re-`auth` on the new instance.
- Token binding to client IP / user-agent. Not implemented; the
  `websocket_id` binding is the only binding layer.
- Refresh-token / sliding-window semantics. v1 is just "one token
  per login, refreshed on demand, valid for `token_ttl`."
- Persistent session metadata (last-seen, last-IP, last-action). The
  pending-request table survives reconnects; nothing else does.
