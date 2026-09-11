import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync('rootfs/www/luci-static/resources/view/ttyd/term.js', 'utf8');
function render(protocol, settings = {}) {
    const window = { location: { protocol, hostname: 'router.test', href: `${protocol}//router.test:8443/` } };
    const view = { extend: value => value };
    const uci = { get_first: (_config, _section, key) => settings[key] };
    const E = (tag, attrs, children) => ({ tag, attrs, children });
    const page = new Function('view', 'uci', 'E', '_', 'window', source)(view, uci, E, s => s, window);
    return page.render();
}
assert.equal(render('http:').children[0].tag, 'iframe');
assert.equal(render('https:').children.length, 1);
assert.equal(render('https:').children[0].children[0].attrs.href, 'http://router.test:7681/');
assert.equal(render('https:', { ssl: '1' }).children[0].attrs.src, 'https://router.test:7681/');
assert.equal(render('https:', { url_override: '/terminal/' }).children[0].attrs.src, 'https://router.test:8443/terminal/');
console.log('PASS: ttyd HTTP embed, HTTPS link, TLS and reverse-proxy overrides');

if (process.argv[2]) {
    const host = process.argv[2];
    const response = await fetch(`http://${host}:7681/token`);
    assert(response.ok, 'ttyd token endpoint');
    const token = await response.json();
    await new Promise((resolve, reject) => {
        const ws = new WebSocket(`ws://${host}:7681/ws`, 'tty');
        const timeout = setTimeout(() => { ws.close(); reject(new Error('No interactive login prompt')); }, 15000);
        let output = '';
        let submitted = false;
        ws.onopen = () => ws.send(JSON.stringify({ AuthToken: token.token || '', columns: 80, rows: 24 }));
        ws.onerror = event => { clearTimeout(timeout); reject(new Error(`ttyd WebSocket failed: ${event.message}`)); };
        ws.onmessage = async event => {
            const data = typeof event.data === 'string' ? event.data : Buffer.from(await event.data.arrayBuffer()).toString();
            if (data[0] !== '0') return;
            output += data.slice(1);
            if (!submitted && output.includes('login:')) {
                submitted = true;
                ws.send('0root\r');
            }
            if (submitted && output.includes('Password:')) {
                clearTimeout(timeout);
                ws.close();
                resolve();
            }
        };
    });
    console.log('PASS: real ttyd WebSocket, PTY login prompt and keyboard input');
}
