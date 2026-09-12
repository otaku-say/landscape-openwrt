# syntax=docker/dockerfile:1
FROM scratch AS prepared
ADD build/rootfs.tar.gz /
ENV PATH=/usr/sbin:/usr/bin:/sbin:/bin
COPY build/passwall.apk build/passwall-zh.apk /tmp/packages/
COPY build/upstream.json /usr/share/landscape-openwrt/upstream.json
COPY scripts/install-packages.sh scripts/passwall-packages.txt /tmp/
COPY build/packages.adb /tmp/passwall-feed.adb
COPY build/passwall-build.pem /etc/apk/keys/openwrt-passwall-build.pem
RUN /bin/sh /tmp/install-packages.sh

# Keep package downloads and removed hardware helpers out of the final layers.
FROM scratch
COPY --from=prepared / /
ARG IMMORTALWRT_VERSION
ARG ROOTFS_SHA256
ARG PASSWALL_VERSION
ARG INPUTS_DIGEST
ARG HANDLER_VERSION
ARG HANDLER_SHA256
ARG SOURCE_REVISION
ARG BUILD_DATE
LABEL org.opencontainers.image.title="Landscape ImmortalWrt PassWall" \
      org.opencontainers.image.description="Official ImmortalWrt rootfs, official PassWall APK, full proxy dependencies and Landscape route-mode handler" \
      org.opencontainers.image.source="https://github.com/otaku-say/landscape-openwrt" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      dev.landscape.immortalwrt.version="${IMMORTALWRT_VERSION}" \
      dev.landscape.rootfs.sha256="${ROOTFS_SHA256}" \
      dev.landscape.passwall.version="${PASSWALL_VERSION}" \
      dev.landscape.inputs.digest="${INPUTS_DIGEST}" \
      dev.landscape.handler.version="${HANDLER_VERSION}" \
      dev.landscape.handler.sha256="${HANDLER_SHA256}"
COPY build/redirect_pkg_handler /usr/bin/redirect_pkg_handler
COPY start.sh /usr/bin/landscape-start
COPY rootfs/ /
RUN set -eu; \
    test -x /sbin/init; test -x /sbin/procd; \
    for binary in fw4 nft ip jsonfilter chpasswd xray sing-box hysteria geoview chinadns-ng; do command -v "$binary"; done; \
    mkdir -p /var/lock; \
    chmod 0755 /etc/preinit /usr/bin/redirect_pkg_handler /usr/bin/landscape-start \
      /usr/libexec/landscape-* /etc/init.d/landscape-* /etc/hotplug.d/iface/99-landscape-ipv6; \
    printf '%s  %s\n' "$HANDLER_SHA256" /usr/bin/redirect_pkg_handler | sha256sum -c -; \
    /usr/bin/redirect_pkg_handler --help >/dev/null; \
    /etc/init.d/landscape-prepare enable; \
    /etc/init.d/landscape-redirect enable; \
    for service in sysctl sysntpd sysfixtime fstab gpio_switch led packet_steering sysfsutils autocore automount; do \
      if [ -x "/etc/init.d/$service" ]; then "/etc/init.d/$service" disable; fi; \
    done
ENV PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    LAND_DNS_ADDR=223.5.5.5 \
    LAND_REDIRECT_LOG_LEVEL=INFO
EXPOSE 22 80 443
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD /usr/libexec/landscape-healthcheck
ENTRYPOINT ["/usr/bin/landscape-start"]
CMD []
