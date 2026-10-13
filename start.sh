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
# redirect_pkg_handler 8项参数校验、归一化（严格对齐文档口径）
proxy_saddr=${LAND_PROXY_SERVER_ADDR:-0.0.0.0}
case "$proxy_saddr" in ''|*[!0-9.]*) fail 'LAND_PROXY_SERVER_ADDR must be a valid IPv4 address.' ;; esac
proxy_saddr6=${LAND_PROXY_SERVER_ADDR_V6:-::}
case "$proxy_saddr6" in ''|*[!0-9a-fA-F:]*) fail 'LAND_PROXY_SERVER_ADDR_V6 must be a valid IPv6 address.' ;; esac
proxy_sport=${LAND_PROXY_SERVER_PORT:-12345}
case "$proxy_sport" in ''|*[!0-9]*) fail 'LAND_PROXY_SERVER_PORT must be an integer (1-65535).' ;; esac
if [ "$proxy_sport" -lt 1 ] || [ "$proxy_sport" -gt 65535 ]; then fail 'LAND_PROXY_SERVER_PORT must be in range 1-65535.'; fi
proxy_mode_raw=${LAND_PROXY_HANDLE_MODE:-route}
proxy_mode=$(printf '%s' "$proxy_mode_raw" | tr '_' '-')
case "$proxy_mode" in tproxy|route|multiple-tproxy) ;; *) fail 'LAND_PROXY_HANDLE_MODE must be tproxy, route, or multiple-tproxy.' ;; esac
icmp_on_raw=${LAND_PROXY_ENABLE_ICMP_PASSTHROUGH:-true}
icmp_on=$(printf '%s' "$icmp_on_raw" | tr '[:upper:]' '[:lower:]')
case "$icmp_on" in true|false) ;; *) fail 'LAND_PROXY_ENABLE_ICMP_PASSTHROUGH must be true or false (document format).' ;; esac
icmp_mark=${LAND_PROXY_ICMP_MARK_VALUE:-2}
case "$icmp_mark" in '' | *[!0-9]* | 0) fail 'LAND_PROXY_ICMP_MARK_VALUE must be a positive decimal integer.' ;; esac
proxy_sock_path=${LAND_SOCK_PATH:-/ld_unix_link}
[ "$proxy_sock_path" = '/ld_unix_link' ] || fail 'LAND_SOCK_PATH must be /ld_unix_link (fixed mount in container).'
log_level=${LAND_REDIRECT_LOG_LEVEL:-ERROR}
case "$log_level" in OFF|ERROR|WARN|INFO|DEBUG|TRACE) ;; *) fail 'Invalid LAND_REDIRECT_LOG_LEVEL.' ;; esac
tz=${TZ:-Asia/Shanghai}
case "$tz" in ''|/*|*..*|*[!A-Za-z0-9_+/-]*) fail 'TZ must be a valid IANA timezone.' ;; esac
[ -f "/usr/share/zoneinfo/$tz" ] || fail 'Unknown TZ; use an installed IANA timezone such as Asia/Shanghai.'
export TZ="$tz"
dns_servers=$(/usr/libexec/landscape-network-dns)

/usr/libexec/landscape-management --check
/usr/libexec/landscape-console
/usr/libexec/landscape-password
unset LAND_ROOT_PASSWORD

# Network is Docker-owned. Recreate only this UCI file, never subscription/plugin settings.
# It is prepared before /sbin/init so OpenWrt cannot generate a conflicting br-lan.
mkdir -p /etc/config
if [ ! -s /etc/config/system ]; then
    touch /etc/config/system
    uci set system.system=system
    uci set system.system.hostname=ImmortalWrt
    uci set system.system.timezone=CST-8
    uci set system.system.zonename=Asia/Shanghai
    uci set system.ntp=timeserver
    uci set system.ntp.enabled=0
    uci commit system
fi
# Docker's resolver file is a bind mount, so update its contents, not its inode.
printf 'nameserver 127.0.0.1\n' > /etc/resolv.conf
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
for dns in $dns_servers; do uci add_list network.lan.dns="$dns"; done
for address in $ip6; do uci add_list network.lan.ip6addr="$address"; done
uci commit network
[ -f /etc/config/landscape ] || touch /etc/config/landscape
uci set landscape.container=container
uci set "landscape.container.timezone=$tz"
uci set landscape.container.proxy_server_addr="$proxy_saddr"
uci set landscape.container.proxy_server_addr_v6="$proxy_saddr6"
uci set landscape.container.proxy_server_port="$proxy_sport"
uci set landscape.container.proxy_handle_mode="$proxy_mode"
uci set landscape.container.icmp_passthrough="$icmp_on"
uci set landscape.container.icmp_mark="$icmp_mark"
uci set landscape.container.sock_path="$proxy_sock_path"
uci set landscape.container.log_level="$log_level"
uci commit landscape
/usr/libexec/landscape-management

exec /sbin/init
