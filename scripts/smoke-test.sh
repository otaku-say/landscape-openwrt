#!/bin/bash
set -euo pipefail
image=${1:-landscape-openwrt:test}
name=landscape-openwrt-smoke
network=landscape-openwrt-smoke
volume=landscape-openwrt-smoke-config
keys=landscape-openwrt-smoke-keys
socket_dir=$(mktemp -d)
server_pid=
fixture_pid=
dns_capture_pid=
mkdir -p build
python=build/test-venv/bin/python
# Generated per CI run; never trace or print this environment.
export LAND_ROOT_PASSWORD TZ LUCI_HTTP_PORT LUCI_HTTPS_PORT SSH_PORT LAND_DNS_ADDR
LAND_DNS_ADDR=172.30.80.1,fd70:6c61:6e64:80::1
LUCI_HTTP_PORT=80
LUCI_HTTPS_PORT=443
SSH_PORT=22
TZ=Asia/Shanghai
LAND_ROOT_PASSWORD=$(openssl rand -hex 20)
cleanup() {
    timeout 15 docker logs "$name" > build/smoke-docker.log 2>&1 || true
    timeout 15 docker exec "$name" logread > build/smoke-openwrt.log 2>&1 || true
    timeout 15 docker exec "$name" uci export firewall > build/smoke-uci-firewall.log 2>&1 || true
    timeout 15 docker exec "$name" uci export dhcp > build/smoke-uci-dhcp.log 2>&1 || true
    timeout 15 docker exec "$name" uci export network > build/smoke-uci-network.log 2>&1 || true
    timeout 15 docker exec "$name" cat /tmp/resolv.conf.d/resolv.conf.auto > build/smoke-resolv.log 2>&1 || true
    ip -6 addr show dev ld-owrt-test > build/smoke-host-ipv6.log 2>&1 || true
    ip -6 neigh show dev ld-owrt-test > build/smoke-host-neigh.log 2>&1 || true
    ss -lnup 'sport = :53' > build/smoke-host-dns.log 2>&1 || true
    if [[ -n "$dns_capture_pid" ]]; then sudo kill "$dns_capture_pid" 2>/dev/null || true; wait "$dns_capture_pid" 2>/dev/null || true; fi
    timeout 15 docker exec "$name" uci export dropbear > build/smoke-uci-dropbear.log 2>&1 || true
    timeout 15 docker exec "$name" netstat -lntp > build/smoke-listeners.log 2>&1 || true
    timeout 15 docker exec "$name" cat /tmp/landscape-proxy-check.log > build/smoke-proxy-check.log 2>&1 || true
    docker exec "$name" ip -4 route > build/smoke-ipv4.log 2>&1 || true
    docker exec "$name" ip -6 route > build/smoke-ipv6.log 2>&1 || true
    docker exec "$name" nft list ruleset > build/smoke-nft.log 2>&1 || true
    if [[ -n "$fixture_pid" ]]; then
        sudo "$python" scripts/network-fixture.py stop "$socket_dir" || true
        wait "$fixture_pid" 2>/dev/null || true
    fi
    docker rm -f "${name}-client" "$name" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    docker volume rm "$volume" "$keys" >/dev/null 2>&1 || true
    if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi
    rm -rf "$socket_dir"
}
trap cleanup EXIT

docker network create --driver bridge --ipv6 \
    --opt com.docker.network.bridge.name=ld-owrt-test \
    --opt com.docker.network.bridge.gateway_mode_ipv4=nat-unprotected \
    --opt com.docker.network.bridge.gateway_mode_ipv6=nat-unprotected \
    --subnet 172.30.80.0/24 --gateway 172.30.80.1 \
    --subnet fd70:6c61:6e64:80::/64 --gateway fd70:6c61:6e64:80::1 "$network"
python3 scripts/test-server.py "$socket_dir" > build/smoke-server.log 2>&1 &
server_pid=$!
for _ in {1..20}; do [[ ! -S "$socket_dir/register.sock" ]] || break; sleep 1; done
[[ -S "$socket_dir/register.sock" ]]
# Keep diagnostics runner-owned; only the packet sockets need root.
# shellcheck disable=SC2024
sudo "$python" scripts/network-fixture.py serve "$socket_dir" > build/smoke-network.log 2>&1 &
fixture_pid=$!
for _ in {1..20}; do [[ ! -f "$socket_dir/network-ready" ]] || break; sleep 1; done
[[ -f "$socket_dir/network-ready" ]]
# Keep DNS and neighbor-discovery evidence when an actual lookup fails.
# shellcheck disable=SC2024
sudo tcpdump -p -l -nne -s 256 -i ld-owrt-test 'port 53 or icmp6' > build/smoke-dns-packets.log 2>&1 &
dns_capture_pid=$!

start_container() {
    docker run -d --name "$name" --privileged --network "$network" \
        --ip 172.30.80.2 --ip6 fd70:6c61:6e64:80::2 \
        --label ld_flow_edge=true --ulimit memlock=-1:-1 \
        -e LAND_DNS_ADDR -e LAND_ROOT_PASSWORD -e TZ \
        -e LUCI_HTTP_PORT -e LUCI_HTTPS_PORT -e SSH_PORT \
        --sysctl net.ipv4.conf.lo.accept_local=1 \
        --sysctl net.ipv6.conf.all.disable_ipv6=0 \
        --sysctl net.ipv6.conf.default.disable_ipv6=0 \
        -v "$socket_dir:/ld_unix_link:ro" -v "$volume:/etc/config" -v "$keys:/etc/dropbear" "$image"
}
wait_healthy() {
    local deadline=$((SECONDS + 180))
    while (( SECONDS < deadline )); do
        if timeout 8 docker exec "$name" /usr/libexec/landscape-healthcheck; then return; fi
        [[ $(docker inspect -f '{{.State.Running}}' "$name") == true ]]
        sleep 2
    done
    echo 'Container did not become healthy' >&2
    return 1
}
start_container
wait_healthy
docker exec -i "$name" sh -s -- --fresh < scripts/smoke-dns.sh
[[ -z $(docker port "$name") ]]
"$python" scripts/test-management.py '172.30.80.2,fd70:6c61:6e64:80::2' "$LUCI_HTTP_PORT" "$LUCI_HTTPS_PORT" "$SSH_PORT"
key_before=$(docker exec "$name" sha256sum /etc/dropbear/dropbear_ed25519_host_key)
for _ in {1..40}; do [[ ! -s "$socket_dir/enrollment.json" ]] || break; sleep 2; done
id=$(docker inspect -f '{{.Id}}' "$name")
python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert sys.argv[2].startswith(v["id"]); assert v["ifindex"]>0' "$socket_dir/enrollment.json" "$id"
docker exec "$name" sh -ec '! pidof odhcpd; test "$(uci get dhcp.lan.ignore)" = 1; test "$(uci get firewall.@defaults[0].flow_offloading)" = 0'
docker exec "$name" nft list chain inet fw4 srcnat_lan | grep 'meta nfproto ipv4.*masquerade'
docker exec "$name" nft list chain inet fw4 srcnat_lan | grep 'meta nfproto ipv6.*masquerade'

# Off-subnet source addresses force return traffic through the candidate's NAT.
# This checks the OpenWrt forwarding path, not Landscape's tagged-flow classifier.
# Provide the return routes normally supplied by the real router's LAN topology.
docker exec "$name" ip route add 198.18.0.2/32 via 172.30.80.3 dev eth0
docker exec "$name" ip -6 route add fd00:dead:beef::2/128 via fd70:6c61:6e64:80::3 dev eth0
timeout 45 docker run --rm --name "${name}-client" --privileged --no-healthcheck --network "$network" \
    --ip 172.30.80.3 --ip6 fd70:6c61:6e64:80::3 \
    --entrypoint /bin/sh "$image" -ec '
      ip addr add 198.18.0.2/32 dev lo
      ip -6 addr add fd00:dead:beef::2/128 dev lo
      ip route replace 172.30.80.1/32 via 172.30.80.2 dev eth0 src 198.18.0.2
      ip -6 route replace fd70:6c61:6e64:80::1/128 via fd70:6c61:6e64:80::2 dev eth0 src fd00:dead:beef::2
      wget -T 10 -qO- http://172.30.80.1:18081/ | grep -Fx "::ffff:172.30.80.2"
      wget -T 10 -qO- "http://[fd70:6c61:6e64:80::1]:18081/" | grep -Fx "fd70:6c61:6e64:80::2"
    '

"$python" scripts/smoke-login.py "$name"
sudo "$python" scripts/network-fixture.py check "$name" "$socket_dir"
sudo "$python" scripts/test-flow-exit.py "$name"
# OpenWrt mounts /tmp itself; docker cp may address the underlying mount instead.
docker exec -i "$name" sh -s -- --preserve < scripts/smoke-dns.sh
docker exec "$name" sh -ec '
  for binary in xray sing-box hysteria geoview chinadns-ng bash unzip fw4 nft; do command -v "$binary"; done
  apk info -e luci-app-passwall luci-i18n-passwall-zh-cn >/dev/null
  ! command -v ttyd
  xray version
  sing-box version
  hysteria version
  for app in openclash homeproxy momo; do
    test ! -e "/etc/init.d/$app"
    test ! -e "/usr/share/luci/menu.d/luci-app-$app.json"
  done
  test ! -e /usr/libexec/mihomo
'

# A user setting must survive recreation without freezing the image's init scripts.
docker exec "$name" sh -ec '
  uci set landscape.test=persistence
  uci set landscape.test.value=retained
  uci set passwall.landscape_smoke=nodes
  uci set passwall.landscape_smoke.remarks=retained
  uci add_list "dhcp.@dnsmasq[0].server=/retained.example.net/172.30.80.1"
  uci commit
'
docker rm -f "$name"
export PREVIOUS_ROOT_PASSWORD="$LAND_ROOT_PASSWORD"
# Deliberately exercise a short numeric password: no length/complexity policy.
LAND_ROOT_PASSWORD=123
TZ=Europe/Berlin
LUCI_HTTP_PORT=18000
LUCI_HTTPS_PORT=18443
SSH_PORT=12222
# Switching to IPv6-only proves recreation replaces, rather than appends, interface DNS.
LAND_DNS_ADDR=fd70:6c61:6e64:80::1
start_container
wait_healthy
[[ $(docker exec "$name" uci get 'dhcp.@dnsmasq[0].server') == /retained.example.net/172.30.80.1 ]]
docker exec -i "$name" sh -s -- --interface < scripts/smoke-dns.sh
echo 'PASS: IPv6-only interface DNS replaces the old list while custom forwarding survives recreation'
[[ $(docker exec "$name" uci get landscape.test.value) == retained ]]
[[ $(docker exec "$name" sha256sum /etc/dropbear/dropbear_ed25519_host_key) == "$key_before" ]]
"$python" scripts/test-management.py '172.30.80.2,fd70:6c61:6e64:80::2' "$LUCI_HTTP_PORT" "$LUCI_HTTPS_PORT" "$SSH_PORT"
"$python" scripts/smoke-login.py "$name"
sudo "$python" scripts/network-fixture.py management "$name"
sudo "$python" scripts/network-fixture.py verify "$name" "$socket_dir"
docker exec "$name" sh -ec '
  test "$(uci get passwall.landscape_smoke.remarks)" = retained
  uci delete landscape.test
  uci delete passwall.landscape_smoke
  uci commit
'
echo 'PASS: real tagged PassWall TCP/UDP exits, preserved outbound NAT, native management, password/port rotation and DNS/SLAAC.'
