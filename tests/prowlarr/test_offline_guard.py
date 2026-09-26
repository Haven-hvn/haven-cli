"""The offline guard itself must block sockets, or the other tests prove nothing."""

import socket

import pytest


def test_socket_connect_is_blocked():
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("192.0.2.1", 9))
    with socket.socket() as sock, pytest.raises(AssertionError, match="network access"):
        sock.connect(("192.0.2.1", 9))
    with pytest.raises(AssertionError, match="network access"):
        socket.getaddrinfo("example.org", 443)
