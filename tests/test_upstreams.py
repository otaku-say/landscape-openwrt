import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('upstreams', Path(__file__).parents[1] / 'scripts/upstreams.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.state = dict(immortalwrt_version='25.12.2', immortalwrt_commit='a'*40,
                          rootfs_sha256='b'*64, passwall_release='26.9.9-1', passwall_version='26.9.9-r1',
                          passwall_sha256='c'*64, passwall_i18n_sha256='d'*64,
                          dependency_feed_sha256='e'*64, dependency_key_sha256='f'*64,
                          handler_version='v0.24.3', handler_sha256='1'*64, source_revision='2'*40)
        self.state['inputs_digest'] = u.inputs_digest(self.state)

    def test_skip_unchanged_inputs(self):
        self.assertTrue(u.same_inputs({'dev.landscape.inputs.digest': self.state['inputs_digest']}, self.state))

    def test_passwall_and_every_input_change_rebuilds(self):
        for key in self.state:
            if key == 'inputs_digest':
                continue
            with self.subTest(key=key):
                changed = dict(self.state, **{key: 'different'})
                changed['inputs_digest'] = u.inputs_digest(changed)
                self.assertFalse(u.same_inputs({'dev.landscape.inputs.digest': self.state['inputs_digest']}, changed))

    def test_resolve_time_does_not_trigger_rebuild(self):
        self.assertEqual(u.inputs_digest(dict(self.state, resolved_at='tomorrow')), self.state['inputs_digest'])

    def test_missing_input_digest_triggers_build(self):
        self.assertFalse(u.same_inputs({'org.opencontainers.image.revision': '2'*40}, self.state))

    def test_numeric_stable_tag_selection(self):
        tags = [{'name': t} for t in ['v25.12.2', 'v25.12.10', 'v26.0.0-rc1', 'v24.10.99']]
        self.assertEqual(u.latest_stable_tag(tags)['name'], 'v25.12.10')

    def test_reject_missing_stable_tag(self):
        with self.assertRaises(ValueError):
            u.latest_stable_tag([{'name': 'v26.0.0-rc1'}])

    def test_reject_prerelease_and_draft(self):
        for flag in ('draft', 'prerelease'):
            with self.assertRaises(ValueError):
                u.stable_release({flag: True})

    def test_checksum_exact_filename(self):
        self.assertEqual(u.rootfs_checksum('a'*64+'  rootfs.tar.gz\n'+'b'*64+'  other.tar.gz', 'rootfs.tar.gz'), 'a'*64)
        for text in ['', 'bad rootfs.tar.gz', ('a'*64+'  rootfs.tar.gz\n')*2]:
            with self.assertRaises(ValueError):
                u.rootfs_checksum(text, 'rootfs.tar.gz')

    def test_corrupt_download_rejected(self):
        u.verify_digest(b'correct', u.digest_of(b'correct'))
        for digest in ['', 'sha256:'+'f'*64]:
            with self.assertRaises(ValueError):
                u.verify_digest(b'correct', digest)

    def test_select_exact_apk_and_encoded_plus(self):
        release = {'tag_name': '26.9.9-1', 'assets': [{
            'name': '25.12+_luci-app-passwall-26.9.9-r1.apk',
            'digest': 'sha256:'+'a'*64,
            'browser_download_url': 'https://github.com/Openwrt-Passwall/openwrt-passwall/releases/download/26.9.9-1/25.12%2B_luci-app-passwall-26.9.9-r1.apk'}]}
        pattern = r'25\.12\+_luci-app-passwall-.*\.apk'
        self.assertEqual(u.select_asset(release, pattern, 'Openwrt-Passwall/openwrt-passwall'), release['assets'][0])
        for property, value in [('digest', ''), ('browser_download_url', 'https://example.com/package.apk')]:
            changed = copy.deepcopy(release)
            changed['assets'][0][property] = value
            with self.assertRaises(ValueError):
                u.select_asset(changed, pattern, 'Openwrt-Passwall/openwrt-passwall')
        release['assets'].append(release['assets'][0])
        with self.assertRaises(ValueError):
            u.select_asset(release, pattern, 'Openwrt-Passwall/openwrt-passwall')

    @patch.object(u, 'json_request', return_value={'token': 'test'})
    def test_amd64_selection(self, _):
        registry = u.Registry('ghcr.io', 'owner/image')
        config = b'{"os":"linux","architecture":"amd64"}'
        index = {'manifests': [{'digest': 'amd64', 'platform': {'os': 'linux', 'architecture': 'amd64'}},
                               {'digest': 'arm64', 'platform': {'os': 'linux', 'architecture': 'arm64'}}]}
        with patch.object(registry, 'manifest', side_effect=[(index, 'index'), ({'config': {'digest': u.digest_of(config)}}, 'amd64')]):
            with patch.object(registry, 'get', return_value=(config, {})):
                self.assertEqual(registry.amd64('latest')[0], 'amd64')

    @patch.object(u, 'json_request', return_value={'token': 'test'})
    def test_missing_amd64_rejected(self, _):
        registry = u.Registry('ghcr.io', 'owner/image')
        with patch.object(registry, 'manifest', return_value=({'manifests': []}, 'index')):
            with self.assertRaises(ValueError):
                registry.amd64('latest')
