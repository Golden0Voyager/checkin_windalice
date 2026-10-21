"""传输层测试：起一个真实的本地 HTTP 服务，不 mock urlopen。

这是唯一会真的走 socket 的测试，用来证明 HTTP 错误码被当正常响应返回、
POST body 真的发出去了。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from checkin_windalice._http import urllib_transport


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server 规定的方法名)
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        received = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(received or b"{}")

    def log_message(self, *args: object) -> None:  # 静音访问日志
        return


@contextmanager
def local_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_get_returns_body_and_status() -> None:
    with local_server() as base:
        response = urllib_transport("GET", f"{base}/ok", {}, None)

    assert response.status == 200
    assert b'"ok":true' in response.body


def test_http_error_is_returned_not_raised() -> None:
    with local_server() as base:
        response = urllib_transport("GET", f"{base}/missing", {}, None)

    assert response.status == 404
    assert response.body == b"not found"


def test_post_body_reaches_server() -> None:
    with local_server() as base:
        response = urllib_transport("POST", f"{base}/echo", {}, b'{"hello":1}')

    assert response.body == b'{"hello":1}'
