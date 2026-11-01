from __future__ import annotations

import os
import socket

import pytest


def test_model_roles_point_at_a_closed_loopback_port():
    for role in ("CHAT", "VISION", "STT"):
        base_url = os.environ[f"{role}_BASE_URL"]
        assert base_url.startswith(("http://127.0.0.1:", "http://localhost:")), base_url
        assert base_url.endswith(":9/v1")


def test_external_sockets_are_refused():
    with socket.socket() as sock:
        with pytest.raises(RuntimeError, match="Outbound network is blocked"):
            sock.connect(("1.1.1.1", 443))


def test_external_dns_lookups_are_refused():
    with pytest.raises(RuntimeError, match="DNS lookup is blocked"):
        socket.getaddrinfo("api.example.com", 443)


def test_loopback_is_still_allowed():
    with socket.socket() as sock:
        with pytest.raises(OSError) as excinfo:
            sock.connect(("127.0.0.1", 9))
    assert not isinstance(excinfo.value, RuntimeError)
