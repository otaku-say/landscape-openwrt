#!/usr/bin/env python3
"""An isolated fake WAN origin: return the observed peer for TCP and UDP."""
import http.server
import socket
import sys
import threading


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content = (self.client_address[0] + '\n').encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class Server(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def udp():
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as stream:
        stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        stream.bind(('::', 18082))
        while True:
            _, peer = stream.recvfrom(2048)
            stream.sendto(peer[0].encode(), peer)


if sys.argv[1] == 'serve':
    threading.Thread(target=udp, daemon=True).start()
    Server(('::', 18081), Handler).serve_forever()
elif sys.argv[1] == 'udp':
    host = sys.argv[2]
    with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.settimeout(8)
        stream.sendto(b'flow-exit-test', (host, 18082))
        print(stream.recvfrom(2048)[0].decode())
else:
    raise ValueError(sys.argv[1])
