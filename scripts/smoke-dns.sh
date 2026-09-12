#!/bin/sh
# Explicit DNS settings must survive subsequent initialization calls.
set -eu
cp -p /etc/config/dhcp /tmp/smoke-dhcp
restore() { cp -p /tmp/smoke-dhcp /etc/config/dhcp; }
trap restore EXIT
[ "$(uci get landscape.container.initialized)" = 1 ]
uci -q delete 'dhcp.@dnsmasq[0].server'
uci add_list 'dhcp.@dnsmasq[0].server=127.0.0.1#15353'
uci add_list 'dhcp.@dnsmasq[0].server=/internal.example.net/10.1.1.1'
uci commit dhcp
before=$(uci get 'dhcp.@dnsmasq[0].server')
/usr/libexec/landscape-dns
[ "$(uci get 'dhcp.@dnsmasq[0].server')" = "$before" ]
echo 'PASS: initialized DNS remains under LuCI/PassWall control'
