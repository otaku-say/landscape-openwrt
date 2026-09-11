#!/bin/bash
set -euo pipefail
image=${1:-landscape-openwrt:test}
name=landscape-openwrt-smoke
network=landscape-openwrt-smoke
volume=landscape-openwrt-smoke-config
socket_dir=$(mktemp -d)
server_pid=
mkdir -p build
cleanup() {
    docker logs "$name" > build/smoke-docker.log 2>&1 || true
    docker exec "$name" logread > build/smoke-openwrt.log 2>&1 || true
    docker exec "$name" ip -4 route > build/smoke-ipv4.log 2>&1 || true
    docker exec "$name" ip -6 route > build/smoke-ipv6.log 2>&1 || true
    docker exec "$name" nft list ruleset > build/smoke-nft.log 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi
    rm -rf "$socket_dir"
}
trap cleanup EXIT

docker network create --driver bridge --ipv6 \
    --subnet 172.30.80.0/24 --gateway 172.30.80.1 \
    --subnet fd70:6c61:6e64:80::/64 --gateway fd70:6c61:6e64:80::1 "$network"
python3 scripts/test-server.py "$socket_dir" > build/smoke-server.log 2>&1 &
server_pid=$!
for _ in {1..20}; do [[ ! -S "$socket_dir/register.sock" ]] || break; sleep 1; done
[[ -S "$socket_dir/register.sock" ]]

start_container() {
    docker run -d --name "$name" --privileged --network "$network" \
        --ip 172.30.80.2 --ip6 fd70:6c61:6e64:80::2 \
        --label ld_flow_edge=true --ulimit memlock=-1:-1 \
        --sysctl net.ipv4.conf.lo.accept_local=1 \
        --sysctl net.ipv6.conf.all.disable_ipv6=0 \
        --sysctl net.ipv6.conf.default.disable_ipv6=0 \
        -v "$socket_dir:/ld_unix_link:ro" -v "$volume:/etc/config" "$image"
}
wait_healthy() {
    for _ in {1..90}; do
        if docker exec "$name" /usr/libexec/landscape-healthcheck; then return; fi
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
docker exec "$name" sh -c '! pidof odhcpd; test "$(uci get dhcp.lan.ignore)" = 1; test "$(uci get firewall.@defaults[0].flow_offloading)" = 0'
docker exec "$name" nft list chain inet fw4 srcnat_lan | grep 'meta nfproto ipv4.*masquerade'
docker exec "$name" nft list chain inet fw4 srcnat_lan | grep 'meta nfproto ipv6.*masquerade'

# Off-subnet source addresses force return traffic through the candidate's NAT.
# This checks the OpenWrt forwarding path, not Landscape's tagged-flow classifier.
docker run --rm --privileged --no-healthcheck --network "$network" \
    --ip 172.30.80.3 --ip6 fd70:6c61:6e64:80::3 \
    --entrypoint /bin/sh "$image" -ec '
      ip addr add 198.18.0.2/32 dev lo
      ip -6 addr add fd00:dead:beef::2/128 dev lo
      ip route replace 172.30.80.1/32 via 172.30.80.2 dev eth0 src 198.18.0.2
      ip -6 route replace fd70:6c61:6e64:80::1/128 via fd70:6c61:6e64:80::2 dev eth0 src fd00:dead:beef::2
      wget -T 10 -qO- http://172.30.80.1:18081/ | grep -Fx "::ffff:172.30.80.2"
      wget -T 10 -qO- "http://[fd70:6c61:6e64:80::1]:18081/" | grep -Fx "fd70:6c61:6e64:80::2"
    '

# A user setting must survive recreation without freezing the image's init scripts.
docker exec "$name" sh -ec 'uci set landscape.test=persistence; uci set landscape.test.value=retained; uci commit landscape'
docker rm -f "$name"
start_container
wait_healthy
[[ $(docker exec "$name" uci get landscape.test.value) == retained ]]
docker exec "$name" sh -ec 'uci delete landscape.test; uci commit landscape'
echo 'PASS: procd, LuCI IPv4/IPv6, handler enrollment, NAT44/NAT66, config recreation.'
