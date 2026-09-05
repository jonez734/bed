# bed help service — Specification

> F1 per-menu help pulled on demand.

## 1. Why this is its own message type

`F1` (a.k.a. `KEY_HELP`) is the per-menu help key. The `help`
text is **not** shipped in the `menu` envelope; it is pulled on
demand via the `help` round-trip. This is a deliberate bandwidth
optimization: most users never press `F1`, and the rendered help
text (especially for murdermotel's `playgroundhelp` /
`lobbyhelp` / rabidwolf `help` callables) can be 1–4 KB per
menu. Shipping it eagerly would waste bytes on every menu the
user sees.

`help` is keyed by the outer `menu.request_id` plus a
`sub_request_id` that is locally unique to the help exchange.
The client may pipeline multiple help requests for the same
outer `request_id` (each with a different `sub_request_id`); the
server processes them in order.

`help` is **complementary** to `menu`: it is a sub-request of a
pending `menu`; the client sends `help{request_id,
sub_request_id}` while the menu is still pending, the server
invokes the callable on demand, and the result is rendered then
the menu re-prompts.

## 2. Wire shape

```json
# Client → server: I need the help text for menu request_id r100
C→S {"type":"help",
     "request_id":"r100",
     "sub_request_id":"r-help-7",
     "ts":"2026-06-25T11:31:42.000Z"}

# Server → client: here is the rendered help text
S→C {"type":"help_result",
     "request_id":"r100",
     "sub_request_id":"r-help-7",
     "text":"[G]ame instructions\n[C]redits\n[Q]uit",
     "rendered_at":"2026-06-25T11:31:42.123Z"}

# Server → client: the help request can't be served
S→C {"type":"help_error",
     "request_id":"r100",
     "sub_request_id":"r-help-7",
     "code":"menu_resolved" | "no_help" | "help_rate_limited",
     "message":"the menu for request_id r100 has already been answered"}
```

## 3. Semantics

- `help` is keyed by the outer `menu.request_id` plus a
  `sub_request_id` that is locally unique to the help exchange.
  The client may pipeline multiple help requests for the same
  outer `request_id`; the server processes them in order.
- The server rejects `help` for a `request_id` that is not
  currently pending (the menu has already been answered, timed
  out, or cancelled) with `help_error{code:"menu_resolved"}`.
- The server rejects `help` for a `request_id` that has no help
  configured (the call site did not pass `help=<callable or
  string>`) with `help_error{code:"no_help"}`.
- The server rate-limits `help` per session (default 10 req/s,
  configurable via `bed.json` `help.rate_limit` and CLI flag
  `--help-rate-limit`); over-limit requests get
  `help_error{code:"help_rate_limited"}`.
- `help` does **not** resolve the menu's outer `request_id`. The
  user still has to press a hotkey (or `KEY_ENTER`) to answer
  the menu.
- The `MenuAdapter` invokes the `help=<callable>` **on demand**
  at `help` time, not eagerly at menu-send time. The callable is
  called server-side with the forwarded `**kwargs` and its
  `io.echo` output is captured into the `help_result.text`
  string. Staleness window is **zero**.
- A `help_request` that arrives after the menu has been resolved
  (timeout, cancel, or `menu_reply`) is a
  `help_error{code:"menu_resolved"}`.

## 4. Decisions (v1)

- `help` is pulled on demand via the round-trip, not shipped
  eagerly with the `menu` envelope.
- The callable is invoked **on demand** at request time, not
  eagerly at menu-send time. Staleness window is zero.
- `help` is rate-limited per session at 10 req/s (configurable
  via `bed.json` `help.rate_limit` and CLI flag
  `--help-rate-limit`).
- The `sub_request_id` is locally unique to the help exchange;
  the client may pipeline multiple help requests for the same
  outer `menu.request_id` (each with a different
  `sub_request_id`).

## 5. Open follow-ups

- [ ] `bed/api/help.py` — `HelpService` (registers `help`,
      `help_result`, `help_error`).
- [ ] Per-outer-`request_id` help future in the session registry.
- [ ] `bed.main.BED.start` — register `HelpService` after
      `MenuService` and before any game router.
- [ ] `bed/tests/test_help_service.py` — pull help for a pending
      menu (string help / callable help / callable invoked on
      demand), error on resolved menu, error on no-help menu,
      error on rate-limit, pipelined help requests for distinct
      menus, pipelined help requests for the same menu
      (different `sub_request_id`s), late `help` after
      `menu_cancel` is `menu_resolved`.
- [ ] `bed/tests/test_help_callable.py` — murdermotel case:
      `playgroundhelp(**kwargs)` invoked at `help` time,
      captures `io.echo` output, ships as `help_result.text`.
      Assert callable output reflects LIVE state (mutate
      `player.weapons()` between menu-send and `help` request;
      assert new help text reflects the mutation).
- [ ] `bed.json` `help.rate_limit` config + CLI flag
      `--help-rate-limit`.

## 6. Adoption

- **murdermotel** (primary): three callable-`help` call sites —
  `lobby.py:74` `help=lobbyhelp`, `play.py:446`
  `help=playgroundhelp`, `rabidwolf.py:531` `help=help`. The
  murdermotel `MenuAdapter` stashes the callable + `kwargs`
  server-side. On `help` request, the `HelpService` invokes the
  callable on demand.
- **casino**: secondary — most call sites pass a string `help=`;
  some pass nothing. Where `help=` is passed, the string is
  shipped in `help_result.text` directly.
- Other consumers adopt `help` opportunistically when their
  `inputchoice` call sites pass `help=<callable>`.

## 7. See also

- `menu.md` — parent envelope; help is a sub-request of menu.
- `key_f2.md` — sibling session-level F2 new-messages query.
- `echo.md` — sibling push channel.
- `../handbook/ARCHITECTURE.md` — dep graph.
