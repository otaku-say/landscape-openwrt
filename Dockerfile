# syntax=docker/dockerfile:1
ARG BASE_IMAGE=piaoyizy/openwrt-x86:latest
FROM ${BASE_IMAGE} AS upstream
FROM python:3.13-alpine AS planner
COPY --from=upstream /usr/lib/opkg/info/ /metadata/
COPY scripts/plan-prune.py /plan-prune.py
RUN python /plan-prune.py --opkg-info /metadata --output /plan

FROM upstream AS stripped
COPY --from=planner /plan/ /usr/share/landscape-openwrt/
COPY scripts/slim.sh /tmp/landscape-slim.sh
RUN /bin/sh /tmp/landscape-slim.sh

# Flatten the stripped rootfs: deleted cores must not remain in inherited layers.
FROM scratch
COPY --from=stripped / /
ARG BASE_DIGEST
ARG HANDLER_VERSION
ARG HANDLER_SHA256
ARG SOURCE_REVISION
ARG BUILD_DATE
LABEL org.opencontainers.image.title="Landscape OpenWrt" \
      org.opencontainers.image.description="PassWall OpenWrt with dynamic IPv6 and the official Landscape route-mode handler" \
      org.opencontainers.image.source="https://github.com/otaku-say/landscape-openwrt" \
      org.opencontainers.image.base.name="docker.io/piaoyizy/openwrt-x86:latest" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      dev.landscape.handler.version="${HANDLER_VERSION}" \
      dev.landscape.handler.sha256="${HANDLER_SHA256}"
COPY build/redirect_pkg_handler /usr/bin/redirect_pkg_handler
COPY build/upstream.json /usr/share/landscape-openwrt/upstream.json
COPY start.sh /usr/bin/landscape-start
COPY rootfs/ /
RUN set -eu; \
    test -x /sbin/init; test -x /sbin/procd; \
    command -v fw4; command -v nft; command -v ip; command -v jsonfilter; \
    test -f /etc/config/dhcp; \
    mkdir -p /var/lock; \
    rm -f /etc/uci-defaults/xxx-coremark; \
    sed -i '\|/etc/coremark.sh|d' /etc/crontabs/root; \
    chmod 0755 /etc/preinit /usr/bin/redirect_pkg_handler /usr/bin/landscape-start \
      /usr/libexec/landscape-* /etc/init.d/landscape-* /etc/hotplug.d/iface/99-landscape-ipv6; \
    printf '%s  %s\n' "$HANDLER_SHA256" /usr/bin/redirect_pkg_handler | sha256sum -c -; \
    /usr/bin/redirect_pkg_handler --help >/dev/null; \
    /etc/init.d/landscape-prepare enable; \
    /etc/init.d/landscape-redirect enable; \
    for service in sysctl sysntpd sysfixtime fstab gpio_switch led packet_steering sysfsutils turboacc autocore autoreboot; do \
      if [ -x "/etc/init.d/$service" ]; then "/etc/init.d/$service" disable; fi; \
    done
ENV PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    LAND_DNS_ADDR=223.5.5.5 \
    LAND_REDIRECT_LOG_LEVEL=INFO
EXPOSE 22 80 443 7681
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD /usr/libexec/landscape-healthcheck
ENTRYPOINT ["/usr/bin/landscape-start"]
CMD []
