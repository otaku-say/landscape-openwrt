#!/bin/sh
set -eu

fail() { printf 'landscape-start: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'This container must run as root.'
[ -f /sys/fs/cgroup/cgroup.controllers ] || fail 'The official handler requires cgroup v2.'
[ -d /ld_unix_link ] || fail 'Mount the Landscape unix_link directory at /ld_unix_link:ro.'
[ -d /sys/class/net/eth0 ] || fail 'Attach exactly one Docker bridge network (eth0).'
# Reject host networking and extra interfaces: the upstream handler selects its first peer.
peers=0
for iface in /sys/class/net/*; do
    [ -f "$iface/ifindex" ] && [ -f "$iface/iflink" ] || continue
    read -r index < "$iface/ifindex"
    read -r link < "$iface/iflink"
    [ "$index" = "$link" ] || peers=$((peers + 1))
done
[ "$peers" = 1 ] || fail 'Exactly one veth peer is required; do not use host/macvlan networking.'
[ -d /sys/devices/virtual/net/eth0 ] || fail 'eth0 must be a Docker veth interface.'

ip4=$(ip -o -4 address show dev eth0 scope global | awk 'NR == 1 {print $4}')
ip6=$(ip -o -6 address show dev eth0 scope global | awk '!/ dadfailed | dynamic / {print $4}')
gw4=$(ip -4 route show default dev eth0 | awk '$1 == "default" && $2 == "via" {print $3; exit}')
gw6=$(ip -6 route show default dev eth0 | awk '$1 == "default" && $2 == "via" {print $3; exit}')
[ -n "$ip4" ] && [ -n "$gw4" ] || fail 'Docker must supply an IPv4 address and gateway.'
[ -n "$ip6" ] && [ -n "$gw6" ] || fail 'Docker must supply an IPv6 address and gateway; enable IPv6 on its bridge.'
log_level=${LAND_REDIRECT_LOG_LEVEL:-INFO}
case "$log_level" in OFF|ERROR|WARN|INFO|DEBUG|TRACE) ;; *) fail 'Invalid LAND_REDIRECT_LOG_LEVEL.' ;; esac
dns=${LAND_DNS_ADDR:-223.5.5.5}
case "$dns" in ''|*[!0-9a-fA-F:.]*) fail 'LAND_DNS_ADDR must be an IP address.' ;; esac

# Network is Docker-owned. Recreate only this UCI file, never subscription/plugin settings.
# It is prepared before /sbin/init so OpenWrt cannot generate a conflicting br-lan.
mkdir -p /etc/config /etc/landscape-original
if [ -f /etc/config/network ] && [ ! -f /etc/landscape-original/network ]; then
    cp /etc/config/network /etc/landscape-original/network
fi
: > /etc/config/network
uci set network.loopback=interface
uci set network.loopback.device=lo
uci set network.loopback.proto=static
uci set network.loopback.ipaddr=127.0.0.1
uci set network.loopback.netmask=255.0.0.0
uci set network.lan=interface
uci set network.lan.device=eth0
uci set network.lan.proto=static
uci set network.lan.ipaddr="$ip4"
uci set network.lan.gateway="$gw4"
uci set network.lan.ip6gw="$gw6"
uci set network.lan.delegate=0
uci set network.lan.force_link=1
uci add_list network.lan.dns="$dns"
for address in $ip6; do uci add_list network.lan.ip6addr="$address"; done
uci commit network
[ -f /etc/config/landscape ] || touch /etc/config/landscape
uci set landscape.container=container
uci set landscape.container.log_level="$log_level"
uci set landscape.container.dns="$dns"
uci commit landscape

exec /sbin/init
