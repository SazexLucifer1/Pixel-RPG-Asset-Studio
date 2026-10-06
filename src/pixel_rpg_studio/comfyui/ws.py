"""Minimal WebSocket client (RFC 6455), receive-only, stdlib only.

Used solely for ComfyUI's progress messages on localhost. Keeping it tiny and
dependency-free avoids one more package in the Windows build; if anything
goes wrong the caller falls back to HTTP polling.
"""

from __future__ import annotations

import base64
import os
import socket
import struct
from urllib.parse import urlparse


class WebSocketError(Exception):
    pass


class SimpleWebSocket:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._buffer = b""

    @classmethod
    def connect(cls, url: str, timeout: float = 5.0) -> "SimpleWebSocket":
        parsed = urlparse(url)
        if parsed.scheme != "ws":
            raise WebSocketError("Only plain ws:// URLs are supported (local ComfyUI).")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise WebSocketError("Connection closed during handshake")
            response += chunk
            if len(response) > 65536:
                sock.close()
                raise WebSocketError("Handshake response too large")
        header, rest = response.split(b"\r\n\r\n", 1)
        status_line = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line + b" ":
            sock.close()
            raise WebSocketError(f"Handshake failed: {status_line.decode(errors='replace')}")
        ws = cls(sock)
        ws._buffer = rest
        return ws

    def _read_exact(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("Connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def recv_frame(self) -> tuple[int, bytes]:
        b1, b2 = self._read_exact(2)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return opcode, payload

    def recv_text(self, timeout: float | None = None) -> str | None:
        """Return the next text message, None for non-text frames.

        Raises ``TimeoutError`` if nothing arrives within ``timeout``.
        """
        self.sock.settimeout(timeout)
        try:
            if not self._buffer:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise WebSocketError("Connection closed")
                self._buffer += chunk
            self.sock.settimeout(10.0)
            opcode, payload = self.recv_frame()
        except socket.timeout as exc:
            raise TimeoutError() from exc
        if opcode == 0x8:
            raise WebSocketError("Server closed the connection")
        if opcode == 0x9:  # ping -> pong
            self._send(0xA, payload)
            return None
        if opcode == 0x1:
            return payload.decode("utf-8", errors="replace")
        return None  # binary preview images etc. are ignored

    def _send(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def close(self) -> None:
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
