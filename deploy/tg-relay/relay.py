#!/usr/bin/env python3
import asyncio
import ipaddress
import logging
import os
import struct

import websockets

WORKER_URL = os.environ.get("TG_RELAY_WORKER", "")
TOKEN = os.environ.get("TG_RELAY_TOKEN", "")
LISTEN_HOST = os.environ.get("TG_RELAY_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("TG_RELAY_LISTEN_PORT", "1080"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg-relay")


async def _socks5_handshake(reader, writer):
    header = await reader.readexactly(2)
    if header[0] != 0x05:
        return None
    await reader.readexactly(header[1])
    writer.write(b"\x05\x00")
    await writer.drain()

    ver, cmd, _rsv, atyp = await reader.readexactly(4)
    if ver != 0x05 or cmd != 0x01:
        writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
        await writer.drain()
        return None

    if atyp == 0x01:
        host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
    elif atyp == 0x04:
        host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
    elif atyp == 0x03:
        length = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(length)).decode("utf-8", "replace")
    else:
        return None
    port = struct.unpack("!H", await reader.readexactly(2))[0]
    return host, port


async def _pipe_up(reader, ws):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws.send(data)
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


async def _pipe_down(ws, writer):
    try:
        async for message in ws:
            if isinstance(message, str):
                message = message.encode()
            writer.write(message)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _open_worker(url):
    last_error = None
    for attempt in range(3):
        try:
            return await websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=20, open_timeout=30)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last_error


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        target = await _socks5_handshake(reader, writer)
        if not target:
            writer.close()
            return
        host, port = target
        url = f"{WORKER_URL}/?token={TOKEN}&host={host}&port={port}"
        try:
            ws = await _open_worker(url)
        except Exception as exc:
            log.warning("worker connect failed for %s:%s: %s", host, port, exc)
            writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)
            await writer.drain()
            writer.close()
            return
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
        await writer.drain()
        log.info("tunnel %s:%s (client %s)", host, port, peer)
        await asyncio.gather(_pipe_up(reader, ws), _pipe_down(ws, writer))
    except Exception as exc:
        log.warning("handler error: %s", exc, exc_info=True)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    log.info("listening on %s (worker=%s)", addrs, WORKER_URL)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
