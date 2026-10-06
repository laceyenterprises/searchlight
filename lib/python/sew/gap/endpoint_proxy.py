"""Parent-owned transport to pre-resolved harness endpoints only."""

from __future__ import annotations

import select
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn, UnixStreamServer
from urllib.parse import urlsplit


@contextmanager
def endpoint_proxy(endpoints, *, socket_path=None):
    # Freeze both the authority allowlist and DNS answers for this cell.
    endpoints = {key: tuple(records) for key, records in endpoints.items()}
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        rbufsize = 0  # Body/tunnel bytes must remain available to the relay.

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # URLs and headers may contain credentials.

        def relay(self, tunnel=False):
            try:
                target = urlsplit("//" + self.path if tunnel else self.path)
                if target.username or target.password:
                    raise ValueError("credentials in authority")
                port = target.port if tunnel else target.port or 80
                if not tunnel and target.scheme != "http":
                    raise ValueError("unsupported proxy scheme")
                records = endpoints.get((target.hostname, port))
                if not records:
                    self.send_error(403, "Permission denied: endpoint not allowlisted")
                    return
            except ValueError:
                self.send_error(400, "Invalid endpoint")
                return
            upstream = None
            for family, kind, protocol, _, address in records:
                candidate = None
                try:
                    candidate = socket.socket(family, kind, protocol)
                    candidate.settimeout(10)
                    candidate.connect(address)
                except OSError:
                    if candidate is not None:
                        candidate.close()
                    continue
                upstream = candidate
                break
            if upstream is None:
                self.send_error(502, "Endpoint unavailable")
                return
            self.close_connection = True
            with upstream:
                try:
                    if tunnel:
                        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    else:
                        path = target.path or "/"
                        if target.query:
                            path += "?" + target.query
                        head = f"{self.command} {path} HTTP/1.1\r\n"
                        head += "".join(
                            f"{key}: {value}\r\n"
                            for key, value in self.headers.items()
                            if key.lower()
                            not in {"proxy-authorization", "proxy-connection", "connection"}
                        )
                        upstream.sendall((head + "Connection: close\r\n\r\n").encode("latin-1"))
                    while not stopped.is_set():
                        ready, _, _ = select.select([self.connection, upstream], [], [], 0.2)
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            destination = upstream if source is self.connection else self.connection
                            destination.sendall(data)
                except OSError:
                    return

        def do_CONNECT(self):
            self.relay(tunnel=True)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = relay

    class UnixServer(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True

    server = (
        UnixServer(str(socket_path), Handler)
        if socket_path is not None
        else ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    )
    with server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                str(socket_path)
                if socket_path is not None
                else f"http://127.0.0.1:{server.server_port}"
            )
        finally:
            stopped.set()
            server.shutdown()
            thread.join()
