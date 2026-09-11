import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("upstreams", Path(__file__).parents[1] / "scripts/upstreams.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.state = {
            "base_digest": "sha256:" + "a" * 64,
            "handler_version": "v0.24.3",
            "handler_sha256": "b" * 64,
            "source_revision": "c" * 40,
        }
        self.labels = {
            "org.opencontainers.image.base.digest": self.state["base_digest"],
            "dev.landscape.handler.version": self.state["handler_version"],
            "dev.landscape.handler.sha256": self.state["handler_sha256"],
            "org.opencontainers.image.revision": self.state["source_revision"],
        }

    def test_unchanged_inputs_skip(self):
        self.assertTrue(u.same_inputs(self.labels, self.state))

    def test_each_changed_input_rebuilds(self):
        for key in self.labels:
            with self.subTest(key=key):
                changed = dict(self.labels, **{key: "changed"})
                self.assertFalse(u.same_inputs(changed, self.state))

    def test_missing_labels_rebuild(self):
        self.assertFalse(u.same_inputs({}, self.state))

    def test_corrupt_download_is_rejected(self):
        expected = u.digest_of(b"original")
        u.verify_digest(b"original", expected)
        with self.assertRaises(ValueError):
            u.verify_digest(b"corrupt", expected)
        with self.assertRaises(ValueError):
            u.verify_digest(b"original", "")

    @patch.object(u, "json_request", return_value={"token": "test"})
    def test_selects_only_linux_amd64(self, _):
        registry = u.Registry("registry-1.docker.io", u.BASE)
        config = b'{"os":"linux","architecture":"amd64"}'
        index = {"manifests": [
            {"digest": "amd64", "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": "arm64", "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "attestation", "platform": {"os": "unknown", "architecture": "unknown"}},
        ]}
        manifest = {"config": {"digest": u.digest_of(config)}}
        with patch.object(registry, "manifest", side_effect=[(index, "index"), (manifest, "amd64")]) as read:
            with patch.object(registry, "get", return_value=(config, {})):
                self.assertEqual(registry.amd64("latest")[0], "amd64")
                self.assertEqual(read.call_args.args, ("amd64",))

    @patch.object(u, "json_request", return_value={"token": "test"})
    def test_missing_amd64_is_rejected(self, _):
        registry = u.Registry("registry-1.docker.io", u.BASE)
        with patch.object(registry, "manifest", return_value=({"manifests": []}, "index")):
            with self.assertRaises(ValueError):
                registry.amd64("latest")

    @patch.object(u, "json_request")
    def test_prerelease_is_rejected(self, mock):
        mock.return_value = {"prerelease": True}
        with self.assertRaises(ValueError):
            u.resolve()

    @patch.object(u, "json_request")
    def test_release_without_checksum_is_rejected(self, mock):
        mock.return_value = {"tag_name": "v0.24.3", "assets": [{"name": u.ASSET_NAME}]}
        with self.assertRaises(ValueError):
            u.resolve()


if __name__ == "__main__":
    unittest.main()
