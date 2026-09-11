#!/usr/bin/env python3
"""A disposable enrollment socket and HTTP target for isolated CI tests."""
import http.server
import json
import signal
import socket
import sys
import threading
from pathlib import Path

root = Path(sys.argv[1])
listener = socket.socket(socket.AF_UNIX)
listener.bind(str(root / "register.sock"))
listener.listen(8)
(root / "register.sock").chmod(0o777)


def enroll():
    while True:
        conn, _ = listener.accept()
        with conn:
            chunks = []
            while data := conn.recv(65536):
                chunks.append(data)
            value = json.loads(b"".join(chunks))
            (root / "enrollment.json").write_text(json.dumps(value))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content = (self.client_address[0] + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class Server(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


threading.Thread(target=enroll, daemon=True).start()
server = Server(("::", 18081), Handler)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
server.serve_forever()
