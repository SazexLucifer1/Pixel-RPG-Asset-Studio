import socket
import struct
import threading

from pixel_rpg_studio.comfyui.ws import SimpleWebSocket


def _server(messages):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        conn, _ = srv.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(1024)
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        for m in messages:
            payload = m.encode()
            if len(payload) < 126:
                header = bytes([0x81, len(payload)])
            else:
                header = bytes([0x81, 126]) + struct.pack(">H", len(payload))
            conn.sendall(header + payload)
        conn.sendall(bytes([0x88, 0]))
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def test_receive_text_frames():
    long_msg = '{"type": "progress", "data": {"value": 3, "max": 20, "x": "' + "a" * 300 + '"}}'
    port = _server(['{"type": "status"}', long_msg])
    ws = SimpleWebSocket.connect(f"ws://127.0.0.1:{port}/ws?clientId=abc", timeout=3)
    assert ws.recv_text(timeout=3) == '{"type": "status"}'
    assert ws.recv_text(timeout=3) == long_msg
    ws.close()
