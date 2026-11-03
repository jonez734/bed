# standardized finally clause in main — bed

Every program's `finally` on exit should call `bottombar.shutdown()`, and every program's setup
should call `bottombar.init()`. Neither should use the deprecated `screen.init()` shim or a private
once-per-process flag.

Contract (bbsengine6 `bottombar`):
- `init()` runs once per process, and sets the scroll region on a tty only.
- `shutdown()` shuts down object fragments first, then, only if `init()` ran, clears the bottom bar line,
  resets colors and margins (`{reset}`), and restores the cursor. It never raises and does nothing without
  a tty.
- Interrupt and EOF messages print `{/all}*INTR*` / `{/all}*EOF*` with no cursor restore in the text, so
  `shutdown()` restores the cursor after the message.

Verify by driving the program under a pty, interrupting it, and checking that the exit bytes end in this
order: color reset, message, bar-line clear, color reset, margin reset, cursor restore.

## Open

- [ ] `src/bed/tools/auth.py`: `main_with_args` wraps `_main_with_args_run` in `finally`, calling `bottombar.shutdown()`. Confirm the interrupt message follows the `{/all}*INTR*` convention (done in c8adf0a); recheck after the next deploy.
- [ ] `src/bed/tools/bank.py`: same `finally` and interrupt convention as `auth.py`.
- [ ] Remove any remaining private once-per-process flags (`_bottombar_initialized`, `_screen_initialized`) in favor of `bottombar.init()`.
- [ ] Check `src/bed/tools/message.py` and any other `bed/tools` entry for a screen setup or interrupt path that has no `finally`.
- [ ] Deploy `bed.venv` and `zoid6.venv` to `/home/zoid6/venv`, restart zoid6, and run the pty interrupt check on `auth`.
