# bed menu service — Specification

> Single-pick option list with hotkeys; wire form of
> `bbsengine6.io.inputchoice`.

## 1. Why `bed` owns this

The casino menu (Blackjack "Hit / Stand / Double / Split", Poker
"Fold / Check / Call / Raise", Roulette "Inside / Outside / …"),
most empyre sub-menus (Town "Bank / Train / Tax / …", Combat
"Attack / Spy / Diplomat / …"), and the murdermotel lobby /
playground / rabidwolf menus all want the same primitive: **show
a labelled list of options with one keystroke per option, get the
pick back, and let the server define the hotkeys.** A generic
`menu` message type sits between the low-level `inputchoice`
(which is one keystroke with no menu layout) and a full `listbox`
(which supports paging, cursors, multi-column rendering,
KEY_INSERT, etc.).

Owning `menu` in `bed` gives every game:

- one canonical wire shape for "display this option list, get
  one pick",
- one place to handle `KEY_ENTER` / unknown-key / `noneok` /
  `rewriteprompt` semantics the same way door mode does,
- one place to hook into the shared `help` (F1) and `key_f2`
  (session-level) message types,
- one place to add tracing, metrics, and rate limiting.

### F1 and F2 are NOT part of the `menu` envelope

`bbsengine6.io.inputchoice` accepts a `help=<callable>` kwarg
(F1) and a `f2_handler=<callable>` kwarg (F2). Neither belongs
on the `menu` envelope:

- **`F1` (`help`)** is per-menu. It is pulled out into the
  `help` / `help_result` / `help_error` message types (see
  `help.md`). The client pulls help text on demand by sending
  `help{request_id, sub_request_id}`; the server invokes the
  callable on-demand and ships the rendered text in `help_result`.
- **`F2` is NOT a per-menu help callback.** In this monorepo,
  `F2` is the **session-level** "list new messages from subscribed
  channels" key. It is pulled out into the `key_f2` /
  `key_f2_result` / `key_f2_empty` / `key_f2_error` message types
  (see `key_f2.md`). The `inputchoice` `f2_handler` kwarg is
  **not** projected to the wire at all; no game in this monorepo
  actually passes `f2_handler=` to `inputchoice`.

The thin client's `KEY_F1` handler sends
`help{request_id, sub_request_id}`; its `KEY_F2` handler sends
`key_f2`. Two independent code paths, two independent server-side
services, no shared envelope.

## 2. Argument compatibility

The `menu` envelope is a **1:1 projection** of the *positional
and unconditional* parts of `bbsengine6.io.inputchoice`'s
signature. The two kwarg-only parts (`help` and `f2_handler`) are
pulled out into their own message types; the remaining kwargs
(`noneok`, `rewriteprompt`) stay on the envelope.

| Envelope field    | `inputchoice` parameter | Notes |
|---|---|---|
| `prompt`          | `prompt`                | The literal prompt string. |
| `options`         | `options`               | A string of valid single-character hotkeys. The client uppercases the keystroke and tests `ch in options`. |
| `default`         | `default`               | Uppercased. The hotkey returned on `KEY_ENTER` (or on `menu_timeout` server-side). |
| `noneok`          | `noneok` (kwarg)        | Boolean. If `true`, `KEY_ENTER` returns `noneok_picked:true`. |
| `rewriteprompt`   | `rewriteprompt` (kwarg) | Boolean. If `true`, the client does the one-shot `[HSDPQ] → [(H)SDPQ]` substitution. |
| ~~`help`~~        | `help` (kwarg)          | NOT on the menu envelope. Pulled into `help` round-trip. |
| ~~`f2_handler`~~  | `f2_handler` (kwarg)    | NOT on the wire. `F2` is session-level. |

Anything not in this table is **out of scope** for the `menu`
envelope. There is no `enabled`, no per-option `style`, no
per-option `hint`, no `[Q]uit` / `[X]it` / `[B]ack`
auto-convention, no `ESC`/`^C` binding.

## 3. Wire shape (v1)

```json
# Server → client: present a menu and wait for a single keystroke
S→C {"type":"menu",
     "request_id":"r100",
     "prompt":"{var:promptcolor}Blackjack — Hand #42 — Your move [{HSDPQ}] ({S}): {var:inputcolor}",
     "options":"HSDPQ",
     "default":"S",
     "noneok":false,
     "rewriteprompt":false,
     "timeout":60,
     "ts":"2026-06-25T11:31:00.000Z"}

# Client → server: the user picked a hotkey in `options`
C→S {"type":"menu_reply",
     "request_id":"r100",
     "hotkey":"H"}

# Client → server: the user hit Enter on a `noneok=true` menu
C→S {"type":"menu_reply",
     "request_id":"r100",
     "noneok_picked":true}

# Server → client: the menu timed out (informational; the client
# does NOT need to ack. The server has already resolved the future
# as if the user had picked `default`.)
S→C {"type":"menu_timeout",
     "request_id":"r100",
     "hotkey":"S"}

# Server → client: cancel a pending menu
S→C {"type":"menu_cancel",
     "request_id":"r100",
     "reason":"round_ended"}
```

## 4. Semantics

- **One keystroke per pick.** The client renders the menu and
  waits for exactly one keystroke. On a keystroke:
  - `KEY_ENTER` →
    - if `noneok=true`: client sends `menu_reply{noneok_picked:true}`.
    - else if `default != ""`: client sends `menu_reply{hotkey:<default>}`.
    - else: client rings the bell and re-prompts; no `menu_reply`
      is sent.
  - `KEY_HELP` or `KEY_F1` → client sends `help{request_id,
    sub_request_id}` (see `help.md`). The menu stays pending.
  - `KEY_F2` → client sends `key_f2` (session-level, see
    `key_f2.md`). The menu stays pending.
  - Other keys: client uppercases the keystroke. If `ch in
    options`, client sends `menu_reply{hotkey:<uppercased ch>}`.
    Otherwise, bell + re-prompt.
- **No `ESC` / `^C` handling.** The thin client does NOT send
  `cancelled:true` on `ESC` — that would be a protocol error.
- **Default on Enter / timeout.** The server treats `KEY_ENTER`
  and `menu_timeout` identically: resolve to `default` (or
  `noneok_picked` if `noneok=true`, or `cancelled` if `default ==
  ""` and `noneok` is false).
- **Server-side timeout.** `timeout` is enforced by the server
  via `asyncio.Timer`, not by the thin client. A `menu_reply`
  that arrives after `menu_timeout` is a late reply; the server
  drops it with a `logentry` debug message.
- **Hotkey collisions are a server error.** Duplicate hotkeys
  silently mask the second option. The `MenuService` validates
  this on the server side and raises `DuplicateHotkeyError` at
  send time.
- **Disabled options are not modelled.** If a server wants to
  disable an option, it omits the hotkey from `options` and
  prints a hint banner as a separate `echo` frame before the
  `menu` envelope.
- **Reconnect resume.** On `reconnect`, the server replays any
  unacked `menu` from the pending-request table.
- **Cancellation.** The server may send `menu_cancel` to
  withdraw a pending menu.

## 5. Style and layout

- v1 default: the thin client renders the `prompt` string
  verbatim. The server is responsible for any banner / hint /
  label rendering via `echo()` frames *before* the `menu`
  envelope.
- v1 default: `rewriteprompt=true` causes the client to do a
  one-shot `[HSDPQ] → [(H)SDPQ]` substitution on `prompt`.
- The wire shape is **layout-agnostic** — a future web client may
  render the same envelope as a `<select>` or a list of buttons;
  the server doesn't care.

## 6. Decisions (v1)

- `options` is a single uppercase-string of valid single-character
  hotkeys.
- `default` is the hotkey returned on `KEY_ENTER` and on
  `menu_timeout`.
- `noneok=true` + `KEY_ENTER` → `menu_reply{noneok_picked:true}`.
- The `menu` envelope does NOT include `help` or `f2_handler`
  fields.
- `timeout` is server-side enforcement via `asyncio.Timer`.
  Default 0 (no timeout) if omitted.
- `rewriteprompt=true` causes the client to do the one-shot
  substitution.
- `menu` does NOT support multi-pick.
- `menu_cancel` after a late `menu_reply` is a silent no-op on
  the server.
- The thin client never sends `cancelled:true`.
- Future: per-game style palettes, `menu_multi` primitive.

## 7. Open follow-ups

- [ ] `bed/api/menu.py` — `MenuService` (registers `menu`,
      `menu_reply`, `menu_timeout`, `menu_cancel`).
- [ ] `bed/api/menu_validator.py` — `validate_menu(envelope)`
      enforcing: `options.isalpha()`,
      `len(options) == len(set(options.upper()))`,
      `default == "" or default.upper() in options.upper()`,
      `timeout >= 0`, `noneok in (true, false)`,
      `rewriteprompt in (true, false)`.
- [ ] `bed/api/menu_timeout.py` — `asyncio.Timer`-based
      server-side timeout enforcement + three resolution paths.
- [ ] `bed.main.BED.start` — register `MenuService` after
      `EchoService` and before any game router.
- [ ] `bed/tests/test_menu_service.py`, `test_menu_validator.py`,
      `test_menu_timeout_server_side.py`.
- [ ] `bed.json` `menu.timeout` config + CLI flag
      `--menu-timeout`.

## 8. Adoption

- **casino** (primary driver): replace every
  `bbsengine6.io.inputchoice` call in blackjack/poker/roulette
  /lobby with a `menu` envelope.
- **murdermotel**: top-level menu + per-mode help (see `help.md`).
- **empyre**: top-level menu + sub-menus (town, combat, dock,
  shipyard, investments).
- **zoid6**: dashboard "switch to casino / switch to empyre /
  check bank / log out" picker.
- **mistermcfeely (postoffice)**: "read / reply / forward /
  delete / next" folder menu.
- **bbsengine6 bank service**: "balance / deposit / withdraw /
  transfer / history" picker.

## 9. See also

- `help.md` — sibling F1 help-on-demand service.
- `key_f2.md` — sibling session-level F2 new-messages query.
- `echo.md` — sibling push channel.
- `sink.md` — sibling BEDSink / ThinClientIOSink.
- `../handbook/ARCHITECTURE.md` — dep graph.
