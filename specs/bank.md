# bed BankService — Specification

> **Audience:** implementers working on `bed` and downstream
> consumers (`zoid6`, `empyre`, `casino`, `murdermotel`, `mistermcfeely`,
> the `bbsengine6` TUI). This is the entry-point spec for the
> bed-native bank service.
>
> **See also:**
> - [`SPEC.md`](../SPEC.md) — bed daemon entry point (what works, what
>   doesn't, v1/v1.1/v1.2/v1.3/v1.4/v2 phase gates, code that moved
>   to `bbsengine6`, bbsengine6-side prerequisites for each bed
>   service).
> - [`README.md`](../README.md) — quick-start, CLI flags, routers,
>   console scripts, layout.
> - [`specs/message.md`](message.md), [`specs/auth.md`](auth.md),
>   [`specs/ping.md`](ping.md) — sibling service specs.
> - `bbsengine6.bank` — the underlying bank ledger package. The
>   bed BankService is a thin bed-side layer over the bbsengine6
>   `BankService` (the actual DB-backed account / transaction /
>   transfer objects live there). This spec covers the bed-side
>   surface; the storage layer is upstream.
> - [`CHANGELOG.md`](../CHANGELOG.md) — release history.

---

## 1. Overview

`bed.BankService` is the bed-native counterpart of
`bed.MessageService`. `MessageService` is a thin bed layer over
`bbsengine6.message.get_pending_messages` and the LISTEN/NOTIFY
fanout; `BankService` is the bed-native counterpart for bank
operations, delegating actual ledger work to
`bbsengine6.bank.BankService`.

```json
C→S {"type":"bank_balance", "moniker":"alice"}
S→C {"type":"bank_balance", "moniker":"alice", "balance":42}
```

The wire shape is the empyre 9-message bank surface (`bank_balance`
/ `bank_add` / `bank_remove` / `bank_history` /
`bank_transfer_request` / `bank_transfer_approve` /
`bank_transfer_reject` / `bank_pending` / `bank_list_all`).
Games that need a different shape can fall back to the bbsengine6
router's own 9-message variant (`bed.defaultrouter.BankServiceHandler`
in `bed.defaultrouter`).

### 1.1 What this spec covers

- Wire protocol: the nine request types and their response shapes.
- Service registration: where BankService sits in `BED.start()`,
  and how to opt out with `--no-bank-service`.
- Authorization: the five-gate flow that runs on every op (session
  resolve, wire token, session token, wire-shape,
  `bbsengine6.bank.access()`).
- Server runtime: `BankService` lifecycle, the per-op handlers,
  the lazy `bbsengine6.bank.BankService` construction.
- Client runtime: `BedBankServiceClient` (get_balance / add_funds
  / remove_funds / get_history / transfer / approve_transfer /
  reject_transfer / get_pending_transfers / list_all).
- CLI tool: `bed bank` — both the WS-bound menu (the user-facing
  bank loop) and the standalone CLI flag surface.
- Configuration: `--no-bank-service`, `--bank-module`,
  `--bank-handler`.
- Testing: `test_bank_service.py` (~3,028 lines) and
  `test_bank_tool.py` (~2,581 lines).

### 1.2 What this spec does NOT cover

- The unified bank ledger's storage layer (`bbsengine6.bank.*`).
  See `bbsengine6/handbook/specs/bank.md` for that.
- The auth flow itself — see [`specs/auth.md`](auth.md). BankService
  consumes tokens minted by `bed.api.auth.AuthService` and
  re-verifies them on every op (see Section 5).
- The postoffice / message system's wire shape — see
  [`specs/message.md`](message.md).

---

## 2. Wire protocol

### 2.1 Client requests

Nine wire types. The first four (`bank_balance` / `bank_add` /
`bank_remove` / `bank_history`) are the small surface; the
transfers (`bank_transfer_request` / `bank_transfer_approve` /
`bank_transfer_reject`) add approval flow; `bank_pending` /
`bank_list_all` add the queue + sysop views.

#### 2.1.1 `bank_balance`

```json
{"type": "bank_balance", "moniker": "alice"}
```

Response:

```json
{"type": "bank_balance", "moniker": "alice", "balance": 42}
```

#### 2.1.2 `bank_add`

```json
{"type": "bank_add", "moniker": "alice", "amount": 100, "description": "credit"}
```

`description` defaults to `"credit"` when absent. Response:

```json
{"type": "bank_add", "moniker": "alice", "amount": 100, "new_balance": 142}
```

#### 2.1.3 `bank_remove`

```json
{"type": "bank_remove", "moniker": "alice", "amount": 50, "description": "debit"}
```

`description` defaults to `"debit"` when absent. Response:

```json
{"type": "bank_remove", "moniker": "alice", "amount": 50, "new_balance": 92}
```

#### 2.1.4 `bank_history`

```json
{"type": "bank_history", "moniker": "alice", "limit": 50}
```

`limit` defaults to 50. Response:

```json
{"type": "bank_history", "moniker": "alice", "transactions": [{...}, ...]}
```

Each row is the dict shape produced by `bbsengine6.bank.history`,
with `datetime` / `date` / `Decimal` values coerced via
`_jsonable` (`bank.py:103-127`).

#### 2.1.5 `bank_transfer_request`

```json
{"type": "bank_transfer_request",
 "from": "alice",
 "to": "bob",
 "amount": 100,
 "requested_by": "alice"}
```

`requested_by` defaults to the bound session's moniker when
absent. Response:

```json
{"type": "bank_transfer_request",
 "transfer_id": 7,
 "message": "Transfer 100 from alice to bob pending approval"}
```

A `bank_transfer_request` creates a pending transfer that must
be approved by the recipient (or by a sysop). The ledger
implements approval as a separate row in
`engine.__bank_transfer`; `bank_transfer_request` is the wire-side
shape.

#### 2.1.6 `bank_transfer_approve`

```json
{"type": "bank_transfer_approve",
 "transfer_id": 7,
 "responded_by": "bob"}
```

`responded_by` defaults to the bound session's moniker when
absent. Response:

```json
{"type": "bank_transfer_approve",
 "transfer_id": 7,
 "from_balance": 92,
 "to_balance": 100}
```

#### 2.1.7 `bank_transfer_reject`

```json
{"type": "bank_transfer_reject",
 "transfer_id": 7,
 "responded_by": "bob"}
```

Response:

```json
{"type": "bank_transfer_reject", "transfer_id": 7}
```

#### 2.1.8 `bank_pending`

```json
{"type": "bank_pending", "moniker": "alice", "is_sysop": false}
```

`is_sysop` from the wire is **ignored** — the server uses
`state.is_sysop` from the bound session so a non-sysop session
cannot escalate by sending `is_sysop: true`. Response:

```json
{"type": "bank_pending",
 "moniker": "alice",
 "is_sysop": false,
 "transfers": [{...}, ...]}
```

#### 2.1.9 `bank_list_all`

```json
{"type": "bank_list_all"}
```

No fields. Response:

```json
{"type": "bank_list_all",
 "accounts": [{"moniker": "alice", "balance": 92}, ...]}
```

`bank_list_all` is **sysop-only** — the per-op policy
(`bbsengine6.bank.access` for `op="list_all"`) denies non-sysop
sessions.

### 2.2 Error envelopes

Standard `{"type": "error", "code": "...", "message": "..."}`.
Bank-specific codes:

| code                  | meaning                                                              |
|-----------------------|----------------------------------------------------------------------|
| `missing_moniker`     | `moniker` empty (or `from` / `to` empty for transfer)                 |
| `invalid_amount`      | `amount` / `limit` / `transfer_id` not an int, or non-positive        |
| `operation_failed`    | `bbsengine6.bank` returned `success: False` with a human message     |
| `database_error`      | DB call raised (envelope carries traceback via `io.echo_traceback`)   |
| `not_authenticated`   | no bound session AND no wire token (legacy unauthenticated)           |
| `token_invalid`       | HMAC verify failed or shape invalid                                  |
| `token_revoked`       | token not in the `token_store`                                       |
| `bed_instance_mismatch` | token issued by a different bed instance                           |
| `token_expired`       | `expires_at` claim is in the past                                    |
| `forbidden`           | `bbsengine6.bank.access()` returned False                             |

Codes mirror the auth service so clients can reuse the same
reconnect / refresh logic.

---

## 3. Service registration

`BankService` is one of the four services `BED.start()` registers
alongside any non-default router. See `bed/src/bed/main.py:495-522`.

### 3.1 Wiring order

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
6. **If `--no-bank-service` is NOT set, construct `BankService`
   and `register_all(server)`.**
7. `PingService` is registered LAST so its `["ping"]` registration
   wins over any router-side `["ping"]`.

### 3.2 Token-aware wiring

When auth is enabled, `BED.start()` passes the same `secret`,
`token_store`, and `instance_id` the auth service uses to the
`BankService` constructor. This lets `_check_access` re-verify
`state.auth_service_token` on every bank op (defense-in-depth
against a token revoked since the WS opened).

```python
bank_kwargs: Dict[str, Any] = {}
if self.auth_service is not None and self.token_store is not None:
    bank_kwargs = {
        "secret": getattr(self.auth_service, "secret", None),
        "token_store": self.token_store,
        "instance_id": getattr(self.auth_service, "instance_id", None),
    }
self.bank_service = BankService(
    db_args, self._session_registry, **bank_kwargs
)
```

When auth is disabled (legacy mode or `--token-persistence=none`),
the bank service falls back to session-bound authorization
without token re-verification. `bbsengine6.bank.access()` is
still called and still applies the same per-op rules.

### 3.3 Opt-out

`--no-bank-service` (parsed in `bed/src/bed/lib.py`) suppresses
construction of `BankService` entirely. The bed startup banner
prints `BED BankService: bank_balance/add/remove/history` only
when the service is active. The CLI (`bed bank`) still works in
`--direct` mode (talks to the local DB through `bbsengine6.bank`)
because the storage layer is upstream of bed.

`bed.json`:

```json
"bank_service": {
  "enabled": true,
  "modulepath": "bed.api.bank",
  "description": "bed-native bank service over bbsengine6.bank"
}
```

The `enabled` flag and `--no-bank-service` CLI flag are
redundant-but-orthogonal: the config is the persistent default, the
CLI is the per-invocation override. Both are honored.

### 3.4 Standalone router fallback

`bed.defaultrouter.DefaultRouter` ships a `BankServiceHandler`
that wraps `bbsengine6.bank.api.handler.BankServiceHandler`
(the 9-message bbsengine6 router variant). For callers that
don't need the bed-native 4-message surface (e.g. a game that
needs both transfers AND the full pending-approval flow), the
defaultrouter is still available; it's just not the default
anymore.

---

## 4. Authorization — the five gates

Every op (`balance` / `add` / `remove` / `history` /
`transfer` / `approve` / `reject` / `pending` / `list_all`) runs
the same five-gate pipeline in `BankService._check_access`
(`bank.py:488-554`). The gates run in order; the first failure
short-circuits and returns the envelope to the client.

### 4.1 Gate 1 — Session resolve

`_get_or_bind_session_for(self_ref, websocket, message)`:

1. Look up the SessionState bound to `websocket.id` via
   `self_ref.sessions.get_by_websocket(ws_id)`.
2. If bound, return it.
3. If not bound, attempt a **lazy bind** from the wire token:
   - When `message["token"]` is non-empty, validate it (Gate 2)
     and synthesize a fresh `SessionState` (or rebind an existing
     `session_id` from the claims).
   - When `message["token"]` is empty/absent, return
     `not_authenticated()`.

The lazy-bind fallback exists because the CLI's `bed bank` tool
runs each bank wire call under a fresh `asyncio.run` (one per
subcommand) which closes its event loop and forces
`BedConnection` to open a new WebSocket on the next call. Each
new WebSocket is a fresh `websocket.id` in the server's eyes, so
without this fallback the session registered by the prior
`auth reconnect` would be unreachable and every bank op would
return `not_authenticated`.

### 4.2 Gate 2 — Wire token (preferred)

`_validate_wire_token(self_ref, message)` — when
`message["token"]` is non-empty:

1. `_decode_token(token, secret)` (HMAC verify, shape check). On
   `TokenError`, return the envelope (`token_invalid`,
   `token_revoked`, `bed_instance_mismatch`, etc., depending on
   the subclass).
2. Compare `claims.expires_at` to `_now()`. If expired, delete the
   record from `token_store` (best-effort, swallowed) and return
   `token_expired` (`recoverable=true`).
3. Look up the record in `token_store`. If absent, return
   `token_revoked`.
4. Compare `store_record.bed_instance_id` to `self.instance_id`.
   If different, delete the record and return
   `bed_instance_mismatch`.

On success the decoded claims dict is stashed on
`message["claims"]` so `bbsengine6.bank.access()` can prefer
claim-derived `moniker` / `is_sysop` over the in-memory session.

### 4.3 Gate 3 — Session token (fallback)

`_validate_session_token(self_ref, state)` — only runs when
Gate 2 was a no-op (wire token absent). Reads
`state.auth_service_token` (set by the auth flow at WS bind
time, or by the lazy-bind fallback above) and runs the same
`_validate_token_against_store` pipeline.

When Gate 1 ran the lazy-bind fallback it sets
`state.auth_service_token = wire_token` so subsequent ops still
have a bound snapshot.

### 4.4 Gate 4 — Wire-shape validation

`_validate_shape(op, message)` — `bbsengine6.bank.access()`
intentionally does NOT check wire-shape invariants. The handler
does, because envelope codes are a wire-protocol concern.
Per-op rules:

- `balance` / `add` / `remove` / `history` / `pending`: require
  non-empty `moniker` (`missing_moniker`).
- `add` / `remove`: require `amount` to be a positive int
  (`invalid_amount`).
- `history`: require `limit` to be a non-negative int
  (`invalid_amount`).
- `transfer`: require non-empty `from` and `to`, and a positive
  int `amount`.
- `approve` / `reject`: require a positive int `transfer_id`.

### 4.5 Gate 5 — `bbsengine6.bank.access()`

`bbsengine6.bank.access(args, op, session=state, message=message)`.
The bbsengine6.bank package owns the op vocabulary and the per-op
policy; bed is a thin consumer. The policy is documented in
`bbsengine6/handbook/specs/bank.md`; this spec does not duplicate
it.

Per-op rules at a glance (the `bbsengine6.bank.access` matrix):

| op          | self / sysop rule                                                              |
|-------------|--------------------------------------------------------------------------------|
| `balance`   | self-moniker match OR sysop                                                    |
| `add`       | self-moniker match OR sysop                                                    |
| `remove`    | self-moniker match OR sysop                                                    |
| `history`   | self-moniker match OR sysop                                                    |
| `transfer`  | self-moniker must equal `from` (or sysop); `to` may be any moniker              |
| `approve`   | self-moniker must equal `to` (the recipient); or sysop                         |
| `reject`    | self-moniker must equal `to` (the recipient); or sysop                         |
| `pending`   | non-sysop: own + counterparty transfers only; sysop: all                       |
| `list_all`  | sysop-only                                                                     |

When access returns False the handler returns a `forbidden`
envelope with code `forbidden` and message `"Operation not
permitted for this account"`.

### 4.6 Why five gates and not one

- Session lookup catches the legacy case where no token was ever
  presented (the WS was opened with `bbsengine6`'s
  `DefaultRouter` and never sent `auth`).
- Wire token (Gate 2) catches a token revoked since WS open even if
  the session-bound snapshot is stale. The CLI reads its
  `--token-file` on every subcommand, so this is the freshest view
  the server has.
- Session token (Gate 3) handles the legacy case where the WS is
  long-lived and the client does not send a per-call token.
- Wire-shape (Gate 4) keeps envelope codes (`missing_moniker` /
  `invalid_amount`) inside the wire-protocol layer, not the
  policy layer.
- `bbsengine6.bank.access()` (Gate 5) is the policy decision
  itself, encoded in the package that owns the bank ledger.

Mirrors `bed.api.message.MessageService._check_access` and
`bed.api.auth.AuthService` so a token minted by AuthService is
consumable by either service without re-implementation. The
bed-native `BankService` is the **reference implementation** for
the `bbsengine6.<name>.access()` pattern; other bed.api.* services
(auth, message, …) follow this template — see the cross-reference
in `bed/api/auth.py:1-27`.

---

## 5. Server runtime — `BankService`

File: `bed/src/bed/api/bank.py` (841 lines).

### 5.1 Construction

```python
def __init__(
    self,
    args: Any,
    session_manager: Any,
    *,
    secret: Optional[bytes] = None,
    token_store: Optional[TokenStore] = None,
    instance_id: Optional[str] = None,
    clock: Optional[Any] = None,
) -> None:
```

- `args` — bed argparse namespace, used for `make_dsn(self.args)`
  so the service shares the DB connection pool the rest of bed
  uses.
- `session_manager` — `SessionRegistry` (from `bed.api.session`),
  used by `_get_or_bind_session_for` and lazy-bind.
- `secret`, `token_store`, `instance_id` — token-aware wiring
  (Section 4). All optional; when any is missing the service falls
  back to session-only authorization.
- `clock` — injectable time source for deterministic expiry tests
  (mirrors `AuthService._clock`).

Internal state:

- `_bank: Optional[Any]` — the lazily-constructed
  `bbsengine6.bank.BankService` instance.
- `secret` / `token_store` / `instance_id` — token-aware wiring
  (Section 4).
- `_clock` — time source.

### 5.2 Lazy `_get_bank`

```python
def _get_bank(self) -> Any:
    if self._bank is None:
        self._bank = _BBSBankService(self.args)
    return self._bank
```

Construction is deferred to the first message so a transient DB
outage at bed startup does not prevent the service from being
registered (the connection is re-attempted on each call). The
import itself is at module top so a missing `bbsengine6.bank`
fails loudly at bed import time, matching the `MessageService`
convention.

### 5.3 `_check_access`

The five-gate pipeline (Section 4). Returns `(state, None)` on
success or `(state_or_None, error_envelope)` on failure. The
caller uses the returned envelope as the wire response and stops
processing.

### 5.4 `register_all(server)`

```python
def register_all(self, server: Any) -> None:
    server.register_service(self, list(self.HANDLED_TYPES))
```

`HANDLED_TYPES = tuple(_TYPE_TO_OP.keys())` — the nine wire types
listed in Section 2.1.

### 5.5 Per-op handler dispatch

```python
_TYPE_TO_OP = {
    "bank_balance":            "balance",
    "bank_add":                "add",
    "bank_remove":             "remove",
    "bank_history":            "history",
    "bank_transfer_request":   "transfer",
    "bank_transfer_approve":   "approve",
    "bank_transfer_reject":    "reject",
    "bank_pending":            "pending",
    "bank_list_all":           "list_all",
}

_OP_TO_HANDLER = {
    "balance":   BankService._handle_balance,
    "add":       BankService._handle_add,
    "remove":    BankService._handle_remove,
    "history":   BankService._handle_history,
    "transfer":  BankService._handle_transfer_request,
    "approve":   BankService._handle_transfer_approve,
    "reject":    BankService._handle_transfer_reject,
    "pending":   BankService._handle_pending,
    "list_all":  BankService._handle_list_all,
}
```

`handle_message(server, websocket, path, message)` looks up the
op, returns `None` for unknown wire types (so other services can
handle them), and otherwise dispatches via the dict. Mirrors the
`bank.py:831-841` flat-dispatch pattern; identical shape to
`bed/api/message.py:731-735` and `bed/api/auth.py:536-540`.

### 5.6 Per-op handlers

Every handler follows the same template:

1. `state, err = self._check_access(websocket, op, message)`.
2. On `err`, return the envelope (no ledger work).
3. Resolve `moniker` / `amount` / etc. from the message (with
   type coercion + default fallback).
4. Call the underlying `bbsengine6.bank.BankService` method.
5. On DB error, `io.echo_traceback("bed.api.bank._handle_<op>:")` and
   return `error_envelope(CODE_DATABASE_ERROR, ...)`.
6. On `result.success == False`, return `error_envelope(
   CODE_OPERATION_FAILED or CODE_DATABASE_ERROR, ...)`.
7. On success, return the wire-shape envelope (Section 2.1).

The per-handler dispatch is:

- `_handle_balance` — `self._get_bank().get_balance(moniker)`.
  Returns `{type, moniker, balance}`.
- `_handle_add` — `self._get_bank().add_funds(moniker, amount,
  transaction_type="credit", description=description)`.
  Returns `{type, moniker, amount, new_balance}`. Default
  description is `"credit"`; the bbsengine6 `BankService.add_funds`
  maps `transaction_type="credit"` to a credit-ledger write.
- `_handle_remove` — symmetric to `_handle_add` with
  `transaction_type="debit"`. Default description is `"debit"`.
- `_handle_history` — `self._get_bank().get_history(moniker, limit)`.
  Returns `{type, moniker, transactions: [_jsonable_row, ...]}`.
- `_handle_transfer_request` — `self._get_bank().transfer(
  from_moniker, to_moniker, amount, requested_by)`. The handler
  defaults `requested_by` to `state.moniker` when the wire omits
  it. Returns `{type, transfer_id, message}`.
- `_handle_transfer_approve` — `self._get_bank().approve_transfer(
  transfer_id, responded_by)`. Returns `{type, transfer_id,
  from_balance, to_balance}`.
- `_handle_transfer_reject` — `self._get_bank().reject_transfer(
  transfer_id, responded_by)`. Returns `{type, transfer_id}`.
- `_handle_pending` — `self._get_bank().get_pending_transfers(
  moniker, is_sysop)`. **`is_sysop` is read from `state.is_sysop`,
  NOT from the wire**, so a non-sysop session cannot escalate by
  sending `is_sysop: true` (Section 2.1.8). Returns `{type,
  moniker, is_sysop, transfers}`.
- `_handle_list_all` — `self._get_bank().list_all()`. Returns
  `{type, accounts: [{moniker, balance}, ...]}`.

### 5.7 `_jsonable` / `_jsonable_row`

JSON-safety helpers (`bank.py:103-127`):

- `datetime.datetime` → ISO 8601 string.
- `datetime.date` → ISO 8601 string.
- `Decimal` → `int` (banks track integer cents).
- Everything else passes through untouched.

Used to make DB row dicts safe for the WebSocket JSON transport.
Every ledger row that crosses the wire passes through
`_jsonable_row` so a `Decimal` field in the row does not crash
`json.dumps`.

### 5.8 Lifecycle

`BankService` is purely request/response. No background task, no
listener, no teardown beyond what `WebSocketServer` does at WS
close.

---

## 6. Client runtime — `BedBankServiceClient`

File: `bed/src/bed/client/bankservice.py` (554 lines).

### 6.1 Class shape

```python
class BedBankServiceClient:
    def __init__(self, connection: BedConnection, *, token: str = "") -> None: ...
    async def get_balance(self, moniker: str) -> Dict[str, Any]: ...
    async def add_funds(self, moniker: str, amount: int, description: str = "credit") -> Dict[str, Any]: ...
    async def remove_funds(self, moniker: str, amount: int, description: str = "debit") -> Dict[str, Any]: ...
    async def get_history(self, moniker: str, limit: int = 50) -> Dict[str, Any]: ...
    async def transfer(self, from_moniker: str, to_moniker: str, amount: int, requested_by: str) -> Dict[str, Any]: ...
    async def approve_transfer(self, transfer_id: int, responded_by: str) -> Dict[str, Any]: ...
    async def reject_transfer(self, transfer_id: int, responded_by: str) -> Dict[str, Any]: ...
    async def get_pending_transfers(self, moniker: str = "", is_sysop: bool = False) -> Dict[str, Any]: ...
    async def list_all(self) -> Dict[str, Any]: ...
```

Holds a `BedConnection` and translates high-level bank operations
into the bank wire protocol. Mirrors
`BedMessageServiceClient` / `BedAuthServiceClient`:

- Empty inputs are rejected locally with a soft-failure dict and no
  transport call.
- Server-side soft failures come back as
  `{"ok": False, "code": ..., "message": ...}` dicts.
- Transport-level failures (no connection, timeout) are translated
  into `{"ok": False, "code": "bed_unavailable", "message": "..."}`
  rather than re-raising `BedUnavailable`.

### 6.2 Per-call token injection

The optional `token=` constructor kwarg is the bearer token the
CLI read from its `--token-file` (or `$XDG_RUNTIME_DIR/bed.token`).
When non-empty, every wire message carries `"token": <token>` so
the server can re-verify it against its token store on every call,
independent of (and preferred over) the WS-bound session token.

```python
def _payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(message)
    if self._token:
        out["token"] = self._token
    return out
```

A fresh dict is built so callers don't accidentally mutate a
shared literal. The token is only injected when non-empty so
legacy callers (and tests that don't care about the wire token)
keep the old payload shape verbatim.

### 6.3 `get_balance(moniker)`

```python
{"type": "bank_balance", "moniker": moniker}
```

Returns `{ok: True, moniker, balance}` on success. Soft failures
(empty moniker, server `error` envelope, transport down) return
`{ok: False, code, message}`.

### 6.4 `add_funds(moniker, amount, description="credit")`

```python
{"type": "bank_add", "moniker": moniker, "amount": amount, "description": description}
```

Returns `{ok: True, moniker, amount, new_balance}`. Local input
validation matches the server's Gate 4 (`missing_moniker` /
`invalid_amount`).

### 6.5 `remove_funds(moniker, amount, description="debit")`

Symmetric to `add_funds` with the `bank_remove` wire type.

### 6.6 `get_history(moniker, limit=50)`

```python
{"type": "bank_history", "moniker": moniker, "limit": limit}
```

Returns `{ok: True, moniker, transactions: [...]}`. Soft failures
include `transactions: []` so callers can iterate without None-checks.

### 6.7 `transfer(from_moniker, to_moniker, amount, requested_by)`

```python
{"type": "bank_transfer_request", "from": from_moniker, "to": to_moniker,
 "amount": amount, "requested_by": requested_by}
```

Returns `{ok: True, from_moniker, to_moniker, amount, transfer_id,
message}`. Local validation rejects missing monikers / non-positive
amounts before the wire call.

### 6.8 `approve_transfer(transfer_id, responded_by)`

```python
{"type": "bank_transfer_approve", "transfer_id": transfer_id, "responded_by": responded_by}
```

Returns `{ok: True, transfer_id, from_balance, to_balance}`.

### 6.9 `reject_transfer(transfer_id, responded_by)`

```python
{"type": "bank_transfer_reject", "transfer_id": transfer_id, "responded_by": responded_by}
```

Returns `{ok: True, transfer_id}`.

### 6.10 `get_pending_transfers(moniker="", is_sysop=False)`

```python
{"type": "bank_pending", "moniker": moniker, "is_sysop": is_sysop}
```

Note: the server IGNORES the wire's `is_sysop` field and uses
`state.is_sysop` from the bound session (Section 2.1.8). The
client's `is_sysop=` arg is preserved in the request only as a
hint for clients that want to echo the session's role back to the
operator; the server makes the authoritative decision.

Returns `{ok: True, moniker, is_sysop, transfers: [...]}`.

### 6.11 `list_all()`

```python
{"type": "bank_list_all"}
```

Returns `{ok: True, accounts: [{moniker, balance}, ...]}`.

### 6.12 Process-wide singleton

```python
_module_client: Optional[BedBankServiceClient] = None

def get_bank_client(connection: BedConnection) -> BedBankServiceClient:
    global _module_client
    if _module_client is None or _module_client._conn is not connection:
        _module_client = BedBankServiceClient(connection)
    return _module_client

def reset_bank_client() -> None:
    global _module_client
    _module_client = None
```

`get_bank_client(connection)` returns a process-wide client bound
to the supplied `BedConnection`. A new connection replaces the old
client. `reset_bank_client` drops the cache (used by tests).

---

## 7. CLI tool — `bed bank`

The `bed bank` console script is the operator-facing surface for
the bed bank service. It has two modes:

- **Menu mode** (no `--direct`): a TUI menu loop driven by
  `bbsengine6.io.inputchoice`. Each letter selection calls
  `_BedBankFacade` (the WS-mode facade) or `bbsengine6.bank.BankService`
  (the direct-mode facade) and prints the result.
- **Standalone mode** (`bed bank --direct`): a one-shot
  `bbsengine6.bank.BankService` call against the local DB.

Both modes share the same wire / DB layout; the menu just adds
the interactive loop + bottombar status fragment.

### 7.1 Entry point

```
pip install .
# registers 'bank' as a console-script entry point
```

| Script | Module                    | Purpose                                       |
|--------|---------------------------|-----------------------------------------------|
| `bank` | `bed.tools.bank:main`     | balance / add / remove / history / transfer / approve / reject / pending / list_all |

File: `bed/src/bed/tools/bank.py` (966 lines).

### 7.2 Subcommand vocabulary

Both menu + standalone modes expose the same nine subcommands:

| Subcommand | WS backend | Direct backend | Notes                                              |
|------------|------------|----------------|----------------------------------------------------|
| `balance`  | `bed`      | DB             | Look up `moniker`'s balance                        |
| `add`      | `bed`      | DB             | Credit funds                                       |
| `remove`   | `bed`      | DB             | Debit funds                                        |
| `history`  | `bed`      | DB             | List recent transactions                          |
| `transfer` | `bed`      | DB             | Request a transfer (recipient must approve)        |
| `approve`  | `bed`      | DB             | Approve a pending transfer                         |
| `reject`   | `bed`      | DB             | Reject a pending transfer                          |
| `pending`  | `bed`      | DB             | List pending transfers visible to the actor        |
| `list_all` | `bed`      | DB             | Sysop-only: list every account                     |

The CLI chooses backend via `bed.tools._routing.select_backend(args)`.
All bank subcommands work in either backend; no `_DIRECT_ONLY_SUBCMDS`
override (unlike `bed message`, where DB-only ops are forced to
direct).

### 7.3 Menu loop

`menu(args, moniker)` (`bank.py:861-918`) renders the bank menu
and runs an interactive loop driven by
`bbsengine6.io.inputchoice`. The registered subcommands are:

| Key | Action |
|-----|--------|
| `B` | balance |
| `A` | add credits |
| `W` | withdraw credits |
| `T` | transfer |
| `P` | show pending transfers |
| `H` | show transaction history |
| `L` | list every account (sysop-only) |
| `Q` | quit |

`F1` / `H` re-renders the menu via the `_render_bank_menu`
help callback.

### 7.4 Bottombar fragments

`menu()` registers two `bbsengine6.bottombar` fragments on entry
and unregisters them in a `finally` block:

- **`_bank_host_fragment`** — renders `"<host>:<port>"` when the
  CLI is in WS mode, `"direct"` when `--direct` is set. Reads
  `args._backend`, `args.bed_host`, `args.bed_port` from the
  cached `_current_args`.
- **`_bank_moniker_balance_fragment`** — renders `"<moniker>:
  <balance>"` on the right side of the bottombar. Reads the cached
  `_current_moniker` / `_current_balance`. Re-queries the bank on
  `_balance_dirty` (true after transfer_* / approve / reject and
  on failure paths); transient DB failures are swallowed so a
  hiccup never blanks the bar.

Fragments are idempotent on the registry; calling
`_register_bank_fragments` twice in a row is a no-op the second
time. The fragments are unregistered on `KeyboardInterrupt` /
`EOFError` so the registry stays clean.

### 7.5 `_screen_initialized` flag

`menu()` calls `bbsengine6.io.screen.init()` exactly once per
process (mirroring `bbsengine6.ed.common.ui.init_screen`).
`screen.init()` sets the terminal scroll region (top/bottom
margins) so the bottom bar stays parked on the last line instead
of scrolling off when output overflows. `setbottombar()` positions
text on the last line, so calling it before `screen.init()` is a
no-op (the bar would be drawn but immediately scrolled away on
the next line of output).

`_clear_bottombar()` runs in the `finally` block to wipe the
bottom row so the bar does not leak past `menu()` exit:

```
{savecursor}{curpos:{height},0}{el}{reset}{restorecursor}
```

This mirrors the cleanup sequence in `empyre/__main__.py`.

### 7.6 Backend selection

`main_with_args(args)` (`bank.py:921-954`) resolves the backend:

1. `bed.tools._routing.select_backend(args)` — `"direct"` when
   `--direct` is set, else probes bed. Raises `BedNotReachable`
   when both fail.
2. In `"bed"` mode, `_authenticate_ws(args)` binds the token to
   the WS via `auth reconnect`.
3. `_resolve_moniker(args)` — pre-resolves the actor moniker
   (precedence: `--moniker` flag > claim-derived > local DB lookup).
4. `menu(args, moniker)` runs the interactive loop.

### 7.7 Authorization on the CLI

`_check_access(args, op, *, session_moniker, **message_fields)`
(`bank.py:158-198`) is the per-op gate. Builds a synthetic
`SessionState` from the claim-derived or `--moniker` flag and
delegates to `bbsengine6.bank.access(args, op, session=synth,
message=msg)`.

The session-bound gate (empty actor moniker → denial) is checked
first, mirroring the WS handler's session gate so the two
surfaces agree on what "unauthenticated" means.

`from_` is the Python keyword so the CLI uses `from_=`; the
`_FIELD_ALIASES = {"from_": "from"}` dict maps back to the wire
key.

### 7.8 Token lifecycle on the CLI

In `bed` mode:

1. `_token.ensure_token_file_arg(args)` — fills `args.token_file`
   with the XDG / `/tmp/bed-<uid>` default.
2. `_token.read_token_file(args.token_file)` — reads the bearer
   token. Missing → render the standard "no bearer token" hint and
   return False (the caller exits non-zero).
3. `BedAuthServiceClient.reconnect(token)` — rebinds the token to
   the WS. On success, the claim-derived `moniker` / `is_sysop`
   are stashed on `args` so `_check_access` can use them.
4. If the server rotated the token, write the rotated token back
   to the file at mode 0600.
5. `_resolve_call_token(args)` returns the cached token; the
   `BedBankServiceClient` is constructed with `token=resolved` so
   every wire call carries it (defense-in-depth, Section 6.2).

In `direct` mode no token is required; the actor moniker is
resolved from `--moniker` or the local DB.

### 7.9 Per-subcommand handlers

- `bank_balance` (`bank.py:540-555`) — runs `_check_access`, then
  `_bank_service(args).get_balance(moniker)`. Caches the new
  balance in the bottombar cache; dirty-flag cleared on success.
- `bank_add` (`bank.py:557-577`) — prompts for amount via
  `io.inputinteger`, runs `_check_access`, then
  `_bank_service(args).add_funds(...)`. Caches the new balance.
- `bank_remove` (`bank.py:579-599`) — symmetric to `bank_add`.
- `bank_transfer` (`bank.py:601-629`) — prompts for `to_moniker` +
  `amount`, runs `_check_access`, then
  `_bank_service(args).transfer(...)`. Marks the balance dirty (we
  don't know the new balance until the recipient approves).
- `bank_pending` (`bank.py:631-650`) — runs `_check_access`, then
  `_bank_service(args).get_pending_transfers(moniker, is_sysop)`.
- `bank_approve` (`bank.py:652-674`) — prompts for `transfer_id`,
  runs `_check_access`, then `_bank_service(args).approve_transfer(...)`.
  Marks the balance dirty.
- `bank_reject` (`bank.py:676-696`) — symmetric to `bank_approve`.
- `bank_history` (`bank.py:698-717`) — runs `_check_access`, then
  `_bank_service(args).get_history(moniker)`. Renders rows as
  `#id  type  amount=<N>  desc=<…>  by=<loginid>  at=<datestamp>`.
- `bank_list_all` (`bank.py:719-730`) — runs `_check_access` (sysop
  policy), then `_bank_service(args).list_all()`. Renders rows
  as `  <moniker>: <balance>`.

### 7.10 CLI flags

```
--bed-host HOST                default: localhost
--bed-port PORT                default: 8765
--bed-path PATH                default: /
--bed-call-timeout SECONDS     default: 5.0
--bed-probe-timeout SECONDS    default: 0.25
--direct                       run against the local DB without bed
--token-file PATH              default: $XDG_RUNTIME_DIR/bed.token or /tmp/bed-<uid>/bed.token
--moniker NAME                 target member moniker (defaults to current user)
--sysop                        bypass sysop privilege check
--debug                        enable debug logging
```

### 7.11 `bbsengine6.bank` direct-mode wiring

`_bank_service(args)` (`bank.py:520-525`) returns the right
service for the current backend:

```python
def _bank_service(args: Any) -> Any:
    backend = getattr(args, "_backend", None)
    if backend == "bed":
        return _BedBankFacade(args)
    return BankService(args)
```

The direct-mode path uses `bbsengine6.bank.BankService` (the
upstream class), which has the same `get_balance` / `add_funds` /
`remove_funds` / `get_history` / `transfer` / `approve_transfer` /
`reject_transfer` / `get_pending_transfers` / `list_all` shape.

---

## 8. Configuration

### 8.1 `bed.json` section

```json
"bank_service": {
  "enabled": true,
  "modulepath": "bed.api.bank",
  "description": "bed-native bank service over bbsengine6.bank"
}
```

The `enabled` flag and `--no-bank-service` CLI flag are
redundant-but-orthogonal: the config is the persistent default, the
CLI is the per-invocation override. Both are honored.

### 8.2 CLI flags

- `--no-bank-service` — parsed in `bed/src/bed/lib.py`. Suppresses
  `BankService` construction entirely.
- The general `--no-bank-*` shape mirrors the `--no-message-*`
  shape and keeps the dependency direction `consumer → bed` (bed
  does not know about zoid6 / empyre).

### 8.3 Precedence

CLI > `bed.json` > argparse default, same as every other bed knob.

---

## 9. Error handling & failure modes

### 9.1 DB down at startup

`BED.start()` runs `db_args.pool.connection()` BEFORE constructing
`WebSocketServer`, so a DB outage at startup fails the daemon's
start sequence (the autorestart loop or systemd sees a real error).
Once the daemon is up, a DB outage after the bank service starts
falls into the per-call `database_error` envelope path (each
handler catches `Exception`, logs via `io.echo_traceback`, and
returns the envelope).

### 9.2 Ledger returned `success: False`

The handler returns `operation_failed` for transfer_* /
approve / reject, and `database_error` for `add` / `remove` /
`balance` / `history` / `pending` / `list_all`. The bbsengine6
`BankService` does not distinguish "ledger rejected the op"
from "DB write failed" in all paths, so the wire code is a
best-effort mapping. Callers that want a precise error code
should read the result's `message` field.

### 9.3 Lazy `_get_bank` + repeated DB outage

`_get_bank` constructs the `bbsengine6.bank.BankService` once
and caches it. A transient DB outage during construction is
retried on the next call (the constructor itself is
connection-pool lazy). A persistent outage yields the same
`database_error` envelope per call.

### 9.4 Lazy-bind fallback failure

If a wire token is present but invalid (Gate 2), Gate 1 returns
the envelope (`token_invalid` / `token_revoked` /
`bed_instance_mismatch` / `token_expired`). The handler does NOT
proceed to Gates 3-5 — a session that cannot be established
cannot be authorized.

### 9.5 `bank_pending` privilege escalation

The wire's `is_sysop` field is ignored; `state.is_sysop` is
authoritative. A non-sysop session sending `"is_sysop": true`
gets the non-sysop pending list (only the user's own and
counterparty transfers). A sysop session always gets the full
list.

---

## 10. Testing

### 10.1 `bed/src/bed/tests/test_bank_service.py` (~3,028 lines)

Coverage:

- `test_bank_service_registers_handled_types` — `HANDLED_TYPES`
  matches the nine `_TYPE_TO_OP` keys.
- `test_balance_returns_balance_for_moniker` — happy path.
- `test_balance_rejects_missing_moniker` — Gate 4 returns
  `missing_moniker`.
- `test_add_credits_and_returns_new_balance` — happy path.
- `test_add_rejects_invalid_amount` — Gate 4 returns
  `invalid_amount` (negative, non-int, zero).
- `test_remove_debits_and_returns_new_balance` — happy path.
- `test_history_returns_jsonable_rows` — `Decimal` / `datetime`
  values are coerced to JSON-safe types.
- `test_transfer_request_creates_pending` — happy path; transfer_id
  echoed in the response.
- `test_approve_transfer_returns_new_balances` — happy path.
- `test_reject_transfer_returns_transfer_id` — happy path.
- `test_pending_uses_session_is_sysop_not_wire` — defense-in-depth:
  a non-sysop session sending `"is_sysop": true` gets the
  non-sysop list.
- `test_list_all_returns_accounts` — happy path; sysop-only.
- Five-gate authorization tests — wire-token preferred over
  session-token; claim-derived `moniker` / `is_sysop` preferred
  over session attributes; lazy-bind fallback from a valid wire
  token when the WS is unbound.
- `token_expired` is checked BEFORE the store lookup so a token
  whose clock has run out surfaces as `token_expired` even when
  the in-memory store's lazy-GC has purged the record.
- Token-aware wiring: a service constructed without `secret` /
  `token_store` / `instance_id` falls back to session-bound
  authorization.
- `bbsengine6.bank.access()` returns `False` on `session=None`
  even with valid claims.

### 10.2 `bed/src/bed/tests/test_bank_integration.py` (~1,580 lines)

Wire-level bank operations + optional live-daemon test. Drives
the nine wire types through a real in-process `WebSocketServer`
+ `BankService`. Marked `@pytest.mark.integration`.

### 10.3 `bed/src/bed/tests/test_bank_tool.py` (~2,581 lines)

CLI surface with `_bank_service` mocked:

- `buildargs` shape — `--bed-*`, `--direct`, `--token-file`,
  `--moniker`, `--sysop`, `--debug`.
- Menu loop: `_render_bank_menu` produces the expected lines;
  `_bank_host_fragment` / `_bank_moniker_balance_fragment` reflect
  the current `_backend` / `_current_moniker` / `_current_balance`.
- Bottombar lifecycle: fragments registered on menu() entry,
  unregistered on exit; `_screen_initialized` is set exactly once.
- `main_with_args` dispatch: `bank_balance` / `bank_add` /
  `bank_remove` / `bank_transfer` / `bank_approve` / `bank_reject`
  / `bank_history` / `bank_pending` / `bank_list_all` each drive
  through `_check_access` + `_bank_service` with mocked transport.
- `bank_pending` defense-in-depth: the wire's `is_sysop` is
  ignored; `state.is_sysop` wins.

### 10.4 Out-of-test surface (deferred)

- End-to-end `bbsengine6.bank` ledger test (requires a live PG).
- `_check_notifications`-equivalent for bank ops (no DB-hit-on-
  warm-cache; currently each bank op is a single DB round-trip).

### 10.5 Running the suite

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest src/bed/tests/test_bank_service.py -q
```

Integration-only tests are marked `@pytest.mark.integration` and
skipped in the default run.

---

## 11. Security

### 11.1 Threat model

- **Unauthenticated op**: the wire-token gate (Gate 2) and the
  lazy-bind fallback (Gate 1) reject bank ops from unbound sockets
  without a valid token. The five-gate flow closes the historical
  gap where any WS could query any moniker.
- **Cross-user ledger mutation**: `bbsengine6.bank.access()` denies
  non-sysop callers from debiting another user's balance. The
  per-op rules (`balance` / `add` / `remove` / `history` /
  `transfer` / `approve` / `reject` / `pending` / `list_all`)
  all use the self-moniker-or-sysop rule.
- **Token replay**: `bed_instance_mismatch` rejects tokens minted
  by a different bed instance; the matching record is deleted so
  the same token cannot be reused.
- **Token expiry**: `token_expired` is checked BEFORE the store
  lookup so a token whose clock has run out cannot be confused
  with a revoked token by a stale in-memory store.
- **Claim-derived authorization**: when a wire token is present,
  `bbsengine6.bank.access()` prefers the HMAC-verified
  `claims.moniker` / `claims.is_sysop` over the in-memory session
  attributes. A compromised session registry cannot elevate a
  caller past the access policy.
- **`is_sysop` escalation via `bank_pending`**: the server ignores
  the wire's `is_sysop` field and uses `state.is_sysop`. A non-sysop
  session cannot see the full pending-queue.

### 11.2 Out-of-scope

- Wire-level encryption — depends on the WS deployment (TLS in
  reverse-proxy / systemd unit, plain WS in dev).
- Rate limiting of bank ops — currently unbounded; the per-call
  token verify is the cost.
- Double-spend prevention under concurrent transfers — handled by
  `bbsengine6.bank.BankService`'s DB-level locks; not a bed concern.

---

## 12. Open work

### 12.1 Phase 8 (open)

- A `bed bank reconcile` subcommand that runs an end-of-day
  ledger reconciliation against the bbsengine6 ledger.
- A `bed bank audit <moniker>` subcommand that prints a richer
  audit trail (full ledger history, including rejected transfers).

### 12.2 Phase 9 (partial)

- Documentation that the bbsengine6 TUI can run without a local
  bed instance for read-only display, but most bank ops require
  bed (or direct mode against a local DB).

### 12.3 Tests (deferred)

- End-to-end `bbsengine6.bank` ledger integration test (requires
  a live PG).
- Defense-in-depth smoke test: rotate the saved token mid-session
  and confirm the next bank op returns `token_revoked` instead of
  driving the ledger.

### 12.4 v1.1 GA gate (per `bed/SPEC.md:251-257`)

- All 9 phases of `bed/TODO-message-service.md` checked.
- `zoid6` `bed.json` enables message service by default.
- F2 key handler in `getch.py` migrated from `message.get_queue`
  (DB) to `message_list_pending` (bed push).
- End-to-end DB LISTEN test in `test_message_lib.py`.

### 12.5 Future (v2+)

- Multi-process bed fanout — would need a shared session registry
  + token store (etcd / Redis / PG advisory locks).
- Sliding-window token expiry for bank ops (currently a fixed
  TTL; a busy account would benefit from "rotate-on-use").
- Per-op rate limiting (e.g. transfers > N per minute per session).

---

## 13. File map

| File                                                   | Role                                      |
|--------------------------------------------------------|-------------------------------------------|
| `bed/src/bed/api/bank.py`                              | `BankService`, 5-gate `_check_access`, 9 per-op handlers, lazy `_get_bank` |
| `bed/src/bed/main.py:495-522`                          | `BED.start()` wires BankService           |
| `bed/src/bed/main.py:647-685`                          | `BED.stop()` / cleanup paths              |
| `bed/src/bed/lib.py`                                   | `--no-bank-service` CLI flag              |
| `bed/src/bed/data/bed.json:bank_service.*`             | `bank_service` config block               |
| `bed/src/bed/client/bankservice.py`                    | `BedBankServiceClient` (9 methods, per-call token injection) |
| `bed/src/bed/client/connection.py`                     | `BedConnection` (transport)              |
| `bbsengine6.bank.BankService`                          | Underlying DB-backed ledger (upstream)   |
| `bbsengine6.bank.access`                               | Per-op policy function (upstream)        |
| `bed/src/bed/tests/test_bank_service.py` (~3,028 LOC)  | Service unit tests                        |
| `bed/src/bed/tests/test_bank_integration.py` (~1,580 LOC) | Wire-level end-to-end (integration)    |
| `bed/src/bed/tests/test_bank_tool.py` (~2,581 LOC)     | CLI surface with mocked `_bank_service`  |
| `bed/src/bed/tools/bank.py`                            | `bed bank` CLI (menu + standalone modes)  |
| `bed/SPEC.md`                                          | Bed daemon entry-point spec               |
| `bed/README.md`                                        | Quick-start, CLI flags, console scripts   |
| `bed/CHANGELOG.md`                                     | Release history                           |

---

## 14. Versioning

This spec tracks the bed daemon. Phase gates per `bed/SPEC.md`:

- **v1.0** (current stable) — daemon core, AuthService,
  MessageService, BankService, PingService, FHS install.
- **v1.1** (in flight) — MessageService GA + cross-repo adoption;
  BankService rides along unchanged.
- **v1.2 / v1.3 / v1.4 / v2** — design-only; not affected by this
  spec beyond what is listed in Section 12.
