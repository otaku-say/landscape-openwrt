import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).parents[1] / 'rootfs/usr/libexec/landscape-password'


class PasswordTest(unittest.TestCase):
    def test_empty_placeholder_and_newlines_rejected(self):
        for password in ('', 'CHANGE_ME_BEFORE_START', 'long-password\nroot:other', 'long-password\rmore'):
            with self.subTest(case=len(password)):
                result = subprocess.run(['sh', HELPER], env={**os.environ, 'LAND_ROOT_PASSWORD': password}, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                if password:
                    self.assertNotIn(password.encode(), result.stderr + result.stdout)

    def test_password_goes_to_stdin_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'chpasswd'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_ARGS"\nwhile IFS= read -r line; do printf "%s\\n" "$line"; done > "$CAPTURE_INPUT"\n')
            executable.chmod(0o755)
            args = Path(directory) / 'args'
            stdin = Path(directory) / 'stdin'
            for password in ('1', '123', 'simple', 'Probe:$x;$(touch nope) ! full'):
                with self.subTest(length=len(password)):
                    result = subprocess.run(['sh', HELPER], capture_output=True, env={**os.environ,
                        'PATH': directory + ':' + os.environ['PATH'], 'LAND_ROOT_PASSWORD': password,
                        'CAPTURE_ARGS': str(args), 'CAPTURE_INPUT': str(stdin)})
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(args.read_text(), '-c\nSHA512\n')
                    self.assertEqual(stdin.read_text(), 'root:' + password + '\n')
                    self.assertNotIn(password.encode(), result.stdout + result.stderr)

    def test_full_runtime_dependency_profile(self):
        packages = set((HELPER.parents[3] / 'scripts/passwall-packages.txt').read_text().split())
        self.assertTrue({'geoview', 'chinadns-ng', 'xray-core', 'sing-box', 'hysteria', 'naiveproxy'} <= packages)
        self.assertEqual(len(packages), 21)
