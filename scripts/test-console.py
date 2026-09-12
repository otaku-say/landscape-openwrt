#!/usr/bin/env python3
"""Use disposable PTYs to detect container writes or ioctl changes to host consoles."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import termios
import tty


def serve(root):
    pairs = [os.openpty() for _ in range(2)]
    for master, slave in pairs:
        tty.setraw(slave)
        os.set_blocking(master, False)
        # Prove this fixture can observe bytes before measuring the container.
        os.write(slave, b'console-fixture-check')
        assert select.select([master], [], [], 2)[0]
        assert os.read(master, 4096) == b'console-fixture-check'
    attributes = [termios.tcgetattr(slave) for _, slave in pairs]
    received = [0, 0]
    listener = socket.socket(socket.AF_UNIX)
    path = root / 'console-probe.sock'
    listener.bind(str(path))
    listener.listen(4)
    (root / 'console-cmdline').write_text('console=ttyS0,115200n8\n')
    (root / 'console-devices.json').write_text(json.dumps([os.ttyname(slave) for _, slave in pairs]))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            readable, _, _ = select.select([listener, *(master for master, _ in pairs)], [], [], 0.2)
            for index, (master, _) in enumerate(pairs):
                if master in readable:
                    received[index] += len(os.read(master, 65536))
            if listener in readable:
                client, _ = listener.accept()
                with client:
                    status = {'received_bytes': received, 'termios_unchanged': [
                        termios.tcgetattr(slave) == expected
                        for (_, slave), expected in zip(pairs, attributes)
                    ]}
                    client.sendall(json.dumps(status).encode())
    finally:
        listener.close()
        path.unlink(missing_ok=True)
        for master, slave in pairs:
            os.close(master)
            os.close(slave)


def check(root, name=None):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(root / 'console-probe.sock'))
        chunks = []
        while chunk := client.recv(4096):
            chunks.append(chunk)
    status = json.loads(b''.join(chunks))
    assert status['received_bytes'] == [0, 0], status
    assert all(status['termios_unchanged']), status
    if name:
        def inside(*args):
            return subprocess.check_output(['docker', 'exec', name, *args], text=True).strip()
        inside('/usr/libexec/landscape-console', '--check')
        assert inside('cat', '/etc/inittab').splitlines() == [
            '::sysinit:/etc/init.d/rcS S boot', '::shutdown:/etc/init.d/rcS K shutdown',
        ]
        assert inside('cat', '/proc/1/comm') == 'procd'
        top = subprocess.check_output(['docker', 'top', name, '-eo', 'pid,ppid,tty,comm'], text=True)
        for line in top.splitlines()[1:]:
            _, _, terminal, command = line.split()
            assert command not in ('askfirst', 'agetty', 'getty', 'login'), line
            assert not terminal.startswith(('ttyS', 'tty1', 'hvc')), line
        # Interactive management must retain its own devpts terminal.
        terminal = subprocess.check_output(['docker', 'exec', '-t', name, 'tty'], text=True).strip()
        assert terminal.startswith('/dev/pts/'), terminal
        inside('logger', '-t', 'console-smoke', 'container syslog remains available')
        assert 'container syslog remains available' in inside('logread')
    print('PASS: host serial/VT PTYs receive no data or termios changes' +
          ('; procd, container PTY and syslog remain available' if name else ' after container shutdown'), flush=True)


if __name__ == '__main__':
    action, directory = sys.argv[1:3]
    if action == 'serve':
        serve(Path(directory))
    elif action == 'check':
        check(Path(directory), sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        raise SystemExit('Usage: test-console.py serve|check directory [container]')
