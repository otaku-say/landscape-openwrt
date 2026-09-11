#!/bin/sh
# Run inside the disposable smoke container.
set -eu
cp -p /etc/config/dhcp /tmp/smoke-dhcp
cp -p /etc/config/landscape /tmp/smoke-landscape
cp -p /etc/config/passwall /tmp/smoke-passwall
restore() {
    cp -p /tmp/smoke-dhcp /etc/config/dhcp
    cp -p /tmp/smoke-landscape /etc/config/landscape
    cp -p /tmp/smoke-passwall /etc/config/passwall
    /etc/init.d/dnsmasq restart
}
trap restore EXIT
helper=/usr/libexec/landscape-dns
server() {
    uci -q delete 'dhcp.@dnsmasq[0].server' || true
    uci add_list "dhcp.@dnsmasq[0].server=$1"
    uci set 'dhcp.@dnsmasq[0].noresolv=1'
    uci commit dhcp
}
legacy() {
    uci -q delete landscape.container.managed_dns || true
    uci -q delete landscape.container.dns_migration_v2 || true
    uci set landscape.container.dns=223.5.5.5
    uci set 'passwall.@global[0].enabled=0'
    uci commit
}
legacy
server 10.10.10.1
"$helper"
[ "$(uci get 'dhcp.@dnsmasq[0].server')" = 223.5.5.5 ]
[ -f /etc/config/.landscape-dhcp-before-dns-v2 ]
uci set landscape.container.dns=9.9.9.9
uci commit landscape
"$helper"
[ "$(uci get 'dhcp.@dnsmasq[0].server')" = 9.9.9.9 ]
legacy
server 8.8.8.8
"$helper"
[ "$(uci get 'dhcp.@dnsmasq[0].server')" = 8.8.8.8 ]
legacy
server '127.0.0.1#15353'
uci add_list 'dhcp.@dnsmasq[0].server=/internal.test/10.1.1.1'
uci commit dhcp
before=$(uci get 'dhcp.@dnsmasq[0].server')
"$helper"
[ "$(uci get 'dhcp.@dnsmasq[0].server')" = "$before" ]
legacy
server 10.10.10.1
uci set 'passwall.@global[0].enabled=1'
uci commit passwall
"$helper"
[ "$(uci get 'dhcp.@dnsmasq[0].server')" = 10.10.10.1 ]
echo 'PASS: legacy DNS migration, managed updates, custom DNS and PassWall preservation'
