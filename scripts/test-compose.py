#!/usr/bin/env python3
"""Validate dotenv interpolation without printing the rendered credentials."""
import json
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import tempfile

source = Path('.env.example').read_text()
variables = re.findall(r'^([A-Z][A-Z0-9_]*)=', source, re.M)
environment = {key: value for key, value in os.environ.items() if key not in variables}
for password in ('1', '123', 'simple', 'value$with# spaces'):
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / '.env'
        file.write_text(source.replace("'CHANGE_ME_BEFORE_START'", "'" + password + "'"))
        output = subprocess.check_output(['docker', 'compose', '--env-file', str(file),
                                          '-f', 'docker-compose.yaml', 'config', '--format', 'json'],
                                         env=environment)
        config = json.loads(output)
        resolved = subprocess.check_output(['docker', 'compose', '--env-file', str(file),
                                            '-f', 'docker-compose.yaml', 'config', '--environment'],
                                           env=environment, text=True)
        assert ('LAND_ROOT_PASSWORD=' + password) in resolved.splitlines()
        service = config['services']['openwrt']
        # `compose config` escapes dollars for a reusable Compose document.
        # This is serialization, not a second interpolation of the dotenv value.
        assert service['environment']['LAND_ROOT_PASSWORD'] == password.replace('$', '$$')
        assert service['environment']['TZ'] == 'Asia/Shanghai'
        assert 'ports' not in service and 'expose' not in service
        assert 'LAN_BIND_IP' not in service['environment']
        for key, value in [('LUCI_HTTP_PORT', '80'), ('LUCI_HTTPS_PORT', '443'), ('SSH_PORT', '22')]:
            assert service['environment'][key] == value
        for family in ('ipv4', 'ipv6'):
            assert config['networks']['edge']['driver_opts'][f'com.docker.network.bridge.gateway_mode_{family}'] == 'nat-unprotected'
        assert service['volumes'][0]['source'] == '/root/.lkit/landscape/data/unix_link'
        bridge = config['networks']['edge']['driver_opts']['com.docker.network.bridge.name']
        assert bridge == 'br-openwrt' and len(bridge) <= 15
        assert config['networks']['edge']['name'] == 'landscape-openwrt'
        assert service['networks']['edge']['ipv4_address'] == '172.30.66.2'
        subnet = ipaddress.ip_network(config['networks']['edge']['ipam']['config'][0]['subnet'])
        assert subnet.subnet_of(ipaddress.ip_network('172.16.0.0/12'))
        assert config['volumes']['landscape-openwrt-config']['name'] == 'landscape-openwrt-direct-config'
        assert config['volumes']['landscape-openwrt-dropbear']['name'] == 'landscape-openwrt-direct-dropbear'
print('PASS: dotenv passwords, native listeners, no host publishing, dual-stack direct routing and fresh volumes')
