#!/usr/bin/env python3
"""Minimal client for the Grandstream WP826 web API (firmware 1.0.3.35).

Usage:
  wp826.py [-c CREDFILE] HOST get 334 1413 :dnd
  wp826.py [-c CREDFILE] HOST set 334=10 1413=30

Credentials come from WP826_USERNAME / WP826_PASSWORD, else CREDFILE, else the
first of .credential-admin / .credential in the repo root (USERNAME= and
PASSWORD= lines).

Uses HTTPS. The handset's certificate is per-device and signed by a
Grandstream private CA, so it is not verified.

Protocol (reverse-engineered from the web UI bundle):
  POST /cgi-bin/access   access=sha256(user)            -> body = nonce
  POST /cgi-bin/dologin  username, sha256(pass + nonce) -> sid cookie
  GET  /cgi-bin/config_get?pvalues=334,1413
  PUT  /cgi-bin/config_update  {"alias": {}, "pvalue": {"334": "10"}}
  POST /cgi-bin/dologout
Form POSTs are rejected (403) without a same-origin Referer header.
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


class WP826:
    def __init__(self, host, username, password):
        self.base = f"https://{host}"
        self.username = username
        self.password = password
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def _req(self, method, path, data=None, json_body=None):
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": self.base + "/"}
        body = b""
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(
            self.base + "/cgi-bin" + path,
            data=body if method != "GET" else None,
            headers=headers,
            method=method,
        )
        with self.opener.open(req, timeout=10) as resp:
            return json.loads(resp.read())

    def login(self):
        r = self._req("POST", "/access", {"access": sha256(self.username)})
        nonce = r["body"]
        r = self._req(
            "POST",
            "/dologin",
            {"username": self.username, "password": sha256(self.password + nonce)},
        )
        if r.get("response") != "success":
            # body is e.g. "wrong3" (attempts left) or "locked"
            raise RuntimeError(f"login failed: {r.get('body')}")

    def logout(self):
        self._req("POST", "/dologout", {})

    def get(self, pvalues):
        q = urllib.parse.urlencode(
            {"pvalues": ",".join(pvalues), "update_session": "false"}
        )
        r = self._req("GET", "/config_get?" + q)
        return {c["pvalue"]: (c["alias"], c["value"]) for c in r.get("configs", [])}

    def set(self, values):
        r = self._req("PUT", "/config_update", json_body={"alias": {}, "pvalue": values})
        if r.get("response") != "success":
            raise RuntimeError(f"update failed: {r}")


def load_credentials(path=None):
    user = os.environ.get("WP826_USERNAME")
    password = os.environ.get("WP826_PASSWORD")
    if user and password:
        return user, password
    if path is None:
        # The repo root, one level up from scripts/.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [os.path.join(root, f) for f in (".credential-admin", ".credential")]
        path = next((c for c in candidates if os.path.exists(c)), candidates[-1])
    creds = dict(line.strip().split("=", 1) for line in open(path) if "=" in line)
    return creds["USERNAME"], creds["PASSWORD"]


def main(argv):
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument("-c", "--credential")
    parser.add_argument("host")
    parser.add_argument("cmd", choices=("get", "set"))
    parser.add_argument("args", nargs="+")
    opts = parser.parse_args(argv[1:])
    host, cmd, args = opts.host, opts.cmd, [a.removeprefix("P") for a in opts.args]
    phone = WP826(host, *load_credentials(opts.credential))
    phone.login()
    try:
        if cmd == "get":
            for p, (alias, value) in phone.get(args).items():
                print(f"P{p}\t{alias}\t{value}")
        else:
            phone.set(dict(a.split("=", 1) for a in args))
            for p, (alias, value) in phone.get([a.split("=")[0] for a in args]).items():
                print(f"P{p}\t{alias}\t{value}")
    finally:
        phone.logout()


if __name__ == "__main__":
    main(sys.argv)
