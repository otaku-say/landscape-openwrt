import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).parents[1]
HELPER = ROOT / 'rootfs/usr/libexec/landscape-console'
FIXTURE = ROOT / 'scripts/test-console.py'


class ConsoleTest(unittest.TestCase):
    def test_inittab_only_manages_service_lifecycle(self):
        self.assertEqual((ROOT / 'rootfs/etc/inittab').read_text().splitlines(), [
            '::sysinit:/etc/init.d/rcS S boot', '::shutdown:/etc/init.d/rcS K shutdown',
        ])

    def test_masks_before_init_and_checks_health(self):
        start = (ROOT / 'start.sh').read_text()
        self.assertLess(start.index('/usr/libexec/landscape-console'), start.index('exec /sbin/init'))
        self.assertIn('/usr/libexec/landscape-console --check',
                      (ROOT / 'rootfs/usr/libexec/landscape-healthcheck').read_text())
        self.assertIn('COPY rootfs/ /', (ROOT / 'Dockerfile').read_text())

    def run_guard(self, mountinfo, docker=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if docker:
                (root / 'dockerenv').touch()
            (root / 'mountinfo').write_text(mountinfo)
            script = HELPER.read_text().replace('/.dockerenv', str(root / 'dockerenv'))
            script = script.replace('/proc/self/mountinfo', str(root / 'mountinfo'))
            # --check cannot mount or create nodes, even on a machine with consoles.
            (root / 'check.sh').write_text(script)
            return subprocess.run(['sh', root / 'check.sh', '--check'], text=True, capture_output=True)

    def test_rejects_non_docker_environment(self):
        result = self.run_guard('', docker=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Only run inside', result.stderr)

    def test_rejects_host_devtmpfs_and_missing_dev_mount(self):
        for info in ['', '25 1 0:6 / /dev rw - devtmpfs devtmpfs rw\n']:
            with self.subTest(info=info):
                result = self.run_guard(info)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('private Docker tmpfs', result.stderr)

    def test_rejects_shared_and_slave_dev_mounts(self):
        for propagation in ['shared:1', 'master:2', 'shared:1 master:2']:
            with self.subTest(propagation=propagation):
                result = self.run_guard(f'25 1 0:6 / /dev rw {propagation} - tmpfs tmpfs rw\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('private Docker tmpfs', result.stderr)

    def test_accepts_private_tmpfs_guard(self):
        result = self.run_guard('25 1 0:6 / /dev rw - tmpfs tmpfs rw,size=65536k\n')
        self.assertNotIn('private Docker tmpfs', result.stderr)
        self.assertNotIn('Only run inside', result.stderr)

    def test_device_patterns_exclude_interactive_container_ptys(self):
        script = HELPER.read_text()
        devices = script.split('for device in ', 1)[1].split('; do', 1)[0].replace('\\\n', ' ').split()
        self.assertIn('/dev/console', devices)
        self.assertIn('/dev/kmsg', devices)
        self.assertIn('/dev/tty[0-9]*', devices)
        self.assertIn('/dev/tty[A-Z]*', devices)
        self.assertIn('/dev/hvc[0-9]*', devices)
        self.assertNotIn('/dev/tty', devices)
        self.assertFalse(any('/dev/pts' in item or '/dev/ptmx' in item for item in devices))


class ConsoleFixtureTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.server = subprocess.Popen([sys.executable, FIXTURE, 'serve', self.root],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            path = self.root / 'console-devices.json'
            if path.exists() and path.stat().st_size:
                self.devices = json.loads(path.read_text())
                return
            if self.server.poll() is not None:
                self.fail(self.server.stderr.read().decode())
            time.sleep(0.02)
        self.fail('Console fixture did not become ready')

    def stop_server(self):
        self.server.terminate()
        try:
            self.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait()
        self.server.stderr.close()

    def check(self):
        return subprocess.run([sys.executable, FIXTURE, 'check', self.root], text=True, capture_output=True)

    def test_untouched_serial_and_vt_pass(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no data or termios changes', result.stdout)

    def test_detects_console_output(self):
        with open(self.devices[0], 'wb', buffering=0) as serial:
            serial.write(b'unexpected init console output')
        result = self.check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('received_bytes', result.stderr)

    def test_detects_terminal_attribute_changes(self):
        fd = os.open(self.devices[1], os.O_RDWR | os.O_NOCTTY)
        try:
            settings = termios.tcgetattr(fd)
            settings[3] ^= termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, settings)
        finally:
            os.close(fd)
        result = self.check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('termios_unchanged', result.stderr)
