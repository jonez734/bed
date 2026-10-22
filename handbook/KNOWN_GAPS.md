# bed: known gaps

Gaps that are known, understood, and deliberately not fixed yet. Each entry
says what is missing, what currently happens, and what the fix would be. A gap
stays here until it is closed; the tests that expose it are not skipped.

## 1. Bank runs without table privileges

**Missing.** `bed` holds no table privileges on the bank tables
(`bank.__account`, `bank.__transaction`, `bank.__transfer`, and their views).
Bank reads and writes run as `bed`, so they cannot reach the tables.

**What happens.** The live bank tests in
`src/bed/tests/test_bank_integration.py::TestBankOperationsLive` fail (10
tests). They connect to the running daemon, and the bank handler returns an
`error` envelope instead of a balance. These failures are expected until bank
has its own function layer. They are not environment noise.

**Fix.** Give bank the same pattern as messages: `zoid6`-owned
`SECURITY DEFINER` functions for balance, add, remove, history, and transfer,
with `bed` holding `EXECUTE` only. Each function takes the token hash and reads
the acting moniker from `engine.__bed_token`. Each needs a failing test first.

**Status.** Deferred by decision. Bank is out of scope for the current pass.

## 2. Message sends and group operations are not exposed over the WebSocket

**Missing.** The database functions exist and are granted to `bed`:
`engine.bed_message_send` and the group functions (`bed_group_create`,
`bed_group_add_member`, `bed_group_remove_member`, `bed_group_delete`). No
WebSocket operation calls them. The daemon's message handler only subscribes,
unsubscribes, and lists pending messages.

**What happens.** Clients cannot send messages or manage groups through a running
`bed` daemon. Sending still works through the `bbsengine6-msg` / `message` CLI,
which writes through bbsengine6's Python path, not through these functions.

**Fix.** Add WebSocket operations that call the functions with the session's
token hash. Needs a protocol decision and tests.

## 3. Send rules that are refused by `bed_message_send`

These are refused with an explicit error, not silently widened:

- `@here`. Needs casino's location resolver, which the database cannot see.
- Nested groups (a group whose members include another `@group`).
- Named system sources (for example `casino.dealer:<table>`).

**Fix.** Each needs its own slice with failing tests. `@here` needs a way to
supply the resolved name that does not trust the caller.

## 4. Multi-instance bed is not built

**Missing.** A token issued by one bed instance is rejected by another, because
reconnect checks `bed_instance_id` (`CODE_INSTANCE_MISMATCH`). Cluster mode
(`auth.bed_instance_ids`, `allow_cross_instance_reconnect`) is described in
`BED_AUTH.md` as planned, not built. Nothing has been tested with two instances
on one database.

**Fix.** The cluster-mode design in `BED_AUTH.md`.

## 5. `sysop` membership on `bed`

**Present.** `bed` is a member of `sysop`. `bed` is `NOINHERIT`, so this does
not grant table access directly, but it is broader than the runtime needs.

**Fix.** Remove it, after the send and group smoke test passes.
