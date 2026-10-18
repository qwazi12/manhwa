"""Inherited by Python staging workers: external network is unavailable.

This is an application guard, not an OS/container network sandbox. Never place
provider or publishing credentials in an offline staging snapshot.
"""
import ipaddress
import os
import socket


def local(address):
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    host = address[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if os.environ.get("FLAMINGO_STAGING") == "1":
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection

    def connect(self, address):
        if not local(address):
            raise PermissionError("Offline staging blocks external network connections")
        return original_connect(self, address)

    def connect_ex(self, address):
        if not local(address):
            raise PermissionError("Offline staging blocks external network connections")
        return original_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        if not local(address):
            raise PermissionError("Offline staging blocks external network connections")
        return original_create(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
