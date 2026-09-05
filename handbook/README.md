# bed handbook

Long-form documentation for `bed` (BBS Engine Daemon).

| Doc | Purpose |
|---|---|
| [`SPEC.md`](../SPEC.md) | Entry-point spec — what bed is, status, phase gates. Start here. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Dep graph, code map, bbsengine6-side prerequisites. |
| [`BED_AUTH.md`](BED_AUTH.md) | Authoritative bearer-token wire-protocol reference. |
| [`FHS.md`](FHS.md) | FHS/UAPI install-path tree, per-service venvs, SELinux. |
| [`ADOPTERS.md`](ADOPTERS.md) | Per-game consumer pointers (empyre, casino, …). |
| [`../specs/`](../specs/) | Per-service specs (auth, message, bank, ping, echo, menu, help, key_f2, sink, postoffice). |

## See also

- `../README.md` — quickstart, CLI flags, console scripts.
- `../TODO.md` — open work.
- `../CHANGELOG.md` — release history.
- `bbsengine6/handbook/` — sibling handbook tree.
