import copy
import importlib.util
from pathlib import Path
import unittest
import urllib.error
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

    def test_monitored_upstream_change_rebuilds(self):
        # 只有三个被监控上游的 tag 信号 + 自身源码修订会触发重建
        keys = [
            'immortalwrt_version', 'immortalwrt_commit',
            'passwall_release', 'handler_version', 'source_revision',
        ]
        for key in keys:
            with self.subTest(key=key):
                changed = self._change_key(key, 'different')
                self.assertFalse(u.same_inputs({'dev.landscape.inputs.digest': self.state['inputs_digest']}, changed))

    def test_content_drift_does_not_rebuild(self):
        # rootfs/APK/feed/key 的内容哈希漂移只影响下载完整性校验，不触发重建
        # （回归：2026-09-20 因 packages.adb 内容更新引发的浪费构建）
        keys = [
            'passwall_version', 'passwall_sha256', 'passwall_i18n_sha256',
            'dependency_key_sha256',
            'arches.amd64.rootfs_sha256', 'arches.amd64.dependency_feed_sha256',
            'arches.amd64.handler_sha256',
            'arches.arm64.rootfs_sha256', 'arches.arm64.dependency_feed_sha256',
            'arches.arm64.handler_sha256',
        ]
        for key in keys:
            with self.subTest(key=key):
                changed = self._change_key(key, 'different')
                self.assertTrue(u.same_inputs({'dev.landscape.inputs.digest': self.state['inputs_digest']}, changed))

    def test_resolve_time_does_not_trigger_rebuild(self):
        changed = dict(self.state, resolved_at='tomorrow')
        self.assertEqual(u.inputs_digest(changed), self.state['inputs_digest'])

    def test_missing_input_digest_triggers_build(self):
        self.assertFalse(u.same_inputs({'org.opencontainers.image.revision': '2'*40}, self.state))

    def test_numeric_stable_tag_selection(self):
        tags = [{'name': t} for t in ['v25.12.2', 'v25.12.10', 'v26.0.0-rc1', 'v24.10.99']]
        self.assertEqual(u.latest_stable_tag(tags)['name'], 'v25.12.10')

    def test_newest_stable_passwall_release(self):
        releases = [{'tag_name': t} for t in
                    ['26.9.9-1', '26.9.16-1', '26.8.19-2', '26.8.19-1', 'not-a-version']]
        self.assertEqual(u.newest_stable_release(releases, r'\d+\.\d+\.\d+-\d+')['tag_name'],
                         '26.9.16-1')

    def test_release_version_ranking_is_numeric(self):
        releases = [{'tag_name': t} for t in ['v0.24.3', 'v0.24.10', 'v0.9.99']]
        self.assertEqual(u.newest_stable_release(releases, r'v\d+\.\d+\.\d+')['tag_name'], 'v0.24.10')

    def test_newest_stable_landscape_release_skips_prerelease(self):
        # 回归：2026-10-04 upstream 把 v0.25.2 标为 prerelease，流水线必须回退到 v0.25.1
        releases = [{'tag_name': 'v0.25.2', 'prerelease': True},
                    {'tag_name': 'v0.25.1'},
                    {'tag_name': 'v0.25.0'},
                    {'tag_name': 'v0.24.10'},
                    {'tag_name': 'v0.25.0-rc1'}]
        self.assertEqual(u.newest_stable_release(releases, r'v\d+\.\d+\.\d+')['tag_name'], 'v0.25.1')

    def test_draft_and_prerelease_are_never_inputs(self):
        for flag in ('draft', 'prerelease'):
            with self.subTest(flag=flag):
                releases = [{'tag_name': 'v0.25.2', flag: True}, {'tag_name': 'v0.25.1'}]
                self.assertEqual(u.newest_stable_release(releases, r'v\d+\.\d+\.\d+')['tag_name'],
                                 'v0.25.1')
        with self.assertRaises(ValueError):
            u.newest_stable_release([{'tag_name': 'v0.25.2', 'prerelease': True}],
                                    r'v\d+\.\d+\.\d+')

    def test_reject_missing_matching_release(self):
        with self.assertRaises(ValueError):
            u.newest_stable_release([{'tag_name': 'v0.25.0-rc1'}], r'v\d+\.\d+\.\d+')

    def test_reject_missing_stable_tag(self):
        with self.assertRaises(ValueError):
            u.latest_stable_tag([{'name': 'v26.0.0-rc1'}])

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


class RequestRetryTest(unittest.TestCase):
    """Transient upstream failures must be retried, permanent ones must not."""

    class Response:
        def __init__(self, body):
            self.body = body
            self.headers = {}

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def _error(self, code, retry_after=None):
        headers = {'Retry-After': retry_after} if retry_after else {}
        return urllib.error.HTTPError('https://example.com/x', code, 'error', headers, None)

    @patch.object(u.time, 'sleep')
    @patch.object(u.urllib.request, 'urlopen')
    def test_transient_status_is_retried(self, urlopen, sleep):
        urlopen.side_effect = [self._error(522), self._error(503), self.Response(b'payload')]
        body, _ = u.request('https://example.com/x')
        self.assertEqual(body, b'payload')
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(u.time, 'sleep')
    @patch.object(u.urllib.request, 'urlopen')
    def test_permanent_status_fails_immediately(self, urlopen, sleep):
        urlopen.side_effect = self._error(404)
        with self.assertRaises(urllib.error.HTTPError):
            u.request('https://example.com/x')
        self.assertEqual(urlopen.call_count, 1)
        self.assertFalse(sleep.called)

    @patch.object(u.time, 'sleep')
    @patch.object(u.urllib.request, 'urlopen')
    def test_connection_errors_are_retried(self, urlopen, sleep):
        urlopen.side_effect = [urllib.error.URLError('timed out'), self.Response(b'payload')]
        self.assertEqual(u.request('https://example.com/x')[0], b'payload')
        self.assertEqual(urlopen.call_count, 2)

    @patch.object(u.time, 'sleep')
    @patch.object(u.urllib.request, 'urlopen')
    def test_retries_are_bounded(self, urlopen, sleep):
        urlopen.side_effect = self._error(504)
        with self.assertRaises(urllib.error.HTTPError):
            u.request('https://example.com/x')
        self.assertEqual(urlopen.call_count, u.HTTP_ATTEMPTS)
        self.assertEqual(sleep.call_count, u.HTTP_ATTEMPTS - 1)

    @patch.object(u.time, 'sleep')
    @patch.object(u.urllib.request, 'urlopen')
    def test_retry_after_is_honoured(self, urlopen, sleep):
        urlopen.side_effect = [self._error(429, retry_after='7'), self.Response(b'payload')]
        u.request('https://example.com/x')
        self.assertEqual(sleep.call_args[0][0], 7.0)

    @patch.object(u.time, 'sleep')
    @patch.object(u.urllib.request, 'urlopen')
    def test_backoff_grows_and_stays_bounded(self, urlopen, sleep):
        urlopen.side_effect = self._error(503)
        with self.assertRaises(urllib.error.HTTPError):
            u.request('https://example.com/x')
        delays = [call[0][0] for call in sleep.call_args_list]
        self.assertEqual(delays, sorted(delays))
        self.assertLessEqual(max(delays), u.HTTP_MAX_DELAY * 1.25)
        self.assertGreaterEqual(min(delays), u.HTTP_BASE_DELAY * 0.75)


if __name__ == '__main__':
    unittest.main()
