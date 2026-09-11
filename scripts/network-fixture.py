#!/usr/bin/env python3
"""Isolated CI DNS and RA fixture; no real WAN, external DNS or ISP prefix."""
import ipaddress
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from dnslib import A, AAAA, QTYPE, RR
from dnslib.server import BaseResolver, DNSServer
from scapy.all import Ether, IPv6, ICMPv6ND_RA, ICMPv6NDOptPrefixInfo, ICMPv6NDOptSrcLLAddr, get_if_hwaddr, sendp

BRIDGE = 'ld-owrt-test'
TARGET = '2001:db8:ffff::1'
# Do not use .test (locally blocked by OpenWrt's RFC6761 config) or RFC1918
# answers (correctly rejected by DNS rebinding protection).
DNS_NAME = 'smoke.example.net'
DNS_A = '1.1.1.1'
DNS_AAAA = '2606:4700:4700::1111'


def command(*args, timeout=30):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()
    except subprocess.CalledProcessError as error:
        print(error.output, file=sys.stderr, flush=True)
        raise


def inside(name, *args):
    return command('docker', 'exec', name, *args)


class Resolver(BaseResolver):
    def resolve(self, request, _handler):
        reply = request.reply()
        q = request.q
        if str(q.qname) == DNS_NAME + '.':
            if q.qtype == QTYPE.A:
                reply.add_answer(RR(q.qname, QTYPE.A, ttl=30, rdata=A(DNS_A)))
            if q.qtype == QTYPE.AAAA:
                reply.add_answer(RR(q.qname, QTYPE.AAAA, ttl=30, rdata=AAAA(DNS_AAAA)))
        return reply


def serve(root):
    (root / 'network.pid').write_text(str(os.getpid()))
    (root / 'prefix-stage').write_text('1')
    for addr in ['fe80::1/64', '2001:db8:80:1::1/64', '2001:db8:80:2::1/64', TARGET + '/128']:
        command('ip', '-6', 'addr', 'add', addr, 'dev', BRIDGE, 'nodad')
    for tcp in (False, True):
        DNSServer(Resolver(), address='172.30.80.1', port=53, tcp=tcp).start_thread()
    mac = get_if_hwaddr(BRIDGE)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    (root / 'network-ready').touch()
    while not stop.is_set():
        stage = int((root / 'prefix-stage').read_text())
        packet = Ether(src=mac, dst='33:33:00:00:00:01') / IPv6(src='fe80::1', dst='ff02::1', hlim=255)
        packet /= ICMPv6ND_RA(routerlifetime=0)
        packet /= ICMPv6NDOptSrcLLAddr(lladdr=mac)
        for index in range(1, stage + 1):
            packet /= ICMPv6NDOptPrefixInfo(prefix=f'2001:db8:80:{index}::', prefixlen=64,
                                            L=1, A=1, validlifetime=300,
                                            preferredlifetime=180 if index == stage else 0)
        sendp(packet, iface=BRIDGE, verbose=False)
        stop.wait(2)


def wait_address(name, stage):
    import json
    prefix = f'2001:db8:80:{stage}:'
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        data = json.loads(inside(name, 'ip', '-j', '-6', 'addr', 'show', 'dev', 'eth0'))
        for iface in data:
            for address in iface.get('addr_info', []):
                if (address['local'].startswith(prefix) and address.get('preferred_life_time', 0) > 0
                        and 'tentative' not in address and 'dadfailed' not in address):
                    if inside(name, 'sysctl', '-n', 'net.ipv6.conf.eth0.accept_ra') == '2':
                        return address['local']
        time.sleep(2)
    raise AssertionError(f'No preferred dynamic address for {prefix}')


def verify(name, root):
    stage = int((root / 'prefix-stage').read_text())
    address = wait_address(name, stage)
    assert inside(name, 'sysctl', '-n', 'net.ipv6.conf.eth0.accept_ra') == '2'
    assert inside(name, 'sysctl', '-n', 'net.ipv6.conf.eth0.accept_ra_defrtr') == '0'
    assert inside(name, 'uci', 'get', 'network.lan.ip6addr') == 'fd70:6c61:6e64:80::2/64'
    result = inside(name, 'curl', '--noproxy', '*', '-gfsS', '--max-time', '10', f'http://[{TARGET}]:18081/')
    assert ipaddress.ip_address(result) == ipaddress.ip_address(address), (result, address)
    print(f'PASS: SLAAC prefix {stage}, static Docker gateway and dynamic public source', flush=True)
    return address


def check(name, root):
    lookup = inside(name, 'nslookup', DNS_NAME, '127.0.0.1')
    assert DNS_A in lookup and DNS_AAAA in lookup, lookup
    print('PASS: local dnsmasq resolves A and AAAA through the configured upstream', flush=True)
    address = verify(name, root)
    command('ping', '-6', '-c', '1', '-W', '3', address)
    blocked = subprocess.run(['curl', '--noproxy', '*', '-gfsS', '--max-time', '3',
                              f'http://[{address}]/'], capture_output=True, timeout=5)
    assert blocked.returncode != 0, 'Public IPv6 management is exposed'
    print('PASS: public IPv6 responds to ICMP but rejects the management HTTP port', flush=True)
    inside(name, 'sysctl', '-qw', 'net.ipv6.conf.eth0.accept_ra=0', 'net.ipv6.conf.eth0.autoconf=0')
    inside(name, '/etc/init.d/network', 'restart')
    verify(name, root)
    print('PASS: netifd restart reapplies RA settings through hotplug', flush=True)
    (root / 'prefix-stage').write_text('2')
    verify(name, root)
    print('PASS: prefix renewal deprecates the old source and selects the new one', flush=True)


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'serve':
        serve(Path(sys.argv[2]))
    elif mode == 'stop':
        pid_file = Path(sys.argv[2]) / 'network.pid'
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
    elif mode == 'check':
        check(sys.argv[2], Path(sys.argv[3]))
    elif mode == 'verify':
        verify(sys.argv[2], Path(sys.argv[3]))
    else:
        raise ValueError(mode)
