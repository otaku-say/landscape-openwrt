#!/usr/bin/env python3
"""Probe native management sockets from the host or an isolated routed LAN client."""
import socket
import ssl
import sys
import urllib.request

hosts = sys.argv[1].split(',')
http, https, ssh = map(int, sys.argv[2:5])
# The test candidate generates a local self-signed certificate.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
for host in hosts:
    authority = f'[{host}]' if ':' in host else host
    for scheme, port in (('http', http), ('https', https)):
        with opener.open(f'{scheme}://{authority}:{port}/', timeout=10) as response:
            assert response.status == 200
            response.read()
    with socket.create_connection((host, ssh), timeout=10) as stream:
        assert stream.recv(256).startswith(b'SSH-2.0-'), 'Missing SSH protocol banner'
    for port in {22, 80, 443, 8000, 8443, 2222} - {http, https, ssh}:
        try:
            with socket.create_connection((host, port), timeout=2):
                pass
        except OSError:
            continue
        raise AssertionError(f'Unexpected listener at {host}:{port}')
    print(f'PASS: {host} native HTTP/HTTPS/SSH {http}/{https}/{ssh}; unused defaults closed', flush=True)
