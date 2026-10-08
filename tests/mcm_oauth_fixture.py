"""Loopback-only OpenShift OAuth fixture for optional real-oc tests.

All identities, authorization codes and tokens below are synthetic. This is not
an OpenShift server: it implements only endpoints exercised by CLI login/whoami.
"""
from __future__ import annotations

import base64
import http.server
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

USERNAME = "fixture-user"
PASSWORD = "  synthetic whitespace password  "
TOKEN = "sha256~synthetic-token-not-a-real-credential"


class OAuthFixture:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcm-oauth-")
        self.root = Path(self.temp.name)
        self.requests = []
        self.password_matches = []
        self.cert = self.root / "cert.pem"
        self.key = self.root / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-days", "1", "-keyout", str(self.key), "-out", str(self.cert),
             "-subj", "/CN=localhost", "-addext",
             "subjectAltName=DNS:localhost,IP:127.0.0.1"],
            check=True, capture_output=True, timeout=15,
        )
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Never log Authorization headers or fixture credentials.
                pass

            def respond(self, code, data, headers=None):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def failure(self, code, reason):
                self.respond(code, {"kind": "Status", "apiVersion": "v1",
                                    "status": "Failure", "reason": reason,
                                    "code": code})

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.do_GET()

            def do_GET(self):
                path = urlsplit(self.path).path
                fixture.requests.append((self.command, path))
                if path == "/version":
                    return self.respond(200, {"major": "1", "minor": "31",
                                              "gitVersion": "v1.31.0"})
                if path == "/.well-known/oauth-authorization-server":
                    return self.respond(200, {
                        "issuer": fixture.url,
                        "authorization_endpoint": fixture.url + "/oauth/authorize",
                        "token_endpoint": fixture.url + "/oauth/token",
                        "response_types_supported": ["code", "token"],
                        "grant_types_supported": ["authorization_code", "implicit"],
                    })
                if path == "/oauth/authorize":
                    expected = "Basic " + base64.b64encode(
                        (USERNAME + ":" + PASSWORD).encode()
                    ).decode()
                    supplied = self.headers.get("Authorization")
                    if supplied:
                        fixture.password_matches.append(supplied == expected)
                    if supplied == expected:
                        return self.respond(302, {}, {
                            "Location": fixture.url +
                            "/oauth/token/implicit?code=synthetic-code",
                        })
                    return self.respond(401, {
                        "kind": "Status", "apiVersion": "v1", "status": "Failure",
                        "reason": "Unauthorized", "code": 401,
                    }, {"WWW-Authenticate": 'Basic realm="fixture"'})
                if path == "/oauth/token":
                    return self.respond(200, {"access_token": TOKEN,
                                              "token_type": "Bearer",
                                              "expires_in": 3600})
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return self.failure(401, "Unauthorized")
                if path == "/apis/authentication.k8s.io/v1/selfsubjectreviews":
                    return self.respond(201, {
                        "kind": "SelfSubjectReview", "apiVersion": "authentication.k8s.io/v1",
                        "status": {"userInfo": {"username": USERNAME}},
                    })
                if path == "/apis/user.openshift.io/v1/users/~":
                    return self.respond(200, {"kind": "User",
                                              "apiVersion": "user.openshift.io/v1",
                                              "metadata": {"name": USERNAME}})
                if path == "/apis/project.openshift.io/v1/projects":
                    return self.respond(200, {"kind": "ProjectList",
                                              "apiVersion": "project.openshift.io/v1",
                                              "items": [{"metadata": {"name": "default"}}]})
                return self.failure(404, "NotFound")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "https://127.0.0.1:" + str(self.server.server_port)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()
