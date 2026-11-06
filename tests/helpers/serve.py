"""A tiny static HTTP server for the tests: serve.py PORT (or the PORT environment variable).

Why not `python -m http.server`? Its HTTPServer.server_bind() calls socket.getfqdn(), a reverse DNS
lookup that can take tens of seconds on CI hosts, so the port stays closed while the process looks
alive. socketserver.TCPServer binds without that lookup.
"""

import http.server
import os
import socketserver
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ["PORT"])
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler) as server:
    server.serve_forever()
