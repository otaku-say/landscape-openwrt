#!/bin/sh
# Test-only: runc rejects CLI mounts under /proc; mount in this private namespace.
set -eu
mount --bind /ld_unix_link/console-cmdline /proc/cmdline
mount -o remount,bind,ro /proc/cmdline
exec /usr/bin/landscape-start
