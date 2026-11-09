import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('flow_fixture', ROOT / 'scripts/test-flow-exit.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class FlowFixtureTest(unittest.TestCase):
    def filters(self):
        commands = []

        def run(*args, **kwargs):
            commands.append(args)
            if args[:3] == ('ip', '-j', 'link'):
                return json.dumps([{'ifindex': 8, 'ifname': 'veth-fixture'}])
            if args[:2] == ('ip', '-n'):
                return json.dumps([{'address': '02:00:00:00:00:02'}])
            if args[0] == 'docker' and args[-1] == '/sys/class/net/eth0/iflink':
                return '8'
            if args[:2] == ('docker', 'inspect'):
                return json.dumps({'fixture': {'MacAddress': '02:00:00:00:00:03'}})
            return ''

        with patch.object(flow, 'run', side_effect=run), patch.object(flow.Path, 'read_text', return_value='02:00:00:00:00:01'):
            flow.lan_return_path()
            flow.tagged_redirect('fixture')
        return [args for args in commands if args[:3] == ('tc', 'filter', 'replace')]

    def test_protocols_have_distinct_priorities_and_edit_continues(self):
        filters = self.filters()
        self.assertEqual(len(filters), 4)
        keys = {(args[args.index('dev') + 1], args[args.index('pref') + 1]) for args in filters}
        self.assertEqual(len(keys), 4)
        for args in filters:
            start = args.index('pedit')
            next_action = args.index('action', start)
            self.assertIn('pipe', args[start:next_action])

    def test_tagged_flow_targets_enrolled_veth_not_bridge(self):
        filters = [args for args in self.filters() if args[args.index('dev') + 1] == flow.LAN_LINK]
        self.assertEqual(len(filters), 2)
        for args in filters:
            self.assertEqual(args[-1], 'veth-fixture')
            self.assertEqual(args[args.index('id') + 1], '3079')

    def test_exit_tls_material_generates_cert_key_and_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            cert_lines, key_lines, pin_sha256 = flow.exit_tls_material(Path(directory))
        self.assertEqual(cert_lines[0], '-----BEGIN CERTIFICATE-----')
        self.assertEqual(cert_lines[-1], '-----END CERTIFICATE-----')
        self.assertIn('PRIVATE KEY', key_lines[0])
        self.assertRegex(pin_sha256, re.compile(r'^[0-9a-f]{64}$'))
