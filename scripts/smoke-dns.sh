#!/bin/sh
# Exercise actual OpenWrt validation, netifd DNS and user-owned forwarding rules.
set -eu
[ "$(uci get landscape.container.initialized)" = 1 ]
case "${1:-}" in
    --fresh|--interface)
        expected=$(/usr/libexec/landscape-network-dns)
        [ "$(uci get network.lan.dns)" = "$expected" ]
        if [ "$1" = --fresh ]; then
            [ -z "$(uci -q get 'dhcp.@dnsmasq[0].server' || true)" ]
            echo 'PASS: fresh DNS forwarding list has no injected default server'
        fi
        [ "$(uci get 'dhcp.@dnsmasq[0].noresolv')" = 0 ]
        [ "$(uci get 'dhcp.@dnsmasq[0].resolvfile')" = /tmp/resolv.conf.d/resolv.conf.auto ]
        for address in $expected; do
            grep -Fx "nameserver $address" /tmp/resolv.conf.d/resolv.conf.auto
            nslookup smoke.example.net "$address" | grep -F '2606:4700:4700::1111'
        done
        count=$(printf '%s\n' "$expected" | wc -w)
        [ "$(grep -c '^nameserver ' /tmp/resolv.conf.d/resolv.conf.auto)" -eq "$count" ]
        lookup=$(nslookup smoke.example.net 127.0.0.1)
        printf '%s\n' "$lookup" | grep -F '1.1.1.1'
        printf '%s\n' "$lookup" | grep -F '2606:4700:4700::1111'
        for value in '' ',8.8.8.8' '8.8.8.8,' '8.8.8.8,,1.1.1.1' '8.8.8.8 1.1.1.1' \
            '999.1.1.1' '8.8.8.8,1.1.1.999' '2001:::1' '8.8.8.8#53' '2001:db8::/64'; do
            if LAND_DNS_ADDR="$value" /usr/libexec/landscape-network-dns >/tmp/smoke-dns-output 2>/dev/null; then
                echo 'Invalid DNS setting was accepted' >&2
                exit 1
            fi
            [ ! -s /tmp/smoke-dns-output ]
        done
        echo 'PASS: interface DNS entries, native IP validation and real resolver-file A/AAAA DNS'
        ;;
    --preserve)
        cp -p /etc/config/dhcp /tmp/smoke-dhcp
        restore() { cp -p /tmp/smoke-dhcp /etc/config/dhcp; }
        trap restore EXIT
        uci -q delete 'dhcp.@dnsmasq[0].server' || true
        uci add_list 'dhcp.@dnsmasq[0].server=127.0.0.1#15353'
        uci add_list 'dhcp.@dnsmasq[0].server=/internal.example.net/10.1.1.1'
        uci set 'dhcp.@dnsmasq[0].noresolv=1'
        uci set 'dhcp.@dnsmasq[0].resolvfile=/tmp/custom-resolv.conf'
        uci commit dhcp
        before=$(uci export dhcp)
        /usr/libexec/landscape-dns
        [ "$(uci export dhcp)" = "$before" ]
        echo 'PASS: initialized DNS forwarding, noresolv and custom resolver file remain under LuCI/PassWall control'
        ;;
    *) echo 'Usage: smoke-dns.sh --fresh|--interface|--preserve' >&2; exit 2 ;;
esac
