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

## v2 roadmap: multi-instance load balancing

v1 was designed around a single `bed` process. Three walls in the
current code path make a stock round-robin load balancer force every
player to re-authenticate on rebalance, and a fourth wall (session
state) limits what "no re-auth" can mean even if the first three are
fixed. This section is a design sketch for the v2 work, derived from a
read of the v1 source as of the date of this doc.

### What the v1 code actually binds to a single process

1. **HMAC secret is per-instance.** `bed/api/secret.py:121-156`
   generates 32 CSPRNG bytes on first run and writes them to
   `~/.config/bed/bed.secret`. The only entry point used by
   `bed/main.py:206` is `load_or_create_secret`. There is no code
   path that loads a *shared* secret from a different source.
2. **Instance check is a strict equality.** `bed/api/auth.py:258-264`
   (in `_handle_reconnect`) and `bed/api/auth.py:315-321` (in
   `_handle_auth_refresh`) both reject with `bed_instance_mismatch`
   and call `self.token_store.delete(token)` whenever
   `store_record.bed_instance_id != self.instance_id`. There is no
   allowlist, and the delete is hostile in cluster mode (a node
   that is on the allowlist should not be evicting another node's
   record).
3. **Token store is per-process by default.** `bed/api/token_store.py:56-106`
   is an in-process `Dict` guarded by a `threading.Lock`. The
   DB-backed variant (`bed/api/token_store.py:112-231`) exists and
   is correct, but it is opt-in via `--token-persistence=db`, not
   the default.
4. **`SessionRegistry` is per-process and `websocket_id` is
   process-local.** `bed/api/session.py:33-99` holds
   `SessionState` in two in-process dicts. `websocket_id` is
   `str(id(websocket))` (`bed/api/auth.py:220, 240, 302`), and
   `id()` only has meaning inside the process that allocated the
   object. On the wrong node, the registry is empty for the
   incoming socket, and the `pending_request` envelope that
   `reconnect_result` is supposed to replay is lost.

### Failure modes of a v1 cluster

| Scenario                                                | v1 outcome                                                                  |
|---------------------------------------------------------|-----------------------------------------------------------------------------|
| LB rebalances a live socket to a sibling node           | `bed_instance_mismatch` → `recoverable:false` → client re-`auth` prompt.    |
| `bed` restarts (and any sibling takes traffic)          | `InMemoryTokenStore` is empty → `token_revoked` → client re-`auth` prompt.  |
| `auth_refresh` reaches a non-issuing node               | `auth.py:330-336` finds no `SessionState` for this `websocket_id` → returns `not_authenticated`. Client has to fall back to `reconnect`, which then hits the instance check. |
| `reconnect` reaches a non-issuing node                  | `_handle_reconnect` re-binds the `session_id` to a fresh local `SessionState`, but `take_pending` (`auth.py:289`, `session.py:116-123`) finds `None` because the registry is local. The `replayed` / `replayed_request_id` envelope is silently dropped. |

### Path A: minimum viable, "no interactive password prompt on rebalance"

The smallest patch that meets the "players don't re-auth on LB
rebalance" bar. Ships without a shared `SessionRegistry`, at the
cost of giving up cross-node replay-on-reconnect.

**A1. Shared signing key.** Extend `secret.py` so the HMAC bytes
can be sourced from somewhere other than a per-node file:

- New CLI: `--bed-secret-source {file,env}` (or an extended syntax
  on `--bed-secret`, e.g. `env:NAME`).
- `env` mode reads the key from an environment variable, refuses to
  start if the variable is unset, shorter than `SECRET_HEX_LEN`, or
  not valid hex.
- The 0600 permission check (`secret.py:64-71`,
  `InsecureSecretError`) still applies: the *file backing the
  injected secret* (if any) must be mode 0600, or the value must
  come from a source the operator attests to (env / KMS).
- The on-disk v2 JSON format (`secret.py:18-21, 76-103`) is already
  trivially portable; the read path is unchanged.
- `bed_instance_id` in the secret file is the *issuing* ID baked
  into new tokens, not a per-node secret. The current `--bed-instance-id`
  flag remains a per-node override for the issuing ID; the
  accept set is a separate, new concept.

**A2. Softened instance check.** `AuthService` accepts a set of
allowed IDs at construction time and the two reject sites gate on
it:

- New `auth.allowed_instance_ids` config (list in `bed.json`,
  `--bed-instance-ids` CLI flag) merged through `_apply_auth_config`
  (`bed/main.py:90-115`) with the same precedence as the other
  `auth.*` keys.
- `auth.py:258-264` and `auth.py:315-321`: change
  `store_record.bed_instance_id != self.instance_id` to
  `store_record.bed_instance_id not in {self.instance_id, *self.allowed_instance_ids}`.
- Move `self.token_store.delete(token)` behind "not in the
  expanded set" (a true foreign token should still be evicted; a
  sibling's token should not).
- Optional stricter mode: a removed-from-allowlist ID triggers
  delete; lax mode (default) just stops accepting. Pick one and
  document the policy.

**A3. Shared token store.** `token_persistence=db` becomes
required in cluster mode rather than opt-in:

- In `BED._start_auth` (`bed/main.py:199-241`), when
  `--bed-secret-source=env` (or whatever opt-in is chosen) is on,
  refuse to start with `token_persistence=memory`. The error
  message points at `bed/data/sql/bed_token.sql` (referenced from
  `bed/api/token_store.py:140-144`).
- `DBTokenStore` (`bed/api/token_store.py:112-231`) is already
  correct for the cross-node get/put/delete contract; no changes
  to its SQL.
- The "atomic rotation" guarantee in this doc (above) currently
  rests on two sequential calls (`auth.py:285-287, 344-346`); wrap
  `put` + `delete` in a single transaction in `DBTokenStore` so
  the cluster-wide invariant holds. Without this, a slow client
  can briefly see "no token" on a sibling node.

**A4. Cross-node `reconnect` is a clean rebind.** Detect the
"wrong node" case explicitly and skip `take_pending`:

- In `_handle_reconnect` (`bed/api/auth.py:236-296`), if
  `self.sessions.get_by_session(claims["session_id"])` is `None`
  *and* `self.allowed_instance_ids` is non-empty (i.e. we're in
  cluster mode), skip the `take_pending` call and always return
  `replayed: null`, `replayed_request_id: null`. The `session_id`
  is preserved (it's the stable handle from the issuing node);
  only the local `SessionState` is recreated.
- For `auth_refresh` (`bed/api/auth.py:298-347`), document that
  it is best-effort and clients should handle `not_authenticated`
  by issuing a fresh `reconnect` (not a fresh `auth` with a
  password). The wire-level error code already has
  `recoverable: true` (`BED_AUTH.md:73, 80-82`), so the contract
  is consistent; the client just needs to know to try
  `reconnect` before `auth`.

### Path B: full shared `SessionRegistry` (v2+)

Required to preserve the replay-on-reconnect feature across nodes.
Significantly more code than Path A; should be a follow-up, not a
prerequisite for "no re-auth on rebalance."

- Replace `bed/api/session.py:33-99` with a DB-backed registry
  mirroring `DBTokenStore` (per-`session_id` row carrying
  `pending_request`, `request_id_counter`, `auth_service_token`).
- Stop using `id(websocket)` for binding. Assign a per-connection
  UUID at WebSocket upgrade and use that as `websocket_id` in
  both `auth.py` (`auth.py:220, 240, 302`) and the registry. The
  current `str(id(websocket))` only has meaning inside the
  process that allocated the object and will collide across
  processes.
- `next_request_id` (`session.py:101-107`) becomes a
  `SELECT … FOR UPDATE`-style increment in the shared store.
  This is a hot path; benchmark before shipping.

### New flag / config surface

To stay consistent with the existing precedence rule (CLI >
`bed.json` > argparse default, applied via `_apply_auth_config` at
`bed/main.py:90-115`), the v2 additions should land in all three
seams:

- `--bed-secret` extended syntax (or `--bed-secret-source
  {file,env}`) for shared-key sourcing.
- `auth.bed_instance_ids` (list) / `--bed-instance-ids ID1,ID2,…`
  for the accept set. `auth.bed_instance_id` remains the issuing
  ID baked into new tokens.
- `auth.allow_cross_instance_reconnect` (bool) for Path A's
  "skip take_pending on sibling" behavior. Default `true` when
  `bed_instance_ids` is set; default `false` otherwise.
- `auth.token_persistence` is unchanged as a value, but the
  cluster-mode doc must call out that `db` is required (not the
  default) and that `bed/data/sql/bed_token.sql` is applied
  lazily on first use.

### Things to fix in passing (not blocking)

- `bed/api/auth.py:378` `_now_iso()` recomputes "now" instead of
  using `record.expires_at`. The token is signed with
  `ts + ttl_seconds` (`auth.py:153-155`), but the wire envelope
  says "now." Cosmetic in single-process; with cluster time
  skew, weird. Fix: emit `record.expires_at` as ISO.
- `bed/api/auth.py:285-287, 344-346` perform `put` and `delete`
  as two separate implicit transactions. With a shared DB
  store, wrap them in one explicit transaction (see Path A3).
- `bed/api/auth.py:308-321` runs the instance check after
  `token_store.get(token)`, so cross-node validation is now a
  real round trip. Latency is fine for `auth` / `reconnect`
  (rare) but every TTL expiry across the fleet is one
  round trip; consider a small in-process LRU cache keyed by
  `(token, bed_instance_id) → TokenRecord` for the
  cluster case.

### Recommended order of work

1. **Stop and check whether sticky sessions are an option.** This
   doc's v1 guidance (`Out of scope for v1`, above) is still the
   right answer for most deployments. It is a load-balancer
   config change, not a `bed` change.
2. **If sticky sessions are not viable, ship Path A (A1-A4) as
   v2.0.** It is enough for "no interactive password prompt on
   rebalance" and explicitly gives up cross-node replay.
3. **Land Path B as v2.1 or later.** Only needed if replay
   survival across nodes is a hard requirement; the cost is a
   shared `SessionRegistry` and a per-connection UUID replacing
   `id(websocket)`.
