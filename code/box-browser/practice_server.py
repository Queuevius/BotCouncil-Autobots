#!/usr/bin/env python3
"""practice_server.py SITE_DIR OUT_DIR [PORT] - serves a practice job site at http://127.0.0.1:PORT/ (default 8765)
and records what the browser sends: POST /submit -> OUT_DIR/submit-N.json (uploaded files in OUT_DIR/uploads/),
POST /draft (JSON) -> OUT_DIR/draft.json. Part of the browser autobot (BC thread, session BC8)."""
import email
import email.policy
import json
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE, OUT = Path(sys.argv[1]), Path(sys.argv[2])
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 8765
OUT.mkdir(parents=True, exist_ok=True)


def parse(handler):
    n = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(n)
    ctype = handler.headers.get("Content-Type", "")
    fields, files = {}, {}
    if ctype.startswith("multipart/form-data"):
        msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body,
                                       policy=email.policy.HTTP)
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            fname = part.get_filename()
            data = part.get_payload(decode=True) or b""
            if fname is not None:
                if fname:
                    (OUT / "uploads").mkdir(exist_ok=True)
                    (OUT / "uploads" / Path(fname).name).write_bytes(data)
                    files[name] = Path(fname).name
            else:
                fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    elif ctype.startswith("application/json"):
        return json.loads(body or b"{}"), {}
    else:
        for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
            fields[k] = v
    return {k: (v[0] if len(v) == 1 else v) for k, v in fields.items()}, files


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(SITE), **kw)

    def log_message(self, *a):
        pass

    def do_POST(self):
        data, files = parse(self)
        path = self.path.split("?")[0]
        if path == "/draft":
            (OUT / "draft.json").write_text(json.dumps(data, indent=1))
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/submit"):
            n = len(list(OUT.glob("submit-*.json"))) + 1
            (OUT / f"submit-{n}.json").write_text(json.dumps({"path": path, "fields": data, "files": files}, indent=1))
            page = (b"<!doctype html><title>Application received</title><h1>Thank you!</h1>"
                    b"<p>Your application was received. Reference number: PRX-" + str(1000 + n).encode() + b"</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)
            return
        self.send_response(404)
        self.end_headers()


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
