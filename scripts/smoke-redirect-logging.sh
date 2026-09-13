#!/bin/sh
set -eu
level=${1:?Expected handler log level}
case "$level" in ERROR|OFF) ;; *) exit 2 ;; esac
[ "$(uci get landscape.container.log_level)" = "$level" ]
pid=$(pidof redirect_pkg_handler)
case "$pid" in ''|*[!0-9]*) echo 'Expected one running redirect handler' >&2; exit 1 ;; esac
tr '\000' '\n' < "/proc/$pid/cmdline" > /tmp/landscape-handler-cmdline
awk -v expected="$level" '
    previous == "--log-level" && $0 == expected { found = 1 }
    { previous = $0 }
    END { exit !found }
' /tmp/landscape-handler-cmdline
if [ "$level" = OFF ]; then
    [ "$(readlink "/proc/$pid/fd/1")" = /dev/null ]
    [ "$(readlink "/proc/$pid/fd/2")" = /dev/null ]
else
    [ "$(readlink "/proc/$pid/fd/1")" != /dev/null ]
    [ "$(readlink "/proc/$pid/fd/2")" != /dev/null ]
fi
printf 'PASS: running handler log level %s and output capture\n' "$level"
