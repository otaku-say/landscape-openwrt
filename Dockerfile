# syntax=docker/dockerfile:1
ARG BASE_IMAGE=piaoyizy/openwrt-x86:latest
FROM ${BASE_IMAGE}
ARG BASE_DIGEST
ARG HANDLER_VERSION
ARG HANDLER_SHA256
ARG SOURCE_REVISION
ARG BUILD_DATE
LABEL org.opencontainers.image.title="Landscape OpenWrt" \
      org.opencontainers.image.description="piaoyizy OpenWrt with the official Landscape route-mode handler" \
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
    chmod 0755 /etc/preinit /usr/bin/redirect_pkg_handler /usr/bin/landscape-start \
      /usr/libexec/landscape-* /etc/init.d/landscape-*; \
    printf '%s  %s\n' "$HANDLER_SHA256" /usr/bin/redirect_pkg_handler | sha256sum -c -; \
    /usr/bin/redirect_pkg_handler --help >/dev/null; \
    /etc/init.d/landscape-prepare enable; \
    /etc/init.d/landscape-redirect enable; \
    for service in sysctl sysntpd sysfixtime cron fstab gpio_switch led packet_steering sysfsutils turboacc; do \
      if [ -x "/etc/init.d/$service" ]; then "/etc/init.d/$service" disable; fi; \
    done
ENV LAND_DNS_ADDR=10.10.10.1 \
    LAND_REDIRECT_LOG_LEVEL=INFO
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD /usr/libexec/landscape-healthcheck
ENTRYPOINT ["/usr/bin/landscape-start"]
CMD []
