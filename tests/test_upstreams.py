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
        self.state = dict(
            immortalwrt_version='25.12.2', immortalwrt_commit='a'*40,
            passwall_release='26.9.9-1', passwall_version='26.9.9-r1',
            passwall_url='https://example.com/passwall.apk',
            passwall_sha256='c'*64, passwall_i18n_url='https://example.com/passwall-zh.apk',
            passwall_i18n_sha256='d'*64,
            dependency_key_url='https://example.com/apk.pub',
            dependency_key_sha256='f'*64,
            handler_version='v0.24.3',
            source_revision='2'*40,
            arches={
                'amd64': {
                    'rootfs_url': 'https://example.com/amd64-rootfs.tar.gz',
                    'rootfs_sha256': 'b'*64,
                    'dependency_feed_url': 'https://example.com/amd64-packages.adb',
                    'dependency_feed_sha256': 'e'*64,
                    'handler_version': 'v0.24.3',
                    'handler_url': 'https://example.com/amd64-handler',
                    'handler_sha256': '1'*64,
                    'handler_source_url': 'https://example.com/amd64-source.tar.gz',
                },
                'arm64': {
                    'rootfs_url': 'https://example.com/arm64-rootfs.tar.gz',
                    'rootfs_sha256': 'aa' + 'b'*62,
                    'dependency_feed_url': 'https://example.com/arm64-packages.adb',
                    'dependency_feed_sha256': 'ee' + 'e'*62,
                    'handler_version': 'v0.24.3',
                    'handler_url': 'https://example.com/arm64-handler',
                    'handler_sha256': '11' + '1'*62,
                    'handler_source_url': 'https://example.com/arm64-source.tar.gz',
                },
            },
        )
        self.state['inputs_digest'] = u.inputs_digest(self.state)

    def test_skip_unchanged_inputs(self):
        self.assertTrue(u.same_inputs({'dev.landscape.inputs.digest': self.state['inputs_digest']}, self.state))

    def _change_key(self, key, value):
        changed = copy.deepcopy(self.state)
        if '.' in key:
            parts = key.split('.')
            obj = changed
            for part in parts[:-1]:
                obj = obj[part]
            obj[parts[-1]] = value
        else:
            changed[key] = value
        changed['inputs_digest'] = u.inputs_digest(changed)
        return changed

    def test_passwall_and_every_input_change_rebuilds(self):
        keys = [
            'immortalwrt_version', 'immortalwrt_commit', 'passwall_release',
            'passwall_version', 'passwall_sha256', 'passwall_i18n_sha256',
            'dependency_key_sha256', 'source_revision',
            'arches.amd64.rootfs_sha256', 'arches.amd64.dependency_feed_sha256',
            'arches.amd64.handler_sha256',
            'arches.arm64.rootfs_sha256', 'arches.arm64.dependency_feed_sha256',
            'arches.arm64.handler_sha256',
        ]
        for key in keys:
            with self.subTest(key=key):
                changed = self._change_key(key, 'different')
                self.assertFalse(u.same_inputs({'dev.landscape.inputs.digest': self.state['inputs_digest']}, changed))

    def test_resolve_time_does_not_trigger_rebuild(self):
        changed = dict(self.state, resolved_at='tomorrow')
        self.assertEqual(u.inputs_digest(changed), self.state['inputs_digest'])

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
    def test_arm64_selection(self, _):
        registry = u.Registry('ghcr.io', 'owner/image')
        config = b'{"os":"linux","architecture":"arm64"}'
        index = {'manifests': [{'digest': 'amd64', 'platform': {'os': 'linux', 'architecture': 'amd64'}},
                               {'digest': 'arm64', 'platform': {'os': 'linux', 'architecture': 'arm64'}}]}
        with patch.object(registry, 'manifest', side_effect=[(index, 'index'), ({'config': {'digest': u.digest_of(config)}}, 'arm64')]):
            with patch.object(registry, 'get', return_value=(config, {})):
                self.assertEqual(registry.platform_config('latest', 'arm64')[0], 'arm64')

    @patch.object(u, 'json_request', return_value={'token': 'test'})
    def test_missing_platform_rejected(self, _):
        registry = u.Registry('ghcr.io', 'owner/image')
        with patch.object(registry, 'manifest', return_value=({'manifests': []}, 'index')):
            with self.assertRaises(ValueError):
                registry.platform_config('latest', 'arm64')


if __name__ == '__main__':
    unittest.main()
