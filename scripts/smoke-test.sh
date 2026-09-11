#!/bin/bash
set -euo pipefail
image=${1:-landscape-openwrt:test}
name=landscape-openwrt-smoke
network=landscape-openwrt-smoke
volume=landscape-openwrt-smoke-config
socket_dir=$(mktemp -d)
server_pid=
fixture_pid=
mkdir -p build
python=build/test-venv/bin/python
cleanup() {
    timeout 15 docker logs "$name" > build/smoke-docker.log 2>&1 || true
    timeout 15 docker exec "$name" logread > build/smoke-openwrt.log 2>&1 || true
    timeout 15 docker exec "$name" uci export firewall > build/smoke-uci-firewall.log 2>&1 || true
    timeout 15 docker exec "$name" uci export dhcp > build/smoke-uci-dhcp.log 2>&1 || true
    docker exec "$name" ip -4 route > build/smoke-ipv4.log 2>&1 || true
    docker exec "$name" ip -6 route > build/smoke-ipv6.log 2>&1 || true
    docker exec "$name" nft list ruleset > build/smoke-nft.log 2>&1 || true
    if [[ -n "$fixture_pid" ]]; then
        sudo "$python" scripts/network-fixture.py stop "$socket_dir" || true
        wait "$fixture_pid" 2>/dev/null || true
    fi
    docker rm -f "${name}-client" "$name" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi
    rm -rf "$socket_dir"
}
trap cleanup EXIT

docker network create --driver bridge --ipv6 \
    --opt com.docker.network.bridge.name=ld-owrt-test \
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

start_container() {
    docker run -d --name "$name" --privileged --network "$network" \
        --ip 172.30.80.2 --ip6 fd70:6c61:6e64:80::2 \
        --label ld_flow_edge=true --ulimit memlock=-1:-1 \
        -e LAND_DNS_ADDR=172.30.80.1 \
        --sysctl net.ipv4.conf.lo.accept_local=1 \
        --sysctl net.ipv6.conf.all.disable_ipv6=0 \
        --sysctl net.ipv6.conf.default.disable_ipv6=0 \
        -v "$socket_dir:/ld_unix_link:ro" -v "$volume:/etc/config" "$image"
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
curl -fsS --max-time 10 http://172.30.80.2/ >/dev/null
curl -gfsS --noproxy '*' --max-time 10 'http://[fd70:6c61:6e64:80::2]/' >/dev/null
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

node scripts/test-ttyd.mjs 172.30.80.2
sudo "$python" scripts/network-fixture.py check "$name" "$socket_dir"
# OpenWrt mounts /tmp itself; docker cp may address the underlying mount instead.
docker exec -i "$name" sh -s < scripts/smoke-dns.sh
docker exec "$name" sh -ec '
  for binary in xray sing-box ttyd bash unzip fw4 nft; do command -v "$binary"; done
  test -f /usr/lib/opkg/info/luci-app-passwall.control
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
  uci set firewall.openclash=include
  uci set firewall.openclash.type=script
  uci set firewall.openclash.path=/var/etc/openclash.include
  uci set firewall.openclash.enabled=1
  uci commit
'
docker rm -f "$name"
start_container
wait_healthy
[[ $(docker exec "$name" uci get landscape.test.value) == retained ]]
sudo "$python" scripts/network-fixture.py verify "$name" "$socket_dir"
docker exec "$name" sh -ec '
  test "$(uci get passwall.landscape_smoke.remarks)" = retained
  test "$(uci get firewall.openclash.enabled)" = 0
  uci delete landscape.test
  uci delete passwall.landscape_smoke
  uci delete firewall.openclash
  uci commit
'
echo 'PASS: procd, dual-stack LuCI, enrollment, NAT44/66, DNS migration, SLAAC renewal, ttyd and recreation.'
