#!/usr/bin/env python3
"""Validate dotenv interpolation without printing the rendered credentials."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RFC1918 = tuple(map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
ULA = ipaddress.ip_network('fc00::/7')
PORT_DEFAULTS = {'LUCI_HTTP_PORT': '80', 'LUCI_HTTPS_PORT': '443', 'SSH_PORT': '22'}


def validate_network(config, resolved):
    edge = config['networks']['edge']
    assert edge['enable_ipv6'] is True, 'edge must support IPv6'
    entries = edge['ipam']['config']
    assert len(entries) == 2, 'edge must have exactly one subnet per address family'
    for version in (4, 6):
        prefix = f'EDGE_IPV{version}_'
        subnet = ipaddress.ip_network(resolved[prefix + 'SUBNET'])
        assert subnet.version == version, f'{prefix}SUBNET has the wrong address family'
        ranges = RFC1918 if version == 4 else (ULA,)
        scope = 'RFC1918' if version == 4 else 'ULA'
        assert any(subnet.subnet_of(block) for block in ranges), f'{prefix}SUBNET must be {scope}'
        matching = [entry for entry in entries if ipaddress.ip_network(entry['subnet']).version == version]
        assert len(matching) == 1, f'edge must have one IPv{version} IPAM entry'
        entry = matching[0]
        assert ipaddress.ip_network(entry['subnet']) == subnet, f'{prefix}SUBNET interpolation mismatch'
        gateway = ipaddress.ip_address(resolved[prefix + 'GATEWAY'])
        address = ipaddress.ip_address(resolved[prefix + 'ADDRESS'])
        for value in (gateway, address):
            assert value.version == version and value in subnet, f'IPv{version} endpoint must belong to its subnet'
            assert value != subnet.network_address, f'IPv{version} endpoint must not use the subnet address'
            if version == 4:
                assert value != subnet.broadcast_address, 'IPv4 endpoint must not use the broadcast address'
        assert gateway != address, f'IPv{version} container address must differ from gateway'
        assert ipaddress.ip_address(entry['gateway']) == gateway, f'{prefix}GATEWAY interpolation mismatch'
        actual = config['services']['openwrt']['networks']['edge'][f'ipv{version}_address']
        assert ipaddress.ip_address(actual) == address, f'{prefix}ADDRESS interpolation mismatch'


def validate_config(config, resolved):
    service = config['services']['openwrt']
    for key in ('LAND_ROOT_PASSWORD', 'TZ', 'LAND_DNS_ADDR', 'LAND_REDIRECT_LOG_LEVEL'):
        # Compose escapes dollars when serializing a reusable Compose document.
        assert service['environment'][key] == resolved[key].replace('$', '$$'), f'{key} interpolation mismatch'
    assert 'ports' not in service and 'expose' not in service, 'native listeners must not be published'
    assert 'LAN_BIND_IP' not in service['environment'], 'host address binding must not return'
    for key, default in PORT_DEFAULTS.items():
        assert service['environment'][key] == (resolved.get(key) or default), f'{key} interpolation mismatch'
    assert set(service['networks']) == {'edge'}, 'the Flow exit must use one network'
    edge = config['networks']['edge']
    options = edge['driver_opts']
    for family in ('ipv4', 'ipv6'):
        assert options[f'com.docker.network.bridge.gateway_mode_{family}'] == 'nat-unprotected'
    assert options['com.docker.network.bridge.enable_ip_masquerade'] == 'true', 'outbound NAT must be retained'
    bridge = options['com.docker.network.bridge.name']
    assert bridge == resolved['EDGE_BRIDGE_NAME'], 'bridge name interpolation mismatch'
    assert bridge and len(bridge.encode()) <= 15 and bridge not in ('.', '..'), 'invalid bridge name length'
    assert not re.search(r'[/:\s]', bridge), 'invalid bridge name characters'
    assert edge['name'] == resolved['EDGE_NETWORK_NAME'], 'network name interpolation mismatch'
    mounts = {mount['target']: mount for mount in service['volumes']}
    socket = mounts['/ld_unix_link']
    assert socket['type'] == 'bind' and socket['read_only'] is True
    assert socket['source'] == resolved['LANDSCAPE_SOCKET_DIR'], 'socket path interpolation mismatch'
    assert socket.get('bind', {}).get('create_host_path', False) is False, 'the Landscape socket directory must already exist'
    names = []
    for volume, key, target in (
        ('landscape-openwrt-config', 'CONFIG_VOLUME_NAME', '/etc/config'),
        ('landscape-openwrt-dropbear', 'DROPBEAR_VOLUME_NAME', '/etc/dropbear'),
    ):
        name = config['volumes'][volume]['name']
        assert name and name == resolved[key], f'{key} interpolation mismatch'
        assert mounts[target]['type'] == 'volume' and mounts[target]['source'] == volume
        names.append(name)
    assert len(set(names)) == len(names), 'configuration and SSH identity must use separate volumes'
    validate_network(config, resolved)


def check_case(source, overrides):
    for key, value in overrides.items():
        source, count = re.subn(rf'(?m)^{re.escape(key)}=.*$', lambda _: f"{key}='{value}'", source)
        assert count == 1, f'expected one {key} assignment in the example'
    variables = re.findall(r'^([A-Z][A-Z0-9_]*)=', source, re.M)
    environment = {key: value for key, value in os.environ.items() if key not in variables}
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / '.env'
        file.write_text(source)
        command = ['docker', 'compose', '--env-file', str(file), '-f', str(ROOT / 'docker-compose.yaml'), 'config']
        config = json.loads(subprocess.check_output(command + ['--format', 'json'], env=environment))
        output = subprocess.check_output(command + ['--environment'], env=environment, text=True)
        resolved = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        assert resolved['LAND_ROOT_PASSWORD'] == overrides['LAND_ROOT_PASSWORD'], 'password interpolation mismatch'
        validate_config(config, resolved)


def main():
    source = (ROOT / '.env.example').read_text()
    assert re.search(r"(?m)^LAND_ROOT_PASSWORD='CHANGE_ME_BEFORE_START'$", source), 'keep the example password placeholder'
    for password in ('1', '123', 'simple', 'value$with# spaces'):
        check_case(source, {'LAND_ROOT_PASSWORD': password})
    check_case(source, {'LAND_ROOT_PASSWORD': 'test-only', **dict.fromkeys(PORT_DEFAULTS, '')})
    for index, prefix in enumerate(('10.66.66', '172.30.66', '192.168.66'), 1):
        check_case(source, {
            'LAND_ROOT_PASSWORD': 'test$with# spaces',
            'EDGE_IPV4_SUBNET': f'{prefix}.0/24',
            'EDGE_IPV4_GATEWAY': f'{prefix}.1',
            'EDGE_IPV4_ADDRESS': f'{prefix}.2',
            'EDGE_IPV6_SUBNET': f'fd66:{index}::/64',
            'EDGE_IPV6_GATEWAY': f'fd66:{index}::1',
            'EDGE_IPV6_ADDRESS': f'fd66:{index}::2',
            'EDGE_BRIDGE_NAME': f'br-ci-{index}',
            'EDGE_NETWORK_NAME': f'ci-network-{index}',
            'CONFIG_VOLUME_NAME': f'ci-config-{index}',
            'DROPBEAR_VOLUME_NAME': f'ci-dropbear-{index}',
            'LANDSCAPE_SOCKET_DIR': '/tmp/landscape-ci/unix_link',
            'TZ': 'Etc/UTC',
            'LUCI_HTTP_PORT': '18080',
            'LUCI_HTTPS_PORT': '18443',
            'SSH_PORT': '12222',
        })
    print('PASS: dotenv passwords, configurable dual-stack networks and volumes, native listeners and preserved NAT')


if __name__ == '__main__':
    main()
