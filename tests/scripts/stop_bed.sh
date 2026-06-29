#!/usr/bin/env bash
# Stop a bed process via its pidfile. Sends SIGTERM, waits up to 5s for
# graceful shutdown, then escalates to SIGTERM on the process group, and
# finally SIGKILL on the process group as a last resort. Mirrors the
# systemd KillSignal=SIGTERM + TimeoutStopSec=30s behavior scaled down
# to a reasonable test duration.
#
# Usage: stop_bed.sh <pidfile>
#
# Returns 0 on success (process gone), non-zero otherwise. Idempotent:
# missing or empty pidfile is treated as success (nothing to stop).
set -e

pidfile="${1:?usage: stop_bed.sh <pidfile>}"
if [ ! -f "$pidfile" ]; then
  echo "stop_bed.sh: no pidfile at $pidfile, nothing to stop" >&2
  exit 0
fi
pid="$(cat "$pidfile" 2>/dev/null || true)"
if [ -z "$pid" ]; then
  echo "stop_bed.sh: empty pidfile $pidfile, nothing to stop" >&2
  exit 0
fi

# SIGTERM to the main pid. bed's signal handler at
# bed/src/bed/main.py:370-373 calls asyncio.create_task(bed.stop()),
# which awaits self.server.stop() and runs the pidfile cleanup.
kill -TERM "$pid" 2>/dev/null || {
  echo "stop_bed.sh: pid $pid already gone" >&2
  rm -f "$pidfile"
  exit 0
}

# Poll for up to 5 seconds for graceful shutdown.
for _ in 1 2 3 4 5; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    exit 0
  fi
  sleep 1
done

# Still alive. Try SIGTERM to the entire process group.
echo "stop_bed.sh: pid $pid did not exit on SIGTERM, escalating to process group" >&2
kill -TERM -- "-$pid" 2>/dev/null || true

for _ in 1 2 3 4 5; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    exit 0
  fi
  sleep 1
done

# Last resort: SIGKILL on the process group.
echo "stop_bed.sh: pid $pid did not exit on SIGTERM, sending SIGKILL" >&2
kill -KILL -- "-$pid" 2>/dev/null || true

# Give it one more second, then give up.
sleep 1
if kill -0 "$pid" 2>/dev/null; then
  echo "stop_bed.sh: pid $pid survived SIGKILL" >&2
  exit 1
fi

rm -f "$pidfile"
exit 0
