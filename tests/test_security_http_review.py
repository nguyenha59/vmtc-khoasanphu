"""Bounded HTTP framing probes against a temporary loopback Uvicorn server."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from test_security_audit_review import review as review


@pytest.fixture()
def local_http(review):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(review.client.app, host="127.0.0.1", port=port,
                                          lifespan="off", access_log=False, log_level="critical"))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        yield port, review
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        listener.close()


def exchange(port, payload):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(payload)
        return connection.recv(8192)


def test_http_parser_rejects_selected_ambiguous_framing(local_http):
    port, _ = local_http
    headers = [b"Content-Length: 2\r\nContent-Length: 3", b"Content-Length: -1",
               b"Transfer-Encoding: gzip"]
    for framing in headers:
        response = exchange(port, b"POST /api/auth/login HTTP/1.1\r\nHost: localhost\r\n"
                             + framing + b"\r\nConnection: close\r\n\r\n{}")
        assert response.startswith((b"HTTP/1.1 400", b"HTTP/1.1 501")), response.split(b"\r\n", 1)[0]


def test_public_body_limit_is_enforced_over_real_http(local_http):
    port, review = local_http
    body = b" " * 65_537
    request = (f"POST /api/public/form/{review.normal} HTTP/1.1\r\nHost: localhost\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
    response = exchange(port, request + body)
    assert response.startswith(b"HTTP/1.1 413")
