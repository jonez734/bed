# bed specs

Per-service specifications for `bed` (BBS Engine Daemon).

## Implemented (v1 stable / v1.1 in flight)

| Spec | Service | Wire types |
|---|---|---|
| [`auth.md`](auth.md) | `AuthService` | `auth`, `reconnect`, `auth_refresh`, `auth_revoke`. Wire-protocol details: [`../handbook/BED_AUTH.md`](../handbook/BED_AUTH.md). |
| [`message.md`](message.md) | `MessageService` | `message_subscribe`, `message_unsubscribe`, `message_list_pending`; server-push `message`. |
| [`bank.md`](bank.md) | `BankService` | `bank_balance`, `bank_add`, `bank_remove`, `bank_history`. |
| [`ping.md`](ping.md) | `PingService` | `ping` → `pong{name, version, timestamp}`. |

## Planned (v1.2+)

| Spec | Service | Wire types |
|---|---|---|
| [`echo.md`](echo.md) | `EchoService` | `echo`, `echo_batch`, `echo_ack`, `echo_nack`, `echo_cancel`. |
| [`menu.md`](menu.md) | `MenuService` | `menu`, `menu_reply`, `menu_timeout`, `menu_cancel`. |
| [`help.md`](help.md) | `HelpService` | `help`, `help_result`, `help_error`. |
| [`key_f2.md`](key_f2.md) | `KeyF2Service` | `key_f2`, `key_f2_result`, `key_f2_empty`, `key_f2_error`. |
| [`sink.md`](sink.md) | `BEDSink` + `ThinClientIOSink` | (sink protocol; not a wire type). |
| [`postoffice.md`](postoffice.md) | postoffice | (IMAP-style; not part of bed's runtime). |

## Conventions

Each spec has:

- A header that names the service, lists the wire types, and
  points at the sibling specs and `SPEC.md`.
- A "Wire shape" / "Wire protocol" section with the JSON
  envelopes.
- A "Decisions (v1)" section listing the v1 defaults.
- An "Open follow-ups" section with live `[ ]` task boxes.
- An "Adoption" / "Adopters" section pointing at the consumer
  repos via `handbook/ADOPTERS.md`.
- A "See also" footer.

## See also

- `../SPEC.md` — entry-point spec.
- `../handbook/` — long-form docs (architecture, FHS, adopters).
- `../TODO.md` — cross-service open work.
- `bbsengine6/handbook/specs/` — sibling spec tree.
