import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
HELPER = ROOT / 'rootfs/usr/libexec/landscape-proxy-check'


class ProxyCheckTest(unittest.TestCase):
    def run_check(self, nft_status='0', dns_options='nftset', quiet=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = {
                'dnsmasq': '#!/bin/sh\nprintf "%s\\n" "$DNS_OPTIONS"\n',
                'nft': '#!/bin/sh\nprintf "%s\\n" "$@" > "$PROBE_ARGS"\ncat > "$PROBE_RULES"\nexit "$NFT_STATUS"\n',
                'ip': '#!/bin/sh\nexit 0\n',
                'fw4': '#!/bin/sh\nexit 0\n',
            }
            for name, content in commands.items():
                file = root / name
                file.write_text(content)
                file.chmod(0o755)
            result = subprocess.run(['sh', HELPER, *(['--quiet'] if quiet else [])], capture_output=True,
                                    env={**os.environ, 'PATH': directory + ':' + os.environ['PATH'],
                                         'DNS_OPTIONS': dns_options, 'NFT_STATUS': nft_status,
                                         'PROBE_ARGS': str(root / 'args'), 'PROBE_RULES': str(root / 'rules')})
            args = (root / 'args').read_text() if (root / 'args').exists() else ''
            rules = (root / 'rules').read_text() if (root / 'rules').exists() else ''
            return result, args, rules

    def test_success_uses_check_only_and_both_protocol_families(self):
        result, args, rules = self.run_check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(args, '--check\n--file\n-\n')
        self.assertIn('tproxy ip to', rules)
        self.assertIn('tproxy ip6 to', rules)
        self.assertIn('socket transparent 1', rules)
        self.assertIn('redirect to :9', rules)

    def test_failed_kernel_capability_remains_a_failure(self):
        result, _, _ = self.run_check(nft_status='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Host kernel', result.stderr)
        result, _, _ = self.run_check(nft_status='1', quiet=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, b'')

    def test_no_nftset_is_not_mistaken_for_nftset(self):
        result, args, _ = self.run_check(dns_options='no-nftset')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, '')

    def test_official_passwall_source_is_not_patched(self):
        self.assertFalse((ROOT / 'scripts/passwall-capabilities.patch').exists())
        self.assertNotIn('passwall-capabilities.patch', (ROOT / 'scripts/install-packages.sh').read_text())
        self.assertNotIn('passwall-capabilities.patch', (ROOT / 'Dockerfile').read_text())

    def test_no_host_nat_compatibility_service(self):
        self.assertFalse((ROOT / 'scripts/host-network.py').exists())
        self.assertFalse((ROOT / 'scripts/test-mixed-routing.py').exists())
        self.assertNotIn('LAN_INTERFACES', (ROOT / 'docker-compose.yaml').read_text())
