#!/usr/bin/env python3
"""backend.py - a tiny back end for page.html.
Each address under /api/ is one function in ROUTES: it takes the JSON the page sent (a dict) and returns a dict,
which goes back to the page as JSON. Run it with: python3 backend.py  (then open http://127.0.0.1:8099/)"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent


def echo(data):
    return {"text": data.get("text", "")}


ROUTES = {
    "/api/echo": echo,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (HERE / "page.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        fn = ROUTES.get(self.path)
        if not fn:
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        out = json.dumps(fn(data)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
