#!/usr/bin/env python3
"""Validate dotenv interpolation without printing the rendered credentials."""
import json
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
        service = config['services']['openwrt']
        assert service['environment']['LAND_ROOT_PASSWORD'] == password
        assert service['environment']['TZ'] == 'Asia/Shanghai'
        assert service['ports'][0]['published'] == '8000'
        assert service['volumes'][0]['source'] == '/root/.lkit/landscape/data/unix_link'
        bridge = config['networks']['edge']['driver_opts']['com.docker.network.bridge.name']
        assert bridge == 'landscape-owrt' and len(bridge) <= 15
        assert config['networks']['edge']['name'] == 'landscape-openwrt'
        assert service['networks']['edge']['ipv4_address'] == '172.66.66.2'
        assert config['volumes']['landscape-openwrt-config']['name'] == 'landscape-openwrt-config'
print('PASS: dotenv short/numeric/special passwords, timezone, ports, socket, network and new volume names')
