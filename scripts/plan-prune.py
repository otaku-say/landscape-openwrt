#!/usr/bin/env python3
"""Plan removals from the actual opkg dependency graph, retaining shared cores."""
import argparse
from email.parser import Parser
import json
from pathlib import Path
import re

REMOVED_APPS = {"homeproxy", "openclash", "momo"}
# PassWall discovers these at runtime; they are not all declared in Depends.
KEEP_OPTIONAL = {"sing-box", "xray-core", "shadowsocks-rust-sslocal",
                 "shadowsocks-libev-ss-local", "v2ray-plugin", "geoview"}
KEEP_SYSTEM = {"firewall4", "dnsmasq-full", "ip-full", "bash", "unzip",
               "kmod-nft-tproxy", "kmod-nft-socket", "ca-bundle", "luci-app-passwall"}


def read_packages(directory):
    result = {}
    for control in sorted(directory.glob("*.control")):
        fields = dict(Parser().parsestr(control.read_text()).items())
        result[fields["Package"]] = fields
    return result


def dependency_graph(packages):
    providers = {}
    for name, fields in packages.items():
        for provided in fields.get("Provides", "").split(","):
            provided = provided.strip().split(" ")[0]
            if provided:
                providers.setdefault(provided, set()).add(name)
    graph = {}
    for name, fields in packages.items():
        deps = set()
        for group in fields.get("Depends", "").split(","):
            choices = [re.sub(r"\s*\([^)]*\)", "", s).strip() for s in group.split("|")]
            matches = set()
            for choice in choices:
                if choice in packages:
                    matches.add(choice)
                else:
                    matches.update(providers.get(choice, ()))
            if group.strip() and not matches:
                raise ValueError(f"Unresolved installed dependency: {name}: {group}")
            deps.update(matches)
        graph[name] = deps
    return graph


def closure(roots, graph):
    seen = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(graph[name] - seen)
    return seen


def make_plan(packages):
    if "luci-app-passwall" not in packages:
        raise ValueError("Upstream no longer contains PassWall")
    graph = dependency_graph(packages)
    roots = {name for name in packages if name in REMOVED_APPS or name == "mihomo"
             or any(name == f"luci-app-{app}" or name.startswith(f"luci-i18n-{app}-")
                    for app in REMOVED_APPS)}
    candidates = closure(roots, graph)
    retained = closure((set(packages) - candidates) |
                       ((KEEP_OPTIONAL | KEEP_SYSTEM) & set(packages)), graph)
    if roots & retained:
        raise ValueError(f"A retained package needs a removed app: {sorted(roots & retained)}")
    pending = candidates - retained
    order = []
    while pending:
        required = set().union(*(graph[name] & pending for name in pending))
        leaves = sorted(pending - required)
        if not leaves:
            raise ValueError(f"Removal dependency cycle: {sorted(pending)}")
        order.extend(leaves)
        pending.difference_update(leaves)
    return {"removed": order, "retained_packages": len(retained),
            "retained_optional_cores": sorted(KEEP_OPTIONAL & retained),
            "passwall_version": packages["luci-app-passwall"]["Version"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--opkg-info", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = make_plan(read_packages(args.opkg_info))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "prune-manifest.json").write_text(json.dumps(plan, indent=2) + "\n")
    (args.output / "remove-packages.txt").write_text("\n".join(plan["removed"]) + "\n")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
