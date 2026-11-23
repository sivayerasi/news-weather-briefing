"""Run the app locally: python scripts/local_server.py  ->  http://localhost:8000

Uses real Bedrock and Open-Meteo (needs AWS_PROFILE). DynamoDB and SES are
replaced with in-memory/console stand-ins, so nothing is written to AWS.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
try:  # python.org builds of Python on macOS lack root certificates
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda" / "briefing"))
import handler  # noqa: E402
import store  # noqa: E402

SETTINGS, AUDITS = {}, []


def fake_save(email, city, interests, table=None):
    SETTINGS[email] = {"email": email, "city": city, "interests": interests}
    print(f"[local] saved settings: {SETTINGS[email]}")


def fake_audit(user, trigger, city, interests, result, email_status, table=None):
    AUDITS.append(user)
    print(f"[local] audit: user={user or 'anonymous'} trigger={trigger} sources={result['sources']} "
          f"kept={result['items_kept']} gaps={result['gaps']} iterations={result['iterations']}")


store.save_settings = fake_save
store.write_audit = fake_audit


class Local(BaseHTTPRequestHandler):
    def _run(self):
        length = int(self.headers.get("Content-Length") or 0)
        event = {
            "requestContext": {"http": {"method": self.command, "path": self.path.split("?")[0]}},
            "body": self.rfile.read(length).decode() if length else None,
        }
        out = handler.lambda_handler(event, None)
        data = out["body"].encode()
        self.send_response(out["statusCode"])
        self.send_header("Content-Type", out["headers"]["Content-Type"])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _run


if __name__ == "__main__":
    print("Open http://localhost:8000  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", 8000), Local).serve_forever()
