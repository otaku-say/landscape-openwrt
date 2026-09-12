#!/usr/bin/env python3
"""Check actual LuCI root authentication without printing credentials or cookies."""
import http.cookiejar
from html.parser import HTMLParser
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.error
import urllib.request

import paramiko

name = sys.argv[1]
password = os.environ['LAND_ROOT_PASSWORD']
base = f'http://172.30.80.2:{os.environ["LUCI_HTTP_PORT"]}/cgi-bin/luci'


def login(value):
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
    data = urllib.parse.urlencode({'luci_username': 'root', 'luci_password': value}).encode()
    try:
        with opener.open(base + '/', data=data, timeout=15) as response:
            response.read()
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
    return opener, any(c.name.startswith('sysauth') and c.value for c in cookies)


def ssh_login(value, host='172.30.80.2'):
    client = paramiko.SSHClient()
    # An isolated CI container generates its own ephemeral host key.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=int(os.environ['SSH_PORT']), username='root', password=value,
                       look_for_keys=False, allow_agent=False, timeout=10, auth_timeout=10,
                       banner_timeout=10)
        _, stdout, _ = client.exec_command('id -u', timeout=10)
        assert stdout.read().strip() == b'0'
        return True
    except paramiko.AuthenticationException:
        return False
    finally:
        client.close()


assert ssh_login(password), 'Configured root password was not accepted by SSH'
assert ssh_login(password, 'fd70:6c61:6e64:80::2'), 'ULA SSH login failed'
assert not ssh_login(password + '-incorrect'), 'SSH accepted an incorrect password'
if os.environ.get('PREVIOUS_ROOT_PASSWORD'):
    assert not ssh_login(os.environ['PREVIOUS_ROOT_PASSWORD']), 'SSH accepted the old password'
print('PASS: IPv4/ULA SSH root login and wrong/old password rejection at the native port')
opener, accepted = login(password)
assert accepted, 'Configured root password was not accepted by LuCI'
_, wrong = login(password + '-incorrect')
assert not wrong, 'LuCI accepted an incorrect password'
if os.environ.get('PREVIOUS_ROOT_PASSWORD'):
    _, old = login(os.environ['PREVIOUS_ROOT_PASSWORD'])
    assert not old, 'Old root password still accepted after recreation'
class ProxyControls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.enabled = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'input' and values.get('type') == 'checkbox' and 'disabled' not in values:
            self.enabled.add(values.get('name', '').rsplit('.', 1)[-1])
        if 'data-ui-widget' in values:
            widget = json.loads(values['data-ui-widget'])
            if widget[0] == 'Checkbox':
                options = widget[2]
                if not options.get('readonly') and not options.get('disabled'):
                    self.enabled.add(options.get('name', '').rsplit('.', 1)[-1])


with opener.open(base + '/admin/services/passwall', timeout=20) as response:
    html = response.read().decode()
    assert response.status == 200 and ('cbi-passwall' in html or 'cbid.passwall.' in html), 'PassWall configuration form missing'
    assert 'Internal Server Error' not in html
    assert 'Missing components, transparent proxy is unavailable.' not in html
    assert '\u7f3a\u5c11\u7ec4\u4ef6\uff0c\u900f\u660e\u4ee3\u7406\u4e0d\u53ef\u7528' not in html
    controls = ProxyControls()
    controls.feed(html)
    assert {'localhost_proxy', 'client_proxy'} <= controls.enabled, 'Transparent proxy controls are disabled'
subprocess.check_call(['docker', 'exec', name, '/usr/libexec/landscape-proxy-check'])
assert b'landscape_proxy_probe_' not in subprocess.check_output(['docker', 'exec', name, 'nft', 'list', 'tables'])
print('PASS: both PassWall proxy controls enabled through actual kernel capability checks, with no probe rules installed')
raw = subprocess.check_output(['docker', 'exec', name, 'apk', 'query', '--installed', '--format', 'json', '--fields', 'name,version', '*'])
packages = {p['name']: p['version'] for p in json.loads(raw)}
metadata = json.loads(subprocess.check_output(['docker', 'exec', name, 'cat', '/usr/share/landscape-openwrt/upstream.json']))
assert packages['luci-app-passwall'] == metadata['passwall_version']
assert 'luci-i18n-passwall-zh-cn' in packages
for package in ('geoview', 'chinadns-ng', 'xray-core', 'sing-box', 'hysteria', 'haproxy'):
    assert package in packages, package
zone = subprocess.check_output(['docker', 'exec', name, 'uci', 'get', 'system.@system[0].zonename']).decode().strip()
assert zone == os.environ['TZ']
for epoch, expected in ((1767225600, '+0800' if zone == 'Asia/Shanghai' else '+0100'),
                        (1782864000, '+0800' if zone == 'Asia/Shanghai' else '+0200')):
    actual = subprocess.check_output(['docker', 'exec', name, 'date', '-d', '@' + str(epoch), '+%z']).decode().strip()
    assert actual == expected, (zone, epoch, actual, expected)
print('PASS: TZ matches LuCI and winter/summer timezone offsets after recreation')
feeds = subprocess.check_output(['docker', 'exec', name, 'cat', '/etc/apk/repositories.d/distfeeds.list']).decode()
assert 'https://mirrors.ustc.edu.cn/immortalwrt/' in feeds
assert 'https://downloads.immortalwrt.org' not in feeds and 'mirrors.vsean.net' not in feeds
for line in feeds.splitlines():
    if line.strip() and not line.startswith('#'):
        assert line.startswith('https://mirrors.ustc.edu.cn/immortalwrt/'), 'Unexpected system feed'
for path in ('/proc/1/environ', '/etc/config/landscape'):
    content = subprocess.check_output(['docker', 'exec', name, 'cat', path])
    assert b'LAND_ROOT_PASSWORD=' not in content, 'Password environment inherited by services'
    if len(password) >= 12:
        assert password.encode() not in content, 'Password leaked into service environment or UCI'
print('PASS: real LuCI password login, wrong/old password rejection, PassWall page and installed APK versions')
