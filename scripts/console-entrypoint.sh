#!/bin/sh
# Test-only: runc rejects CLI mounts under /proc; mount in this private namespace.
set -eu
mount -o bind /ld_unix_link/console-cmdline /proc/cmdline
[ "$(cat /proc/cmdline)" = 'console=ttyS0,115200n8' ]
exec /usr/bin/landscape-start
