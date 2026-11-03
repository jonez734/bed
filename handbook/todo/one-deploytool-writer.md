# one deploytool writer — bed

Every install target should have one writer. Builds run only in scratch copies, and each install target is written by one account. Today the zoid6 install is written by `jam` through `deploy`, so new files in `/home/zoid6` are `jam`-owned and the permission checks keep failing.

Done for zoid6 (zoid6 `5ceced8`): wheel builds and `install-user` stamp `_version.py` only in the scratch copy, so no checkout is written by a build.

Deferred until the sudo rule is in place: run the install as `zoid6` through a sudo rule that lets `jam` run only the deploy commands for zoid6 (`deploy zoid6.wheels`, `deploy zoid6.venv`, `sync-wheels`, `zoid6-perms.sh`).

## Open

- [ ] Build bed from a scratch copy only. `bed/Makefile` `version` writes `src/bed/_version.py` in the checkout; the zoid6 build now stamps the copy instead (`scripts/stamp_version.py`), so bed's own `version` target is for manual use only. Confirm no deploy path calls it.
- [ ] Deferred: the install into the zoid6 venv runs under `jam` today. Move it to a `zoid6`-run target once the sudo rule exists.
