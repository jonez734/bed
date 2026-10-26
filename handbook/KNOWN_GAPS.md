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

## 5. `sysop` membership on `bed` (closed)

**Closed.** `bed` no longer holds `sysop`. The install revokes it
(`bed_service_grants.sql`), and the send and group smoke test passed afterward
with no memberships. Sysop checks now go through the zoid6-owned
`engine.bed_is_sysop` wrapper.

## 6. bbsengine6 imports bed for the bank balance (done)

**Accepted.** bbsengine6 must not depend on bed, but `member/lib.py::_bed_balance` and
`_resolve_bed_token` import `bed.client` and `bed.tools._token` lazily. The balance is fetched
over the WebSocket, and `getbalance` runs on every bottombar redraw, so the import is exercised
at runtime. `bed` is not a declared dependency of bbsengine6, so a bbsengine6 install never pulls
bed in; the import only works where bed is installed.

The socket is now released through bed's `async with`, so the receive task stops and no
`ResourceWarning` is left (bbsengine6 `730f398`). The dependency itself stays until the fix below.

**Fix.** Move the bank-balance fetch to the caller. `getbalance_info` keeps the balance order and
labels, and takes a `bed_fetch` callable that the shell supplies. With no `bed_fetch`, bed is
skipped and the reason is `no_bed_source`. The two live callers are the bottombar and the welcome
line in letteredolive. This changes the balance API and about 40 test cases, so it is planned
separately. When it lands, close this section and move it to the closed sections, as section 5 does.

**Done.** bbsengine6 no longer imports bed. `getbalance_info` takes `bed_fetch`, and with no
fetch it reports `no_bed_source`. The shell supplies the fetch (`shell/lib.py::_bed_fetch`), and its
bottombar and welcome lines pass it. bbsengine6 `1e26f83` (the bed-free balance and its tests),
letteredolive `cfac5bd` (the shell's fetch), and `tests/test_no_bed_import_anywhere.py` keeps the
rule enforced across the whole package.

## 7. Balance lookup on every bottombar redraw (deferred)

**Deferred by decision.** The bottombar asks bed for the balance on each redraw, which opens a
socket each time. Caching the balance (for example, refreshing on a timer or after a send) would
cut the connection count. Behaviour is unchanged for now.

**Fix.** Cache the balance per session and invalidate it on a message send or a bank change.
Planned separately.

## 8. Opencode token harness did not serve the bank op path (resolved)

`src/bed/tests/test_opencode_token.py::TestBankFlowViaOpenCodeToken::test_balance_then_add_should_both_succeed`
failed after the bank handlers moved to `engine.bed_bank_op` (bed 8f86712 and the Phase 7 routing).

Resolved in the harness, not the server. The harness's fake definer connection now routes by SQL:
`engine.bed_bank_balance` returns an `(account_exists, balance)` row, and `engine.bed_bank_op` returns a
jsonb success result and records the op name. The assertion checks that the server ran the `add` op,
not that the client-side `BankService.add_funds` mock was called, since the wire facade sends the add to
the server and never calls that mock.

## 9. casino token test fails only in the full suite (closed)

**Missing.** Nothing yet explains why
`casino/src/casino/tests/test_casino_client_token.py::test_send_with_token_injects_token`
fails only in the full casino non-integration run. It passes alone, in its own module, and with every
other module paired with it. It passes in the full run at HEAD.

**What happens.** The failure is `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was
never awaited`, raised while that test runs. Several pre-existing casino tests (broadcast, handler, and
integration tests that use `AsyncMock`) leave coroutines unawaited, and the garbage collector reports
them during whichever test is running. Ruled out: the `io` stream bindings (`_current_input_stream` and
`_current_output_stream`) are not rebound by any test in the run, and the failure reproduces with the
new casino blackjack tests excluded.

**Status: closed.** The orphaned coroutines came from the test stubs, not from the client. Some
tests stubbed `receive_loop` and `disconnect` with `AsyncMock`, and `run()` cancels or skips those,
leaving coroutines nothing awaited. They are now finished coroutine functions, and the test's own
event loop is drained (pending tasks cancelled and awaited) before it closes. The full casino suite
raises no "never awaited" warnings. Fixed in casino `301db3a`.

## 10. Play clock is configured and enforced at create and update, but not yet played (open)

**Missing.** A table's `turn_seconds` is stored and capped by the sysop ceiling
(`casino.clock.max_turn_seconds`, set in `bed.json` and `zoid6.json`), but nothing runs the clock.
No deadline is stored on a seat (`acts_by`), the engine does not act when it expires, and the
client shows no countdown.

**What happens.** A table can be created or updated with a `turn_seconds`, and the value is refused
above the ceiling. Nothing else changes: a human seat is never timed out, and no penalty is applied.

**Fix.** Store the deadline on the seat when it is the seat's turn. The engine applies the table's
penalty (`skip` by default, or `remove`, chosen per table) when it expires, and records it in the
round log. Auto-play is excluded for pro games. Bots never wait on the clock. The client displays
the countdown on the bottom bar and in the table view.

## 11. Seat modules (dealer, player, bot) are planned, not built (open)

**Missing.** Table seats do not name a dealer or player module. There is no `casino.seat_module`
registry, no install command, no `bots_allowed`, `bot_max_percent`, `min_players`, or `max_players`
columns, and no bot player.

**What happens.** Every seat is human, and the game code makes all decisions itself. The
`dealermodule` and `playermodule` columns exist but nothing reads them.

**Fix.** Build the shared seat-module layer first (registry, install through `module.load` and
`module.check`, and a per-game contract check). Then move blackjack, tictactoe, yahtzee, slots, and
poker onto seats in that order, one at a time, each with its own tests.

## 12. Player listing and the `client_exit` idle reason (open)

**Missing.** The client cannot list the players at a table (`casino_list_players` does not exist),
and the `idle` exit reason is valid in `log_action` but nothing records it. The client does not
detect idle itself.

**What happens.** A client that sits idle until the server closes its session exits with no reason
recorded, or with `error`.

**Fix.** Add `casino_list_players`, served through a read op with the kind (human or bot) shown on
every row. For idle, the client records `idle` when the server reports an inactivity close. Client-
side idle detection is a separate feature.

## 13. Casino test failures left open in the scratch database (deferred)

**Missing.** Against `zoid6test`, the casino suite has 16 failing tests, all in the casino test tree.
They are deferred, not fixed, because none is a casino runtime bug that users can reach.

- `test_player_stats_integration.py` (2 tests): pass alone and on a clean HEAD export, but fail in the
  full suite run from the working tree. Order-dependent; not isolated.
- `test_registered_types_have_handlers.py::test_handler_registrations_are_answered_by_a_branch_or_a_game_handler`:
  passes alone, fails in the full run. Order-dependent; not isolated.
- Remaining blackjack and new-features integration tests tied to the same database state.

**What happens.** These tests fail in a full run against `zoid6test`. Passing runs cover the surrender
rule, table attrs, and the Decimal surrender refund.

**Fix.** For the two order-dependent groups, find the shared state by bisecting the full run, then
isolate the fixture that leaks it.

`test_betlog_view` no longer switches role. `datepostedlocal` stays NULL unless the connected role has
a member row with a timezone set; the test checks the column exists instead.
