#!/usr/bin/env python3
"""Check actual LuCI root authentication without printing credentials or cookies."""
import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.error
import urllib.request

name = sys.argv[1]
password = os.environ['LAND_ROOT_PASSWORD']
base = 'http://172.30.80.2/cgi-bin/luci'


def login(value):
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    data = urllib.parse.urlencode({'luci_username': 'root', 'luci_password': value}).encode()
    try:
        with opener.open(base + '/', data=data, timeout=15) as response:
            response.read()
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
    return opener, any(c.name.startswith('sysauth') and c.value for c in cookies)


opener, accepted = login(password)
assert accepted, 'Configured root password was not accepted by LuCI'
_, wrong = login(password + '-incorrect')
assert not wrong, 'LuCI accepted an incorrect password'
if os.environ.get('PREVIOUS_ROOT_PASSWORD'):
    _, old = login(os.environ['PREVIOUS_ROOT_PASSWORD'])
    assert not old, 'Old root password still accepted after recreation'
with opener.open(base + '/admin/services/passwall', timeout=20) as response:
    html = response.read().decode()
    assert response.status == 200 and 'PassWall' in html and 'Internal Server Error' not in html
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
    assert password.encode() not in content, 'Password leaked into service environment or UCI'
print('PASS: real LuCI password login, wrong/old password rejection, PassWall page and installed APK versions')
