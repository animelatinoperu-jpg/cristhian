#!/usr/bin/env python
import http.server
import socketserver
import os
import sys

PORT = int(os.environ.get('PORT', 3000))

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK - Test server is running\n')
        sys.stdout.flush()

    def log_message(self, format, *args):
        msg = format % args
        print(f"[{self.log_date_time_string()}] {msg}")
        sys.stdout.flush()

try:
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Test server listening on 0.0.0.0:{PORT}", flush=True)
        sys.stdout.flush()
        httpd.serve_forever()
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    sys.exit(1)
