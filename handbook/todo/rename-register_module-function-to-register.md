# rename register_module to register — bed

bbsengine6 now provides `register(name, module_path, version, apis)` and `unregister(name)`.
`register_module` and `unregister_module` remain as thin aliases.

## Open

- [ ] Search bed (`src/`) for `register_module` and `unregister_module`. No call sites were found when
      this file was written; confirm that still holds, including docs and comments in `handbook/`.
- [ ] Update `handbook/` wording that names the old functions, if any.
- [ ] `handbook/BED_AUTH.md` mentions `MessageRouter._register_module` (zoid6's sub-router method, not
      bbsengine6's function). That name is unrelated and does not change.
