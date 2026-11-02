"""The Discord gateway: hears when someone joins a club's server.

Everything else the bot does needs no open connection (HTTP interactions and
REST). Only "someone joined the server" comes solely over the gateway, with
the privileged Server Members intent. This process keeps that one connection
and turns each join into a Celery task (``tasks.member_joined``); the work
(welcome message, roles) happens in the worker like everything else.

Run it once per platform: ``flask modules run discord-gateway`` (a separate
container from the backend image; docker-compose profile ``discord``, k3s
Deployment ``discord-gateway``). It waits while the bot isn't set up, and
leaves (so the container restarts) when Discord refuses the token or the
intent, after a pause so it doesn't hammer Discord.

The WebSocket client is a small stdlib one (RFC 6455, text frames, no
compression), enough for the gateway's JSON.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import socket
import ssl
import struct
import threading
import time
import urllib.parse

_log = logging.getLogger("hubra_discord.gateway")

GUILD_MEMBERS = 1 << 1
# Close codes after which trying again with the same settings is pointless.
FATAL = {
    4004: "the bot token was refused",
    4010: "invalid shard",
    4011: "sharding required",
    4012: "invalid API version",
    4013: "invalid intents",
    4014: "the Server Members intent is off (Developer Portal › Bot › Privileged Gateway Intents)",
}
IDLE_WAIT = 600  # seconds, while not set up or after a fatal close


class Closed(Exception):
    def __init__(self, code: int | None, reason: str = ""):
        super().__init__(f"closed {code} {reason}".strip())
        self.code = code


# ── WebSocket ────────────────────────────────────────────────────────────────


def encode_frame(payload: bytes, opcode: int = 1, mask: bytes | None = None) -> bytes:
    """A final, masked client frame."""
    mask = mask if mask is not None else os.urandom(4)
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked


def read_frame(read) -> tuple[bool, int, bytes]:
    """(final, opcode, payload) from ``read(n)``; server frames are unmasked."""
    b1, b2 = read(2)
    final, opcode = bool(b1 & 0x80), b1 & 0x0F
    n = b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", read(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", read(8))
    mask = read(4) if b2 & 0x80 else None
    data = read(n) if n else b""
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return final, opcode, data


class WebSocket:
    def __init__(self, url: str, timeout: float = 60):
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port or 443
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        self.lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\nUser-Agent: hubra\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise Closed(None, "no handshake")
            head += chunk
        status, _, rest = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise Closed(None, status.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buffer = rest

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Closed(None, "connection lost")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send(self, text: str, opcode: int = 1) -> None:
        with self.lock:
            self.sock.sendall(encode_frame(text.encode(), opcode))

    def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            final, opcode, data = read_frame(self._read)
            if opcode == 8:
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
                raise Closed(code, data[2:].decode(errors="replace"))
            if opcode == 9:
                with self.lock:
                    self.sock.sendall(encode_frame(data, 10))
                continue
            if opcode == 10:
                continue
            parts.append(data)
            if final:
                return b"".join(parts).decode()

    def close(self, code: int = 1000) -> None:
        try:
            with self.lock:
                self.sock.sendall(encode_frame(struct.pack("!H", code), 8))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ── Gateway session ──────────────────────────────────────────────────────────


def on_dispatch(event: str, data: dict) -> None:
    """Hand the events Hubra cares about to the worker."""
    if event != "GUILD_MEMBER_ADD":
        return
    user = data.get("user") or {}
    if user.get("bot") or not user.get("id") or not data.get("guild_id"):
        return
    from .tasks import member_joined

    member_joined.delay(str(data["guild_id"]), str(user["id"]))


class Session:
    def __init__(self, token: str):
        self.token = token
        self.seq: int | None = None
        self.session_id: str | None = None
        self.resume_url: str | None = None
        self.acked = True
        self.stop = threading.Event()

    def _heartbeat(self, ws: WebSocket, interval: float) -> None:
        # The first beat after a random part of the interval, as Discord asks.
        wait = interval * random.random()  # noqa: S311
        while not self.stop.wait(wait):
            if not self.acked:
                _log.warning("discord gateway: no heartbeat ack, reconnecting")
                ws.close(4000)
                return
            self.acked = False
            try:
                ws.send(json.dumps({"op": 1, "d": self.seq}))
            except OSError:
                return
            wait = interval

    def run_once(self, url: str) -> None:
        """One connection, until it closes (raises ``Closed``)."""
        ws = WebSocket(f"{url}/?v=10&encoding=json")
        self.stop.clear()
        try:
            hello = json.loads(ws.recv())
            interval = hello["d"]["heartbeat_interval"] / 1000
            ws.sock.settimeout(interval * 2)
            self.acked = True
            threading.Thread(
                target=self._heartbeat, args=(ws, interval), daemon=True
            ).start()
            if self.session_id and self.seq is not None:
                ws.send(
                    json.dumps(
                        {
                            "op": 6,
                            "d": {
                                "token": self.token,
                                "session_id": self.session_id,
                                "seq": self.seq,
                            },
                        }
                    )
                )
            else:
                self.identify(ws)
            while True:
                msg = json.loads(ws.recv())
                self.handle(ws, msg)
        finally:
            self.stop.set()
            ws.close()

    def identify(self, ws: WebSocket) -> None:
        ws.send(
            json.dumps(
                {
                    "op": 2,
                    "d": {
                        "token": self.token,
                        "intents": GUILD_MEMBERS,
                        "properties": {
                            "os": "linux",
                            "browser": "hubra",
                            "device": "hubra",
                        },
                    },
                }
            )
        )

    def handle(self, ws: WebSocket, msg: dict) -> None:
        op = msg.get("op")
        if msg.get("s") is not None:
            self.seq = msg["s"]
        if op == 0:
            event, data = msg.get("t"), msg.get("d") or {}
            if event == "READY":
                self.session_id = data.get("session_id")
                self.resume_url = data.get("resume_gateway_url")
                _log.info(
                    "discord gateway: ready on %s servers",
                    len(data.get("guilds") or []),
                )
            try:
                on_dispatch(event, data)
            except Exception:
                _log.exception("discord gateway: handling %s failed", event)
        elif op == 11:
            self.acked = True
        elif op == 1:
            ws.send(json.dumps({"op": 1, "d": self.seq}))
        elif op == 7:
            raise Closed(4000, "Discord asked to reconnect")
        elif op == 9:
            if not msg.get("d"):  # not resumable: start over
                self.session_id, self.seq = None, None
            time.sleep(1 + 4 * random.random())  # noqa: S311
            raise Closed(4000, "invalid session")


def _settings() -> str:
    from . import api

    return api.config("bot_token") if not api.missing_settings() else ""


def run() -> None:
    """Entry point of ``flask modules run discord-gateway``."""
    from . import api

    logging.basicConfig(level=logging.INFO)
    token = _settings()
    if not token:
        _log.info("discord gateway: Discord isn't set up; waiting")
        time.sleep(IDLE_WAIT)
        return
    session = Session(token)
    backoff = 1.0
    while True:
        try:
            url = session.resume_url if session.session_id else api.gateway_url()
            session.run_once(url)
        except Closed as exc:
            if exc.code in FATAL:
                _log.error("discord gateway: %s; waiting", FATAL[exc.code])
                time.sleep(IDLE_WAIT)
                return
            if exc.code in (4007, 4009):  # bad seq, session timed out
                session.session_id, session.seq = None, None
            _log.info("discord gateway: %s; reconnecting", exc)
        except (OSError, ValueError, KeyError, api.DiscordError) as exc:
            _log.warning("discord gateway: %s; reconnecting in %.0fs", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        backoff = 1.0
        time.sleep(1)
