"""Additional inherited Python network guard; OS sandbox covers other binaries."""
import os
import socket

if os.environ.get('FLAMINGO_E2E') == '1':
    original_connect=socket.socket.connect
    original_connect_ex=socket.socket.connect_ex
    def permitted(address):
        return isinstance(address,tuple) and address[0] in ('127.0.0.1','localhost','::1') and address[1] in (8149,8138,3138)
    def connect(self,address):
        if not permitted(address):raise PermissionError('E2E permits only its loopback proxy/studio')
        return original_connect(self,address)
    def connect_ex(self,address):
        if not permitted(address):raise PermissionError('E2E permits only its loopback proxy/studio')
        return original_connect_ex(self,address)
    socket.socket.connect=connect
    socket.socket.connect_ex=connect_ex
