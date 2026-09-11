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

BASE = "piaoyizy/openwrt-x86"
RELEASE_API = "https://api.github.com/repos/ThisSeanZhang/landscape/releases/latest"
ASSET_NAME = "redirect_pkg_handler-x86_64-static"
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


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

    def amd64(self, reference):
        manifest, digest = self.manifest(reference)
        if "manifests" in manifest:
            entries = [m for m in manifest["manifests"]
                       if m.get("platform", {}).get("os") == "linux"
                       and m.get("platform", {}).get("architecture") == "amd64"]
            if len(entries) != 1:
                raise ValueError("Expected exactly one linux/amd64 manifest")
            manifest, digest = self.manifest(entries[0]["digest"])
        raw, _ = self.get("blobs", manifest["config"]["digest"])
        verify_digest(raw, manifest["config"]["digest"])
        config = json.loads(raw)
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("Base image is not linux/amd64")
        return digest, config


def resolve():
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    release = json_request(RELEASE_API, headers)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Only stable published releases are supported")
    tag = release["tag_name"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Unexpected stable version format")
    assets = [a for a in release["assets"] if a["name"] == ASSET_NAME]
    if len(assets) != 1:
        raise ValueError("Stable release must contain exactly one official static x86_64 handler")
    asset = assets[0]
    checksum = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", checksum):
        raise ValueError("Official GitHub asset SHA256 is required")
    url = asset["browser_download_url"]
    expected = f"https://github.com/ThisSeanZhang/landscape/releases/download/{tag}/{ASSET_NAME}"
    if url != expected:
        raise ValueError("Unexpected official asset URL")
    base_digest, config = Registry("registry-1.docker.io", BASE).amd64("latest")
    # Heartbeat-only commits keep GitHub schedules active but do not change image inputs.
    revision = subprocess.check_output([
        "git", "log", "-1", "--format=%H", "--", "Dockerfile", "start.sh", "rootfs",
        "scripts", "tests", ".github/workflows", "docker-compose.yaml", "README.md",
    ], text=True).strip()
    if not revision:
        raise ValueError("Commit the integration source before resolving inputs")
    return {
        "base_image": f"docker.io/{BASE}@{base_digest}",
        "base_digest": base_digest,
        "base_created": config.get("created"),
        "handler_version": tag,
        "handler_url": url,
        "handler_source_url": f"https://github.com/ThisSeanZhang/landscape/archive/refs/tags/{tag}.tar.gz",
        "handler_sha256": checksum.removeprefix("sha256:"),
        "source_revision": revision,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }


def same_inputs(labels, state):
    expected = {
        "org.opencontainers.image.base.digest": state["base_digest"],
        "dev.landscape.handler.version": state["handler_version"],
        "dev.landscape.handler.sha256": state["handler_sha256"],
        "org.opencontainers.image.revision": state["source_revision"],
    }
    return all(labels.get(key) == value for key, value in expected.items())


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
    outputs = {**state, "build": str(build).lower()}
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key, value in outputs.items():
                if value is not None:
                    output.write(f"{key}={value}\n")
    print(json.dumps(outputs, indent=2))
    if build:
        binary, _ = request(state["handler_url"])
        verify_digest(binary, "sha256:" + state["handler_sha256"])
        if not binary.startswith(b"\x7fELF\x02\x01"):
            raise ValueError("Expected a 64-bit little-endian ELF handler")
        Path("build/redirect_pkg_handler").write_bytes(binary)
        Path("build/redirect_pkg_handler").chmod(0o755)


if __name__ == "__main__":
    main()
