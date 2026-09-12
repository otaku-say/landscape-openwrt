import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
PARSER = ROOT / 'rootfs/usr/libexec/landscape-network-dns'
INITIALIZER = ROOT / 'rootfs/usr/libexec/landscape-dns'


class DnsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = self.root / 'calls'
        # The real OpenWrt validator uses inet_pton; CI also tests its actual binary.
        validator = self.root / 'validate_data'
        validator.write_text('''#!/usr/bin/env python3
import socket
import sys
if len(sys.argv) != 3 or sys.argv[1] != 'ipaddr':
    sys.exit(2)
for family in (socket.AF_INET, socket.AF_INET6):
    try:
        socket.inet_pton(family, sys.argv[2])
        sys.exit(0)
    except OSError:
        pass
sys.exit(1)
''')
        validator.chmod(0o755)
        uci = self.root / 'uci'
        uci.write_text('''#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ['UCI_CALLS'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:] == ['-q', 'get', 'landscape.container.initialized']:
    value = os.environ.get('INITIALIZED', '')
    if not value:
        sys.exit(1)
    print(value)
''')
        uci.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root) + ':' + os.environ['PATH'],
                    'UCI_CALLS': str(self.calls)}
        self.env.pop('LAND_DNS_ADDR', None)
        self.env.pop('INITIALIZED', None)

    def parse(self, value=None):
        env = dict(self.env)
        if value is not None:
            env['LAND_DNS_ADDR'] = value
        return subprocess.run(['sh', PARSER], env=env, capture_output=True, text=True)

    def test_single_multiple_and_mixed_families(self):
        for value, expected in (
            (None, '8.8.8.8'), ('8.8.8.8', '8.8.8.8'), ('10.10.66.1', '10.10.66.1'),
            ('8.8.8.8,1.1.1.1', '8.8.8.8 1.1.1.1'),
            ('2606:4700:4700::1111', '2606:4700:4700::1111'),
            ('8.8.8.8,2606:4700:4700::1111', '8.8.8.8 2606:4700:4700::1111'),
            ('fd10:10:66::1,2001:4860:4860::8888', 'fd10:10:66::1 2001:4860:4860::8888'),
            (' 8.8.8.8 ,\t1.1.1.1 ', '8.8.8.8 1.1.1.1'),
            ('::ffff:192.0.2.1', '::ffff:192.0.2.1'),
        ):
            with self.subTest(value=value):
                result = self.parse(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected + '\n')
                self.assertFalse(self.calls.exists(), 'DNS parsing must not write UCI')

    def test_invalid_list_is_rejected_without_partial_output(self):
        for value in ('', ' ', ',', ',8.8.8.8', '8.8.8.8,', '8.8.8.8, ',
                      '8.8.8.8,,1.1.1.1', '8.8.8.8, ,1.1.1.1', '8.8.8.8 1.1.1.1',
                      '8.8.8.8\uff0c1.1.1.1', '999.1.1.1', '8.8.8', '008.8.8.8',
                      '8.8.8.8,1.1.1.999', '8.8.8.8#53', '8.8.8.8:53', '8.8.8.8/32',
                      '[2606:4700:4700::1111]', 'fe80::1%eth0', '2001:::1',
                      '1:2:3:4:5:6:7:8:9', '2001:db8::/64', 'dns.google', 'https://dns.google/dns-query',
                      '8.8.8.8\n', '\r8.8.8.8', '8.8.8.8\n,1.1.1.1', '8.8.8.8;id', '$(id)', '*'):
            with self.subTest(value=value):
                result = self.parse(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertIn('LAND_DNS_ADDR', result.stderr)
                self.assertFalse(self.calls.exists())

    def test_missing_native_validator_fails_closed(self):
        (self.root / 'validate_data').unlink()
        result = self.parse('8.8.8.8')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('validate_data is required', result.stderr)

    def calls_after_init(self, initialized=''):
        result = subprocess.run(['sh', INITIALIZER], capture_output=True, text=True,
                                env={**self.env, 'INITIALIZED': initialized, 'LAND_DNS_ADDR': '8.8.8.8,1.1.1.1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_fresh_dnsmasq_reads_interface_resolver_file_not_forwarding_list(self):
        self.assertEqual(self.calls_after_init(), [
            ['-q', 'get', 'landscape.container.initialized'],
            ['set', 'dhcp.@dnsmasq[0].noresolv=0'],
            ['set', 'dhcp.@dnsmasq[0].resolvfile=/tmp/resolv.conf.d/resolv.conf.auto'],
            ['set', 'dhcp.@dnsmasq[0].localservice=0'],
            ['commit', 'dhcp'], ['set', 'landscape.container.initialized=1'], ['commit', 'landscape'],
        ])

    def test_initialized_dns_settings_are_never_overwritten(self):
        self.assertEqual(self.calls_after_init('1'), [['-q', 'get', 'landscape.container.initialized']])

    def test_start_validates_before_writes_and_sets_individual_interface_entries(self):
        source = (ROOT / 'start.sh').read_text()
        parse_at = source.index('dns_servers=$(/usr/libexec/landscape-network-dns)')
        self.assertLess(parse_at, source.index('/usr/libexec/landscape-password'))
        self.assertLess(parse_at, source.index('uci set'))
        self.assertIn('for dns in $dns_servers; do uci add_list network.lan.dns="$dns"; done', source)
        self.assertNotIn('landscape.container.dns=', source)
        self.assertNotIn('dhcp.@dnsmasq[0].server', source)
        initializer = INITIALIZER.read_text()
        self.assertNotIn('LAND_DNS_ADDR', initializer)
        self.assertNotIn('landscape.container.dns', initializer)
        self.assertNotIn('.server', initializer)

    def test_image_checks_native_validator_and_retains_example_default(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertIn('chpasswd validate_data xray', dockerfile)
        self.assertIn('LAND_DNS_ADDR=8.8.8.8', dockerfile)
        self.assertIn('LAND_DNS_ADDR=8.8.8.8', (ROOT / '.env.example').read_text())
