#!/bin/sh
set -eu
mkdir -p /var/lock /var/run /var/state /tmp/.uci
metadata=/usr/share/landscape-openwrt/upstream.json
# System APK feeds use USTC; retain official package signature verification.
sed -i 's|https://downloads.immortalwrt.org|https://mirrors.ustc.edu.cn/immortalwrt|g' \
    /etc/apk/repositories.d/distfeeds.list
for item in passwall passwall_i18n; do
    file=/tmp/packages/passwall.apk
    [ "$item" != passwall_i18n ] || file=/tmp/packages/passwall-zh.apk
    checksum=$(jsonfilter -i "$metadata" -e "@.${item}_sha256")
    printf '%s  %s\n' "$checksum" "$file" | sha256sum -c -
done
# Query the downloaded APK itself, not a stale hardcoded dependency list.
apk adbdump --format json /tmp/packages/passwall.apk > /tmp/passwall-metadata.json
name=$(jsonfilter -i /tmp/passwall-metadata.json -e '@.info.name')
version=$(jsonfilter -i /tmp/passwall-metadata.json -e '@.info.version')
[ "$name" = luci-app-passwall ]
[ "$version" = "$(jsonfilter -i "$metadata" -e '@.passwall_version')" ]
deps=$(jsonfilter -i /tmp/passwall-metadata.json -e '@.info.depends[*]')
[ -n "$deps" ]
# Use the signed dependency feed recommended by the official PassWall release.
# The key fingerprint and feed bytes were resolved before the build.
for item in dependency_feed dependency_key; do
    file=/tmp/passwall-feed.adb
    [ "$item" != dependency_key ] || file=/etc/apk/keys/openwrt-passwall-build.pem
    checksum=$(jsonfilter -i "$metadata" -e "@.${item}_sha256")
    printf '%s  %s\n' "$checksum" "$file" | sha256sum -c -
done
apk verify /tmp/passwall-feed.adb
feed=$(jsonfilter -i "$metadata" -e '@.dependency_feed_url')
printf '@passwall %s\n' "$feed" > /etc/apk/repositories.d/passwall.list
apk adbdump --format json /tmp/passwall-feed.adb > /tmp/passwall-feed.json
set --
while IFS= read -r package; do
    version=$(jsonfilter -i /tmp/passwall-feed.json -e "@.packages[@.name=\"$package\"].version")
    [ -n "$version" ] || { echo "Missing dependency: $package" >&2; exit 1; }
    set -- "$@" "$package@passwall=$version"
done < /tmp/passwall-packages.txt
# Signature verification stays enabled for every downloaded dependency.
# shellcheck disable=SC2086
apk --no-cache add $deps "$@" haproxy kmod-nft-tproxy kmod-nft-socket \
    bash unzip openssl-util shadow-chpasswd zoneinfo-all
# These two upstream release APKs are authenticated by their GitHub SHA256 above.
# Limit the trust exception to an offline transaction: no unsigned remote feeds.
apk --no-network --allow-untrusted add /tmp/packages/passwall.apk /tmp/packages/passwall-zh.apk
# Firmware upgrades and physical-disk helpers do not belong in this container.
for package in luci-i18n-attendedsysupgrade-zh-cn luci-app-attendedsysupgrade \
    owut attendedsysupgrade-common autocore automount; do
    if apk info -e "$package" >/dev/null 2>&1; then apk del "$package"; fi
done
apk query --installed --format json --fields name,version '*' \
    > /usr/share/landscape-openwrt/packages.json
# Enforce the requested proxy surface even if a future upstream rootfs changes.
for package in luci-app-homeproxy luci-app-openclash luci-app-momo luci-app-passwall2 ttyd; do
    if apk info -e "$package" >/dev/null 2>&1; then
        echo "Unexpected application in base rootfs: $package" >&2
        exit 1
    fi
done
apk info -e luci-app-passwall luci-i18n-passwall-zh-cn xray-core >/dev/null
# Do not let first-boot board defaults override the runtime password or feed URLs.
rm -f /etc/uci-defaults/50-root-passwd
# Kernel clock/timezone is shared with the host; only set userspace localtime.
sed -i '/hwclock -u --systz/d' /etc/init.d/system
sed -i 's|https://mirrors.vsean.net/openwrt|https://mirrors.ustc.edu.cn/immortalwrt|g' \
    /etc/uci-defaults/99-default-settings-chinese
cp /tmp/passwall-packages.txt /usr/share/landscape-openwrt/passwall-packages.txt
rm -rf /tmp/packages /tmp/install-packages.sh /tmp/passwall-metadata.json \
    /tmp/passwall-packages.txt /tmp/passwall-feed.adb /tmp/passwall-feed.json /var/cache/apk
