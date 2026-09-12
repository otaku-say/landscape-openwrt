import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).parents[1]
HELPER = ROOT / 'rootfs/usr/libexec/landscape-management'
KEYS = ('LUCI_HTTP_PORT', 'LUCI_HTTPS_PORT', 'SSH_PORT')


class ManagementTest(unittest.TestCase):
    def check_ports(self, values):
        environment = {key: value for key, value in os.environ.items() if key not in KEYS}
        return subprocess.run(['sh', HELPER, '--check'], env={**environment, **values}, capture_output=True)

    def test_defaults_and_custom_ports(self):
        for values in ({}, dict(zip(KEYS, ('18000', '18443', '12222'))),
                       dict(zip(KEYS, ('1', '65534', '65535')))):
            with self.subTest(values=values):
                self.assertEqual(self.check_ports(values).returncode, 0)

    def test_invalid_ports_fail_before_writing(self):
        for key in KEYS:
            for value in ('', '0', '01', '53', '65536', '12345678901234567890', '-1',
                          '8.0', '8000/tcp', ' 8000', '8000\n', '$(id)', '0x50'):
                with self.subTest(key=key, value=value):
                    self.assertNotEqual(self.check_ports({key: value}).returncode, 0)

    def test_pairwise_conflicts_rejected(self):
        for values in (('8000', '8000', '2222'), ('8000', '8443', '8000'),
                       ('8000', '2222', '2222')):
            with self.subTest(values=values):
                self.assertNotEqual(self.check_ports(dict(zip(KEYS, values))).returncode, 0)

    def test_no_host_publishing_or_generation_switch(self):
        self.assertNotIn('EXPOSE ', (ROOT / 'Dockerfile').read_text())
        compose = (ROOT / 'docker-compose.yaml').read_text()
        self.assertNotIn('    ports:', compose)
        self.assertNotIn('LAN_BIND_IP', compose)
        self.assertNotIn('.generation=', (ROOT / 'start.sh').read_text())
