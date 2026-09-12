#!/usr/bin/env python3
"""Test actual PassWall and the official route handler using an isolated VLESS exit."""
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

WAN = 'ld-owrt-wan'
WAN_LINK = 'ld-owrt-wan0'
LAN_LINK = 'ld-owrt-lan0'
CLIENT = 'ld-owrt-lan'
BRIDGE = 'ld-owrt-test'
REMOTE4 = '93.184.216.34'
REMOTE6 = '2606:4700:ffff::34'


def run(*args, check=True, timeout=30, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{args}: {result.stderr}\n{result.stdout}')
    return result.stdout.strip() if check else result


def inside(name, *args, **kwargs):
    return run('docker', 'exec', '-i', name, *args, **kwargs)


def ip(value):
    address = ipaddress.ip_address(value.strip())
    return address.ipv4_mapped if address.version == 6 and address.ipv4_mapped else address


def uci(name, values):
    for key, value in values.items():
        inside(name, 'uci', 'set', f'passwall.{key}={value}')
    inside(name, 'uci', 'commit', 'passwall')


def topology(pid):
    run('ip', 'netns', 'attach', WAN, pid)
    run('ip', 'link', 'add', WAN_LINK, 'type', 'veth', 'peer', 'name', 'eth0', 'netns', WAN)
    run('ip', 'addr', 'add', '203.0.113.1/24', 'dev', WAN_LINK)
    run('ip', '-6', 'addr', 'add', 'fd70:6c61:6e64:90::1/64', 'dev', WAN_LINK, 'nodad')
    run('ip', 'link', 'set', WAN_LINK, 'up')
    for args in [('link', 'set', 'lo', 'up'), ('link', 'set', 'eth0', 'up'),
                 ('addr', 'add', '203.0.113.2/24', 'dev', 'eth0'),
                 ('-6', 'addr', 'add', 'fd70:6c61:6e64:90::2/64', 'dev', 'eth0', 'nodad'),
                 ('addr', 'add', REMOTE4 + '/32', 'dev', 'lo'),
                 ('-6', 'addr', 'add', REMOTE6 + '/128', 'dev', 'lo', 'nodad'),
                 ('route', 'add', 'default', 'via', '203.0.113.1'),
                 ('-6', 'route', 'add', 'default', 'via', 'fd70:6c61:6e64:90::1')]:
        run('ip', '-n', WAN, *args)
    run('ip', 'route', 'add', REMOTE4 + '/32', 'via', '203.0.113.2', 'dev', WAN_LINK)
    run('ip', '-6', 'route', 'add', REMOTE6 + '/128', 'via', 'fd70:6c61:6e64:90::2', 'dev', WAN_LINK)


def lan_return_path():
    links = json.loads(run('ip', '-n', CLIENT, '-j', 'link', 'show', 'eth0'))
    client_mac = links[0]['address']
    router_mac = Path(f'/sys/class/net/{LAN_LINK}/address').read_text().strip()
    run('tc', 'qdisc', 'replace', 'dev', LAN_LINK, 'clsact')
    run('tc', 'qdisc', 'replace', 'dev', BRIDGE, 'clsact')
    for priority, (protocol, address) in enumerate((('ip', '10.77.0.2'), ('ipv6', 'fd70:6c61:6e64:77::2')), 20):
        # One classifier priority cannot mix IPv4 and IPv6 protocols.
        run('tc', 'filter', 'replace', 'dev', BRIDGE, 'ingress', 'protocol', protocol, 'pref', str(priority),
            'flower', 'skip_hw', 'dst_ip', address, 'action', 'pedit', 'ex',
            'munge', 'eth', 'dst', 'set', client_mac, 'munge', 'eth', 'src', 'set', router_mac, 'pipe',
            'action', 'mirred', 'egress', 'redirect', 'dev', LAN_LINK)


def tagged_redirect(name):
    network = json.loads(run('docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', name))
    mac = next(iter(network.values()))['MacAddress']
    source_mac = Path(f'/sys/class/net/{BRIDGE}/address').read_text().strip()
    peer_index = int(inside(name, 'cat', '/sys/class/net/eth0/iflink'))
    peer = next(link['ifname'] for link in json.loads(run('ip', '-j', 'link', 'show')) if link['ifindex'] == peer_index)
    for priority, (protocol, address) in enumerate((('ip', REMOTE4), ('ipv6', REMOTE6)), 20):
        # VLAN 0xc07 is Landscape flow 7. The shipped route handler must pop it.
        run('tc', 'filter', 'replace', 'dev', LAN_LINK, 'ingress', 'protocol', protocol, 'pref', str(priority),
            'flower', 'skip_hw', 'dst_ip', address, 'action', 'pedit', 'ex',
            'munge', 'eth', 'dst', 'set', mac, 'munge', 'eth', 'src', 'set', source_mac, 'pipe',
            'action', 'vlan', 'push', 'protocol', '802.1Q', 'id', '3079',
            'action', 'mirred', 'egress', 'redirect', 'dev', peer)


def requests(prefix, label):
    for address in (REMOTE4, REMOTE6):
        authority = f'[{address}]' if ':' in address else address
        peer = run(*prefix, 'curl', '--noproxy', '*', '-gfsS', '--max-time', '10',
                   f'http://{authority}:18081/')
        assert ip(peer) == ip(address), ('TCP bypassed the test proxy', address, peer)
        peer = run(*prefix, sys.executable, 'scripts/exit-target.py', 'udp', address)
        assert ip(peer) == ip(address), ('UDP bypassed the test proxy', address, peer)
    print(f'PASS: {label} IPv4/IPv6 TCP and UDP traverse real PassWall and the isolated VLESS exit', flush=True)


def main():
    name = sys.argv[1]
    processes = []
    exit_name = name + '-exit'
    backup = inside(name, 'uci', 'export', 'passwall') + '\n'
    with tempfile.TemporaryDirectory(prefix='landscape-exit-') as directory:
        root = Path(directory)
        try:
            identity = str(uuid.uuid4())
            config = {'log': {'access': str(root / 'access.log'), 'error': str(root / 'error.log'), 'loglevel': 'warning'},
                      'inbounds': [{'listen': '::', 'port': 10443, 'protocol': 'vless',
                                    'settings': {'clients': [{'id': identity}], 'decryption': 'none'},
                                    'streamSettings': {'network': 'raw', 'security': 'none'}}],
                      'outbounds': [{'protocol': 'freedom'}]}
            (root / 'node.json').write_text(json.dumps(config))
            # Run the shipped musl-linked core inside its own image, not the host libc.
            image = run('docker', 'inspect', '--format', '{{.Image}}', name)
            run('docker', 'run', '--detach', '--name', exit_name, '--network', 'none', '--no-healthcheck',
                '--sysctl', 'net.ipv6.conf.all.disable_ipv6=0', '--sysctl', 'net.ipv6.conf.default.disable_ipv6=0',
                '--mount', f'type=bind,src={directory},dst={directory}', '--entrypoint', '/usr/bin/xray',
                image, 'run', '-c', str(root / 'node.json'))
            pid = run('docker', 'inspect', '--format', '{{.State.Pid}}', exit_name)
            assert pid != '0', run('docker', 'logs', exit_name)
            topology(pid)
            target_log = (root / 'target.log').open('w')
            processes.append(subprocess.Popen(['ip', 'netns', 'exec', WAN, sys.executable,
                                              'scripts/exit-target.py', 'serve'], stdout=target_log, stderr=subprocess.STDOUT))
            time.sleep(2)
            for process in processes:
                assert process.poll() is None, 'Isolated WAN process failed to start'
            # A separate WAN interface verifies Docker outbound NAT remains intact.
            peer4 = inside(name, 'curl', '--noproxy', '*', '-fsS', '--max-time', '5', 'http://203.0.113.2:18081/')
            peer6 = inside(name, 'curl', '--noproxy', '*', '--interface', 'fd70:6c61:6e64:80::2',
                           '-gfsS', '--max-time', '5', 'http://[fd70:6c61:6e64:90::2]:18081/')
            assert ip(peer4) == ip('203.0.113.1'), peer4
            assert ip(peer6) == ip('fd70:6c61:6e64:90::1'), peer6
            print('PASS: actual IPv4 and ULA outbound masquerading preserved outside the LAN interface', flush=True)
            lan_return_path()
            tagged_redirect(name)
            candidate_pid = run('docker', 'inspect', '--format', '{{.State.Pid}}', name)
            capture = (root / 'capture.log').open('w')
            processes.append(subprocess.Popen(['nsenter', '--target', candidate_pid, '--net', 'tcpdump',
                                              '-l', '-nne', '-i', 'any', '-s', '128'], stdout=capture, stderr=subprocess.STDOUT))
            uci(name, {'landscape_exit': 'nodes', 'landscape_exit.remarks': 'isolated-ci-exit',
                       'landscape_exit.type': 'Xray', 'landscape_exit.protocol': 'vless',
                       'landscape_exit.address': '203.0.113.2', 'landscape_exit.port': '10443',
                       'landscape_exit.uuid': identity, 'landscape_exit.encryption': 'none',
                       'landscape_exit.transport': 'raw', 'landscape_exit.tls': '0',
                       '@global[0].enabled': '1', '@global[0].node': 'landscape_exit',
                       '@global[0].client_proxy': '1', '@global[0].localhost_proxy': '1',
                       '@global[0].filter_proxy_ipv6': '0', '@global[0].use_direct_list': '0',
                       '@global[0].use_proxy_list': '0', '@global[0].use_block_list': '0',
                       '@global[0].use_gfw_list': '0', '@global[0].chn_list': '0',
                       '@global[0].dns_shunt': 'dnsmasq', '@global[0].remote_dns': '172.30.80.1',
                       '@global_delay[0].start_delay': '0', '@global_delay[0].start_daemon': '0',
                       '@global_forwarding[0].prefer_nft': '1', '@global_forwarding[0].tcp_proxy_way': 'tproxy',
                       '@global_forwarding[0].ipv6_tproxy': '1', '@global_forwarding[0].tcp_redir_ports': '1:65535',
                       '@global_forwarding[0].udp_redir_ports': '1:65535', '@global_forwarding[0].udp_proxy_drop_ports': 'disable'})
            for node_address in ('203.0.113.2', 'fd70:6c61:6e64:90::2'):
                uci(name, {'landscape_exit.address': node_address})
                inside(name, '/etc/init.d/passwall', 'restart', timeout=90)
                time.sleep(5)
                assert 'PSW' in inside(name, 'nft', 'list', 'ruleset'), 'PassWall did not create transparent proxy rules'
                requests(('nsenter', '--target', candidate_pid, '--net'), 'Container localhost')
                requests(('ip', 'netns', 'exec', CLIENT), 'Landscape-tagged client')
                print(f'PASS: proxy transport to {node_address} with LR-style forwarding and unchanged host NAT', flush=True)
            assert '203.0.113.1:' in (root / 'access.log').read_text(), 'VLESS node did not observe preserved IPv4 outbound NAT'
            # Management remains reachable while transparent proxying is enabled.
            ports = [inside(name, 'uci', 'get', f'landscape.container.{key}_port') for key in ('http', 'https', 'ssh')]
            print(run('ip', 'netns', 'exec', CLIENT, sys.executable, 'scripts/test-management.py',
                      '172.30.80.2,fd70:6c61:6e64:80::2', *ports, timeout=90), flush=True)
        finally:
            Path('build/smoke-passwall.log').write_text(inside(name, 'sh', '-c', 'cat /tmp/log/passwall.log 2>/dev/null || true', check=False).stdout)
            Path('build/smoke-passwall-nft.log').write_text(inside(name, 'nft', 'list', 'ruleset', check=False).stdout)
            node_output = run('docker', 'logs', exit_name, check=False)
            Path('build/smoke-exit-node.log').write_text(node_output.stdout + node_output.stderr)
            for log in ('target.log', 'access.log', 'error.log', 'capture.log'):
                if (root / log).exists():
                    Path('build/smoke-exit-' + log).write_bytes((root / log).read_bytes())
            for interface in (LAN_LINK, BRIDGE):
                result = run('tc', '-s', 'filter', 'show', 'dev', interface, 'ingress', check=False)
                Path('build/smoke-tc-' + interface + '.log').write_text(result.stdout + result.stderr)
            inside(name, '/etc/init.d/passwall', 'stop', check=False, timeout=90)
            inside(name, 'uci', 'import', 'passwall', data=backup)
            inside(name, 'uci', 'commit', 'passwall')
            for interface in (LAN_LINK, BRIDGE):
                for priority, protocol in enumerate(('ip', 'ipv6'), 20):
                    run('tc', 'filter', 'del', 'dev', interface, 'ingress', 'protocol', protocol, 'pref', str(priority), check=False)
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            run('docker', 'rm', '-f', exit_name, check=False)
            run('ip', 'netns', 'del', WAN, check=False)
            run('ip', 'link', 'del', WAN_LINK, check=False)


if __name__ == '__main__':
    main()
