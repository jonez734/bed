# postoffice service — Specification

> IMAP-style mail delivery for the unified message system.

## 1. Scope

The `postoffice` service is the IMAP poller that bridges inbound
email into the bbsengine6 unified message system (`bbsengine6.
message`). It is **not** part of `bed`'s transport layer — it is
an upstream producer that feeds the same DB tables
(`engine.__message`, `engine.__message_recipient`) that the
`MessageService` consumes via PG LISTEN/NOTIFY.

This spec lives in the `bed` repo for historical reasons (it was
originally scoped here during the 2025-2026 FHS refactor when
both `bed` and `mistermcfeely` shared an installer). The actual
implementation lives in `mistermcfeely/src/postoffice/`; the
bed-side install is a thin config-key provision.

## 2. Where it lives

| Concern | Repo | Path |
|---|---|---|
| Service implementation | mistermcfeely | `mistermcfeely/src/postoffice/` |
| Config (bed.json section) | bed | `bed/data/bed.json` (top-level `postoffice` block) |
| FHS install paths | mistermcfeely | `/etc/postoffice/`, `/usr/lib/systemd/system/mcfeely-authd.service` |
| Spec | mistermcfeely | `mistermcfeely/TODO.md` (postoffice section) |

The `bed.json` `postoffice` section is registered as disabled
(`enabled: false`) so the daemon loads cleanly without
mistermcfeely installed. Operators who run postoffice enable it
in their custom `bed.json`.

## 3. Bed-side shape

```json
{
  "postoffice": {
    "enabled": false,
    "modulepath": "mistermcfeely.postoffice",
    "config_file": "/etc/postoffice/mcfeely-authd.conf"
  }
}
```

The config mirrors the `message_service` / `bank` shape in the
same `bed.json`:

- `enabled` (bool): whether to auto-load the service. Default
  `false`.
- `modulepath` (dotted name): lazy-imported via
  `bbsengine6.module.load` at `BED.start()` if `enabled` is
  `true`. The module is expected to expose a `Service` class
  matching the `BaseService` protocol.
- `config_file` (path): passed through to the service at
  construction.

## 4. Multi-instance auth (postoffice perspective)

Postoffice is the canonical example of "token-bounded IMAP-style
sessions": the mail-client holds a `bed` token instead of an
IMAP password. After a network blip or `bed` restart the client
reconnects with the token; the IMAP credentials are never sent
again over the wire.

The auth-side details are in `handbook/BED_AUTH.md` and
`specs/auth.md`; the postoffice-specific reconnection behavior
is tracked in `mistermcfeely/TODO.md`.

## 5. Decisions

- The `postoffice` service is **not** in the `bed` repo's runtime
  dependency tree; the `modulepath` is a lazy import that fails
  closed when `enabled: false` and mistermcfeely is not
  installed.
- `bed.json` ships with `enabled: false` to preserve
  out-of-the-box `bed` usability.
- Multi-instance auth Path A/B (see `handbook/BED_AUTH.md`
  §"v2 roadmap") is forward-looking; postoffice needs it when
  scaled beyond a single bed instance.

## 6. Open follow-ups

- [ ] Multi-instance auth Path A (shared signing key,
      softened instance check, shared DB token store) — see
      `handbook/BED_AUTH.md`.
- [ ] Multi-instance auth Path B (DB-backed `SessionRegistry`,
      per-connection UUID) — see `handbook/BED_AUTH.md`.
- [ ] Per-channel `key_f2_priority` for the postoffice channel —
      see `specs/key_f2.md`.

## 7. See also

- `message.md` — the consumer of postoffice output.
- `auth.md` — the bearer-token protocol that powers
  postoffice's token-bounded sessions.
- `key_f2.md` — `F2` is the natural "new mail" key for the
  postoffice mailbox.
- `../handbook/BED_AUTH.md` — bearer-token protocol reference.
- `mistermcfeely/TODO.md` — postoffice implementation status.
