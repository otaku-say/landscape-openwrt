import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
SERVICE = ROOT / 'rootfs/etc/init.d/landscape-redirect'


class RedirectLoggingTest(unittest.TestCase):
    def run_service(self, level='', prepared=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'prepared'
            if prepared:
                marker.touch()
            service = root / 'service.sh'
            service.write_text(SERVICE.read_text().replace('/tmp/landscape-prepared', str(marker)))
            harness = root / 'harness.sh'
            harness.write_text('''#!/bin/sh
uci() { [ -n "$TEST_LOG_LEVEL" ] || return 1; printf '%s\\n' "$TEST_LOG_LEVEL"; }
logger() { printf '%s\\n' "$*" >&2; }
procd_open_instance() { printf 'open\\n'; }
procd_set_param() { printf '%s\\n' "$*"; }
procd_close_instance() { printf 'close\\n'; }
. "$1"
start_service
''')
            return subprocess.run(['sh', str(harness), str(service)], text=True, capture_output=True,
                                  env={**os.environ, 'TEST_LOG_LEVEL': level})

    def test_default_and_error_preserve_error_output(self):
        for level in ('', 'ERROR'):
            with self.subTest(level=level):
                result = self.run_service(level)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--log-level ERROR\n', result.stdout)
                self.assertIn('stdout 1\n', result.stdout)
                self.assertIn('stderr 1\n', result.stdout)
                self.assertNotIn('--log-level INFO', result.stdout)

    def test_off_silences_both_streams_without_disabling_handler(self):
        result = self.run_service('OFF')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('command /usr/bin/redirect_pkg_handler --mode route --sock_path /ld_unix_link --log-level OFF\n', result.stdout)
        self.assertIn('stdout 0\n', result.stdout)
        self.assertIn('stderr 0\n', result.stdout)
        self.assertIn('respawn 3600 5 0\n', result.stdout)
        self.assertIn('limits memlock=unlimited unlimited\n', result.stdout)
        self.assertTrue(result.stdout.endswith('close\n'))

    def test_explicit_diagnostic_levels_are_preserved(self):
        for level in ('WARN', 'INFO', 'DEBUG', 'TRACE'):
            with self.subTest(level=level):
                result = self.run_service(level)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'--log-level {level}\n', result.stdout)
                self.assertIn('stdout 1\n', result.stdout)
                self.assertIn('stderr 1\n', result.stdout)

    def test_invalid_uci_level_fails_before_start(self):
        for level in ('invalid', 'ERROR INFO', 'OFF; false'):
            with self.subTest(level=level):
                result = self.run_service(level)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertIn('Invalid log level', result.stderr)

    def test_network_preparation_is_still_required(self):
        result = self.run_service('OFF', prepared=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Network preparation failed', result.stderr)

    def test_error_default_is_consistent_across_entrypoints(self):
        self.assertIn('LAND_REDIRECT_LOG_LEVEL=ERROR', (ROOT / '.env.example').read_text())
        self.assertIn('LAND_REDIRECT_LOG_LEVEL=ERROR', (ROOT / 'Dockerfile').read_text())
        self.assertIn('log_level=${LAND_REDIRECT_LOG_LEVEL:-ERROR}', (ROOT / 'start.sh').read_text())
        self.assertIn('${LAND_REDIRECT_LOG_LEVEL:-ERROR}', (ROOT / 'docker-compose.yaml').read_text())
        self.assertIn('level=${level:-ERROR}', SERVICE.read_text())
