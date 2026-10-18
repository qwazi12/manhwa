"""CONNECT-only loopback proxy: required source/Google providers, never uploads."""
import json
import select
import socket
import socketserver
from guard import allowed_tunnel, PROXY_PORT


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(8192).decode('ascii', 'replace').strip()
        parts = line.split()
        if len(parts) != 3:
            return
        method, authority, _ = parts
        # No URLs, headers, credentials or query strings are logged.
        while True:
            header = self.rfile.readline(8192)
            if header in (b'\r\n', b'\n', b''):
                break
        allowed = method == 'CONNECT' and allowed_tunnel(authority)
        print(json.dumps({'event':'tunnel', 'host':authority.split(':')[0], 'allowed':allowed}), flush=True)
        if not allowed:
            self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
            return
        host = authority.rpartition(':')[0]
        try:
            remote = socket.create_connection((host,443),timeout=30)
        except OSError:
            self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
            return
        with remote:
            self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');self.wfile.flush()
            sockets=[self.connection,remote]
            while True:
                readable,_,_=select.select(sockets,[],[],180)
                if not readable:return
                for source in readable:
                    try:
                        data=source.recv(65536)
                        if not data:return
                        (remote if source is self.connection else self.connection).sendall(data)
                    except OSError:return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True


if __name__=='__main__':
    with Server(('127.0.0.1',PROXY_PORT),Handler) as server:
        print('Provider-only proxy ready',flush=True)
        server.serve_forever()
