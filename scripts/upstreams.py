#!/usr/bin/env python3
"""Resolve immutable inputs and compare them with the last published image."""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

IMMORTAL_TAGS_API = "https://api.github.com/repos/immortalwrt/immortalwrt/tags?per_page=100"
PASSWALL_API = "https://api.github.com/repos/Openwrt-Passwall/openwrt-passwall/releases/latest"
RELEASE_API = "https://api.github.com/repos/ThisSeanZhang/landscape/releases/latest"
DEPENDENCY_KEY_URL = "https://master.dl.sourceforge.net/project/openwrt-passwall-build/apk.pub"
DEPENDENCY_KEY_SHA256 = "52802b143489214e13b78f96599a147a638205cc22d9dd6d71229504e38ddc00"
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

# Landscape publishes a multi-arch manifest list. The Docker platforms and the
# upstream ImmortalWrt/PassWall architecture names do not line up directly, so
# keep the mapping in one place.
ARCHES = {
    "amd64": {
        "docker_platform": "linux/amd64",
        "immortalwrt_target": "x86/64",
        "rootfs_prefix": "immortalwrt-{version}-x86-64",
        "package_arch": "x86_64",
        "handler_suffix": "x86_64",
    },
    "arm64": {
        "docker_platform": "linux/arm64",
        "immortalwrt_target": "armsr/armv8",
        "rootfs_prefix": "immortalwrt-{version}-armsr-armv8",
        "package_arch": "aarch64_generic",
        "handler_suffix": "aarch64",
    },
}


def request(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "landscape-openwrt", **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read(), response.headers


def json_request(url, headers=None):
    body, _ = request(url, headers)
    return json.loads(body)


def digest_of(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def verify_digest(data, expected):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected or "") or digest_of(data) != expected:
        raise ValueError("Downloaded content digest mismatch")


class Registry:
    def __init__(self, host, repository, username=None, password=None):
        self.host = host
        self.repository = repository
        if host == "registry-1.docker.io":
            auth_url = "https://auth.docker.io/token"
            service = "registry.docker.io"
        elif host == "ghcr.io":
            auth_url = "https://ghcr.io/token"
            service = "ghcr.io"
        else:
            raise ValueError("Unsupported registry")
        auth = {}
        if username and password:
            basic = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth["Authorization"] = "Basic " + basic
        query = urllib.parse.urlencode({"service": service, "scope": f"repository:{repository}:pull"})
        result = json_request(auth_url + "?" + query, auth)
        self.headers = {"Authorization": "Bearer " + (result.get("token") or result["access_token"])}

    def get(self, kind, reference):
        return request(f"https://{self.host}/v2/{self.repository}/{kind}/{reference}",
                       {**self.headers, "Accept": ACCEPT})

    def manifest(self, reference):
        raw, headers = self.get("manifests", reference)
        digest = headers.get("Docker-Content-Digest", digest_of(raw))
        verify_digest(raw, digest)
        if reference.startswith("sha256:"):
            verify_digest(raw, reference)
        return json.loads(raw), digest

    def platform_config(self, reference, architecture):
        manifest, digest = self.manifest(reference)
        if "manifests" in manifest:
            entries = [m for m in manifest["manifests"]
                       if m.get("platform", {}).get("os") == "linux"
                       and m.get("platform", {}).get("architecture") == architecture]
            if len(entries) != 1:
                raise ValueError(f"Expected exactly one linux/{architecture} manifest")
            manifest, digest = self.manifest(entries[0]["digest"])
        raw, _ = self.get("blobs", manifest["config"]["digest"])
        verify_digest(raw, manifest["config"]["digest"])
        config = json.loads(raw)
        if config.get("os") != "linux" or config.get("architecture") != architecture:
            raise ValueError(f"Base image is not linux/{architecture}")
        return digest, config

    def amd64(self, reference):
        return self.platform_config(reference, "amd64")


def latest_stable_tag(tags):
    stable = [t for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t["name"])]
    if not stable:
        raise ValueError("No stable ImmortalWrt tag")
    return max(stable, key=lambda t: tuple(map(int, t["name"][1:].split("."))))


def stable_release(release):
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Only stable published releases are supported")
    return release


def select_asset(release, pattern, repo):
    assets = [a for a in release["assets"] if re.fullmatch(pattern, a["name"])]
    if len(assets) != 1:
        raise ValueError(f"Expected one official asset matching {pattern}")
    asset = assets[0]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest", "")):
        raise ValueError("Official GitHub asset SHA256 is required")
    expected = f"https://github.com/{repo}/releases/download/{release['tag_name']}/{asset['name']}"
    if urllib.parse.unquote(asset["browser_download_url"]) != expected:
        raise ValueError("Unexpected official asset URL")
    return asset


def rootfs_checksum(checksums, filename):
    matches = []
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            matches.append(parts[0])
    if len(matches) != 1 or not re.fullmatch(r"[a-f0-9]{64}", matches[0]):
        raise ValueError("Expected one official rootfs SHA256")
    return matches[0]


def resolve_arch_inputs(version, release, arch, meta):
    target = meta["immortalwrt_target"]
    base_url = f"https://downloads.immortalwrt.org/releases/{version}/targets/{target}/"
    filename = f"{meta['rootfs_prefix'].format(version=version)}-rootfs.tar.gz"
    sums, _ = request(base_url + "sha256sums")
    rootfs_sha = rootfs_checksum(sums.decode(), filename)

    short_version = ".".join(version.split(".")[:2])
    dependency_feed_url = (
        "https://master.dl.sourceforge.net/project/openwrt-passwall-build/"
        f"releases/packages-{short_version}/{meta['package_arch']}/passwall_packages/packages.adb"
    )
    feed_data, _ = request(dependency_feed_url)

    suffix = meta["handler_suffix"]
    asset_name = f"redirect_pkg_handler-{suffix}-static"
    assets = [a for a in release["assets"] if a["name"] == asset_name]
    if len(assets) != 1:
        raise ValueError(f"Stable release must contain exactly one official static {suffix} handler")
    asset = assets[0]
    checksum = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", checksum):
        raise ValueError("Official GitHub asset SHA256 is required")
    url = asset["browser_download_url"]
    expected = f"https://github.com/ThisSeanZhang/landscape/releases/download/{release['tag_name']}/{asset_name}"
    if url != expected:
        raise ValueError("Unexpected official asset URL")

    return {
        "rootfs_url": base_url + filename,
        "rootfs_sha256": rootfs_sha,
        "dependency_feed_url": dependency_feed_url,
        "dependency_feed_sha256": hashlib.sha256(feed_data).hexdigest(),
        "handler_version": release["tag_name"],
        "handler_url": url,
        "handler_sha256": checksum.removeprefix("sha256:"),
    }


def inputs_digest(state):
    fields = (
        "immortalwrt_version", "immortalwrt_commit",
        "passwall_release", "passwall_version", "passwall_sha256", "passwall_i18n_sha256",
        "dependency_key_sha256", "source_revision"
    )
    base = {key: state[key] for key in fields}
    base["arches"] = {
        arch: {key: value for key, value in state["arches"][arch].items()
               if key not in ("handler_source_url",)}
        for arch in sorted(state["arches"])
    }
    return digest_of(json.dumps(base, sort_keys=True).encode())


def resolve():
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    tags = []
    for page in range(1, 11):
        batch = json_request(IMMORTAL_TAGS_API + f"&page={page}", headers)
        tags.extend(batch)
        if len(batch) < 100:
            break
    base = latest_stable_tag(tags)
    version = base["name"][1:]
    if tuple(map(int, version.split("."))) < (25, 12, 0):
        raise ValueError("PassWall APK requires ImmortalWrt 25.12 or newer")

    pw = stable_release(json_request(PASSWALL_API, headers))
    app = select_asset(pw, r"25\.12\+_luci-app-passwall-[0-9][0-9A-Za-z.+~-]*\.apk",
                       "Openwrt-Passwall/openwrt-passwall")
    i18n = select_asset(pw, r"25\.12\+_luci-i18n-passwall-zh-cn-[0-9][0-9A-Za-z.+~-]*\.apk",
                        "Openwrt-Passwall/openwrt-passwall")
    pw_version = app["name"].removeprefix("25.12+_luci-app-passwall-").removesuffix(".apk")
    translation_version = i18n["name"].removeprefix("25.12+_luci-i18n-passwall-zh-cn-").removesuffix(".apk")
    if re.sub(r"-r\d+$", "", pw_version) != re.sub(r"-r\d+$", "", translation_version):
        raise ValueError("PassWall app and Chinese translation versions disagree")

    release = stable_release(json_request(RELEASE_API, headers))
    tag = release["tag_name"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Unexpected stable version format")

    key_data, _ = request(DEPENDENCY_KEY_URL)
    verify_digest(key_data, "sha256:" + DEPENDENCY_KEY_SHA256)

    # Heartbeat-only commits keep GitHub schedules active but do not change image inputs.
    revision = subprocess.check_output([
        "git", "log", "-1", "--format=%H", "--", "Dockerfile", "start.sh", "rootfs",
        "scripts", "tests", ".github/workflows", "docker-compose.yaml", ".env.example", "README.md",
    ], text=True).strip()
    if not revision:
        raise ValueError("Commit the integration source before resolving inputs")

    state = {
        "immortalwrt_version": version,
        "immortalwrt_commit": base["commit"]["sha"],
        "dependency_key_url": DEPENDENCY_KEY_URL,
        "dependency_key_sha256": DEPENDENCY_KEY_SHA256,
        "passwall_release": pw["tag_name"],
        "passwall_version": pw_version,
        "passwall_url": app["browser_download_url"],
        "passwall_sha256": app["digest"].removeprefix("sha256:"),
        "passwall_i18n_url": i18n["browser_download_url"],
        "passwall_i18n_sha256": i18n["digest"].removeprefix("sha256:"),
        "handler_version": tag,
        "source_revision": revision,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "arches": {},
    }
    for arch, meta in ARCHES.items():
        state["arches"][arch] = resolve_arch_inputs(version, release, arch, meta)
        state["arches"][arch]["handler_source_url"] = (
            f"https://github.com/ThisSeanZhang/landscape/archive/refs/tags/{tag}.tar.gz"
        )

    state["inputs_digest"] = inputs_digest(state)
    return state


def same_inputs(labels, state):
    return labels.get("dev.landscape.inputs.digest") == state["inputs_digest"]


def install_inputs(state, arch):
    """Inputs that determine the package-install layer for one architecture."""
    install = {key: value for key, value in state.items()
               if key.startswith(("passwall_", "dependency_key"))}
    install["dependency_feed_url"] = state["arches"][arch]["dependency_feed_url"]
    install["dependency_feed_sha256"] = state["arches"][arch]["dependency_feed_sha256"]
    return install


def flatten_outputs(state):
    """Flatten nested arch inputs for GitHub Actions build-args and step outputs."""
    outputs = {key: value for key, value in state.items() if key != "arches"}
    for arch, meta in state["arches"].items():
        for key, value in meta.items():
            outputs[f"{key}_{arch}"] = value
    return outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="ghcr.io/otaku-say/landscape-openwrt")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--first-build", action="store_true",
                        help="Explicitly permit an unpublished/private package with no read scope")
    args = parser.parse_args()
    state = resolve()
    build = True
    repository = args.image.removeprefix("ghcr.io/")
    if not args.image.startswith("ghcr.io/"):
        raise ValueError("Output image must be on GHCR")
    try:
        registry = Registry("ghcr.io", repository,
                            os.environ.get("GITHUB_ACTOR"), os.environ.get("GH_TOKEN"))
        _, config = registry.amd64("latest")
        build = args.force or not same_inputs(config.get("config", {}).get("Labels") or {}, state)
    except urllib.error.HTTPError as error:
        if error.code == 404 or (args.first_build and error.code in (401, 403)):
            print("No previous readable image; initializing first publication.")
        else:
            raise

    Path("build").mkdir(exist_ok=True)
    Path("build/upstream.json").write_text(json.dumps(state, indent=2) + "\n")

    # Shared inputs: PassWall APKs are noarch and the signing key is identical.
    Path("build/shared").mkdir(parents=True, exist_ok=True)
    for key, filename in (("passwall", "passwall.apk"),
                          ("passwall_i18n", "passwall-zh.apk"),
                          ("dependency_key", "passwall-build.pem")):
        content, _ = request(state[key + "_url"])
        verify_digest(content, "sha256:" + state[key + "_sha256"])
        Path("build/shared", filename).write_bytes(content)

    for arch in ARCHES:
        arch_dir = Path("build", arch)
        arch_dir.mkdir(parents=True, exist_ok=True)
        meta = state["arches"][arch]
        Path(arch_dir, "install-inputs.json").write_text(
            json.dumps(install_inputs(state, arch), indent=2, sort_keys=True) + "\n")
        for key, filename in (("rootfs", "rootfs.tar.gz"),
                              ("dependency_feed", "packages.adb")):
            content, _ = request(meta[key + "_url"])
            verify_digest(content, "sha256:" + meta[key + "_sha256"])
            Path(arch_dir, filename).write_bytes(content)
        binary, _ = request(meta["handler_url"])
        verify_digest(binary, "sha256:" + meta["handler_sha256"])
        if not binary.startswith(b"\x7fELF\x02\x01"):
            raise ValueError(f"Expected a 64-bit little-endian ELF handler for {arch}")
        Path(arch_dir, "redirect_pkg_handler").write_bytes(binary)
        Path(arch_dir, "redirect_pkg_handler").chmod(0o755)

    outputs = flatten_outputs(state)
    outputs["build"] = str(build).lower()
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key, value in outputs.items():
                if value is not None:
                    output.write(f"{key}={value}\n")
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
