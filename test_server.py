#!/usr/bin/env python
import http.server
import socketserver
import os

PORT = int(os.environ.get('PORT', 8000))

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK - Test server is running\n')

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Test server started on port {PORT}")
    httpd.serve_forever()
