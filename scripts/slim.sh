#!/bin/sh
set -eu
mkdir -p /var/lock /var/run /var/state /tmp/.uci
# Leaf-first order is computed from this exact upstream's installed metadata.
# Never use --force-depends or global autoremove: PassWall shares optional cores.
while IFS= read -r package; do
    [ -n "$package" ] || continue
    opkg remove "$package"
    if opkg list-installed | cut -d ' ' -f 1 | grep -qx "$package"; then
        echo "Package removal failed: $package" >&2
        exit 1
    fi
done < /usr/share/landscape-openwrt/remove-packages.txt
# Also remove generated/unowned dashboards, backups and preserved conffiles.
rm -rf /etc/openclash /etc/homeproxy /etc/momo /usr/share/openclash \
    /www/luci-static/resources/openclash /www/luci-static/resources/view/homeproxy \
    /www/luci-static/resources/view/momo
rm -f /etc/config/openclash /etc/config/homeproxy /etc/config/momo
for app in openclash homeproxy momo; do
    uci -q delete "firewall.$app" || true
done
uci commit firewall
find /tmp -mindepth 1 -maxdepth 1 -exec rm -rf '{}' ';'
rm -f /usr/share/landscape-openwrt/remove-packages.txt
for binary in xray sing-box ttyd; do command -v "$binary"; done
test -f /usr/lib/opkg/info/luci-app-passwall.control
test ! -e /usr/libexec/mihomo
test ! -e /etc/init.d/openclash
test ! -e /etc/init.d/homeproxy
test ! -e /etc/init.d/momo
