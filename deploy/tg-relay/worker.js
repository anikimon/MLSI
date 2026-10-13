import { connect } from 'cloudflare:sockets';

// Replace with a long random secret (shared with the local relay's TG_RELAY_TOKEN).
const AUTH = '__AUTH_SECRET__';

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (url.searchParams.get('token') !== AUTH) {
      return new Response('forbidden', { status: 403 });
    }
    const upgrade = (request.headers.get('Upgrade') || '').toLowerCase();
    if (upgrade !== 'websocket') {
      return new Response('requires websocket', { status: 426 });
    }
    const host = url.searchParams.get('host');
    const port = parseInt(url.searchParams.get('port') || '443', 10);
    if (!host || !port) {
      return new Response('bad target', { status: 400 });
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();

    let socket;
    try {
      socket = connect({ hostname: host, port }, { secureTransport: 'off', allowHalfOpen: false });
    } catch (e) {
      server.close(1011, 'connect failed');
      return new Response(null, { status: 101, webSocket: client });
    }

    const writer = socket.writable.getWriter();
    const reader = socket.readable.getReader();

    let closed = false;
    const shutdown = () => {
      if (closed) return;
      closed = true;
      try { server.close(); } catch (e) {}
      try { writer.releaseLock(); } catch (e) {}
      try { socket.close(); } catch (e) {}
    };

    server.addEventListener('message', async (ev) => {
      try {
        const data = ev.data;
        const buf = typeof data === 'string' ? new TextEncoder().encode(data) : new Uint8Array(data);
        await writer.write(buf);
      } catch (e) { shutdown(); }
    });
    server.addEventListener('close', shutdown);
    server.addEventListener('error', shutdown);

    (async () => {
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (value) server.send(value);
        }
      } catch (e) {}
      shutdown();
    })();

    return new Response(null, { status: 101, webSocket: client });
  }
};
