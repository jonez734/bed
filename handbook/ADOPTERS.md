# Adopters

Per-game / per-consumer adoption pointers. Detailed file:line
call-outs are intentionally omitted — they drift. Each consumer
owns its own TODO/SPEC; this doc is the bed-side index.

| Consumer | Repo | Adopts |
|---|---|---|
| **empyre** | `empyre/` | `BedConnection`, `BedBankClient`, `BedMessageServiceClient` (planned), `BankService` (planned), `MessageService` (planned), `EchoService` (planned), `MenuService` (planned). See `empyre/TODO.md` for the phased plan. |
| **casino** | `casino/` | `BedConnection`, `BedBankClient` (per-table shape in `casino/src/casino/services/bank_client.py`), `BankService` (planned), `MessageService` (planned), `MenuService` (planned — primary driver), `key_f2` (planned). |
| **murdermotel** | `murdermotel/` | `BedConnection`, `HelpService` (planned — primary driver, lobby/play/rabidwolf help callables), `MenuService` (planned), `key_f2` (planned). |
| **mistermcfeely / postoffice** | `mistermcfeely/` | `BedConnection`, IMAP-style token-bounded sessions. Mail-client holds a token instead of an IMAP password. See `mistermcfeely/TODO.md`. |
| **zoid6** | `zoid6/` | `MonikerAuthRouter` (uses `bed.AuthService`), `MessageRouter` (auto-wired `AuthService`, `MessageService`, `BankService`, `PingService`). See `zoid6/SPEC.md` §3.2 for the auth-wiring contract that sub-routers must forward. |
| **bbsengine6 TUI** | `bbsengine6/` | `bed.MessageService` (cold-cache fallback; the warm-cache path is in-process and never touches the DB). See `bbsengine6/TODO.md` for the `--no-bed-fallback` flag work. |
| **bbsengine6.bank** | `bbsengine6/` | `BankService` (planned — `MessageRouter` adopts `EchoService` + `menu` + bearer token). |

## Adoption phases

The cross-monorepo rollout is tracked per-game in each repo's
own TODO. Bed-side, the rollout dependency is:

1. **Phase 0a** (auth) — `AuthService` is wired first in `BED.start()`,
   so any non-default router gets it for free. Adopted by zoid6.
2. **Phase 0b** (push) — `MessageService` is wired for any non-default
   router. Adopted by zoid6 + bbsengine6 TUI cache.
3. **Phase 1** (bank) — `BankService` is wired for any non-default
   router. Adoption is per-game (each game picks its bank shape).
4. **Phase 2** (IO shim) — `MenuService` + `EchoService` replace
   `bbsengine6.io.inputchoice` and ad-hoc `echo` calls. Adoption
   is per-game.
5. **Phase 3** (sink) — `BEDSink` + `ThinClientIOSink` replace the
   `sys.modules` swap. Adoption is per-game, blocked on bbsengine6
   sink infrastructure.

## See also

- `ARCHITECTURE.md` §5 — bed symbol → consumer map.
- `../specs/` — per-service specs.
- `bbsengine6/handbook/specs/` — sibling spec tree; `notify.md`
  covers the unified message system that `MessageService` rides.
