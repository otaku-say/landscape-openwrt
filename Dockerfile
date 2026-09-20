# syntax=docker/dockerfile:1
FROM scratch AS prepared
ARG TARGETARCH
ADD build/${TARGETARCH}/rootfs.tar.gz /
ENV PATH=/usr/sbin:/usr/bin:/sbin:/bin
COPY build/shared/passwall.apk build/shared/passwall-zh.apk /tmp/packages/
COPY build/${TARGETARCH}/install-inputs.json /usr/share/landscape-openwrt/install-inputs.json
COPY scripts/install-packages.sh scripts/passwall-packages.txt /tmp/
COPY build/${TARGETARCH}/packages.adb /tmp/passwall-feed.adb
COPY build/shared/passwall-build.pem /etc/apk/keys/openwrt-passwall-build.pem
RUN /bin/sh /tmp/install-packages.sh

# Keep package downloads and removed hardware helpers out of the final layers.
FROM scratch
COPY --from=prepared / /
ARG TARGETARCH
ARG IMMORTALWRT_VERSION
ARG ROOTFS_SHA256_AMD64
ARG ROOTFS_SHA256_ARM64
ARG PASSWALL_VERSION
ARG INPUTS_DIGEST
ARG HANDLER_VERSION
ARG HANDLER_SHA256_AMD64
ARG HANDLER_SHA256_ARM64
ARG SOURCE_REVISION
ARG BUILD_DATE
ARG DEPENDENCY_FEED_SHA256_AMD64
ARG DEPENDENCY_FEED_SHA256_ARM64
LABEL org.opencontainers.image.title="Landscape ImmortalWrt PassWall" \
      org.opencontainers.image.description="Official ImmortalWrt rootfs, official PassWall APK, full proxy dependencies and Landscape route-mode handler" \
      org.opencontainers.image.source="https://github.com/otaku-say/landscape-openwrt" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      dev.landscape.immortalwrt.version="${IMMORTALWRT_VERSION}" \
      dev.landscape.rootfs.sha256.linux-amd64="${ROOTFS_SHA256_AMD64}" \
      dev.landscape.rootfs.sha256.linux-arm64="${ROOTFS_SHA256_ARM64}" \
      dev.landscape.passwall.version="${PASSWALL_VERSION}" \
      dev.landscape.inputs.digest="${INPUTS_DIGEST}" \
      dev.landscape.handler.version="${HANDLER_VERSION}" \
      dev.landscape.handler.sha256.linux-amd64="${HANDLER_SHA256_AMD64}" \
      dev.landscape.handler.sha256.linux-arm64="${HANDLER_SHA256_ARM64}" \
      dev.landscape.dependency_feed.sha256.linux-amd64="${DEPENDENCY_FEED_SHA256_AMD64}" \
      dev.landscape.dependency_feed.sha256.linux-arm64="${DEPENDENCY_FEED_SHA256_ARM64}"
COPY build/${TARGETARCH}/redirect_pkg_handler /usr/bin/redirect_pkg_handler
COPY build/upstream.json /usr/share/landscape-openwrt/upstream.json
COPY start.sh /usr/bin/landscape-start
COPY rootfs/ /
RUN set -eu; \
    test -x /sbin/init; test -x /sbin/procd; \
    for binary in fw4 nft ip uci jsonfilter chpasswd validate_data xray sing-box hysteria geoview chinadns-ng; do command -v "$binary"; done; \
    uci set 'firewall.@defaults[0].syn_flood=0'; \
    uci set 'firewall.@defaults[0].input=ACCEPT'; \
    uci set 'firewall.@defaults[0].output=ACCEPT'; \
    uci set 'firewall.@defaults[0].forward=ACCEPT'; \
    uci commit firewall; \
    uci set 'dhcp.@dnsmasq[0].rebind_protection=0'; \
    uci set 'dhcp.@dnsmasq[0].localise_queries=0'; \
    uci -q delete 'dhcp.@dnsmasq[0].cachesize' || true; \
    uci commit dhcp; \
    mkdir -p /var/lock; \
    chmod 0755 /etc/preinit /usr/bin/redirect_pkg_handler /usr/bin/landscape-start \
      /usr/libexec/landscape-* /etc/init.d/landscape-* /etc/hotplug.d/iface/99-landscape-ipv6; \
    case "$TARGETARCH" in \
      amd64) expected="$HANDLER_SHA256_AMD64" ;; \
      arm64) expected="$HANDLER_SHA256_ARM64" ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    printf '%s  %s\n' "$expected" /usr/bin/redirect_pkg_handler | sha256sum -c -; \
    /usr/bin/redirect_pkg_handler --help >/dev/null; \
    /etc/init.d/landscape-prepare enable; \
    /etc/init.d/landscape-redirect enable; \
    for service in sysctl sysntpd sysfixtime fstab gpio_switch led packet_steering sysfsutils autocore automount haproxy; do \
      if [ -x "/etc/init.d/$service" ]; then "/etc/init.d/$service" disable; fi; \
    done
ENV PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    TZ=Asia/Shanghai \
    LAND_DNS_ADDR=8.8.8.8 \
    LAND_REDIRECT_LOG_LEVEL=ERROR \
    LUCI_HTTP_PORT=80 \
    LUCI_HTTPS_PORT=443 \
    SSH_PORT=22
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD /usr/libexec/landscape-healthcheck
ENTRYPOINT ["/usr/bin/landscape-start"]
CMD []
