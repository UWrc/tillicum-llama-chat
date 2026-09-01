#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OOD reverse proxy for the llama.cpp server Web UI.

Request flow:

  Browser -> OpenOnDemand -> /rnode/<host>/<port>/...   (OOD node proxy)
          -> :$PORT (this proxy, on the compute node)
          -> 127.0.0.1:$UPSTREAM_PORT (llama-server inside Apptainer)

The llama.cpp Web UI is built to be subpath-native:
  * every HTML asset reference is relative (./_app/..., ./favicon.ico)
  * the SvelteKit router computes its base from the current location
  * the chat/stream/slots API calls are relative (./v1/chat/completions)

The only things that break behind OOD's /rnode/<host>/<port>/ prefix are
root-absolute API paths in the UI's JavaScript (/v1/models, /models/load,
/models/sse, /models/unload, /tools, /mcp-servers, /cors-proxy) and the
PWA service worker (scoped per port, risk of stale caches). So this proxy
only:

  1. Strips the /rnode/<host>/<port> prefix before forwarding.
  2. Rewrites Origin/Referer to localhost so the server's CORS policy
     (restricted to localhost when --tools is enabled) accepts requests.
  3. Injects ONE small <script> into HTML responses that rewrites
     root-absolute URLs in fetch/XHR/EventSource/WebSocket and disables
     service-worker registration.
  4. Rewrites root-absolute paths in modulepreload Link headers.
  5. Streams all non-HTML responses (SSE chat streams, gzipped assets)
     without buffering.
  6. While the upstream is not up yet (model still loading), serves a
     friendly auto-reloading "loading" page instead of a 502.

Note on compression: this llama.cpp build ships its UI assets pre-gzipped
and answers 415 ("gzip is not supported by this browser") to any asset
request whose Accept-Encoding lacks "gzip". So the proxy passes the
browser's Accept-Encoding through untouched, and only decompresses
HTML responses (gzip/deflate) before patching them.

Standard library only (python3 on the compute nodes).
"""

import http.client
import os
import re
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("PORT") or 0)
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "9931"))
BASE = (os.environ.get("PATCH_BASE", "/") or "/").rstrip("/")
DEBUG = os.environ.get("PROXY_DEBUG", "0") == "1"

MAX_HTML = 25 * 1024 * 1024      # max size of an HTML document we will patch
CONNECT_TIMEOUT = 30             # seconds to connect to upstream
RESPONSE_TIMEOUT = 900           # generous read timeout (long-lived SSE)

# ---------------------------------------------------------------------------
# JS shim injected into every HTML page, BEFORE any other script runs.
# - Rewrites root-absolute URLs ("/v1/models", "/tools", ...) used by
#   fetch(), XHR, EventSource and WebSocket to be under the OOD prefix.
# - Leaves relative URLs alone: they already resolve under the prefix.
# - Leaves foreign absolute http(s) URLs alone; same-origin URLs built
#   from location.origin (Request/URL objects) get their path prefixed.
# - Disables service-worker registration (PWA scope is per
#   /rnode/<host>/<port>/ and its cache could serve stale responses).
# ---------------------------------------------------------------------------
SHIM = (
    "<script>(function(){'use strict';"
    "if(window.__OOD_PROXY_PATCHED)return;window.__OOD_PROXY_PATCHED=true;"
    "var B=%s;"
    "function r(u){"
    "if(typeof u!=='string')return u;"
    "if(u[0]!== '/')return u;"
    "if(u===B||u.indexOf(B+'/')===0)return u;"
    "return B+u;"
    "}"
    "var of=window.fetch;"
    "if(of)window.fetch=function(input,init){"
    "if(typeof input==='string')input=r(input);"
    "else if(input){"
    "var uu=typeof input.url==='string'?input.url:(typeof input.href==='string'?input.href:null);"
    "if(uu&&uu.indexOf(location.origin)===0){"
    "var p=new URL(uu);"
    "input=new URL(r(p.pathname)+p.search+String(p.hash),location.origin).toString();"
    "}}"
    "return of.call(this,input,init);"
    "};"
    "var oo=XMLHttpRequest.prototype.open;"
    "XMLHttpRequest.prototype.open=function(m,u){arguments[1]=r(u);return oo.apply(this,arguments);};"
    "var ES=window.EventSource;"
    "if(ES){window.EventSource=function(u,p){if(typeof u==='string')u=r(u);return new ES(u,p);};"
    "window.EventSource.prototype=ES.prototype;"
    "['CONNECTING','OPEN','CLOSED'].forEach(function(k){window.EventSource[k]=ES[k];});}"
    "var WS=window.WebSocket;"
    "if(WS){window.WebSocket=function(u,p){"
    "if(typeof u==='string'){"
    "if(u[0]==='/')u=r(u);"
    "else if(u.indexOf('ws://')===0||u.indexOf('wss://')===0){"
    "var s=u.indexOf('/',u.indexOf('//')+2);"
    "if(s>0){"
    "var h=u.slice(6,s).replace(/:[0-9]+$/,'');"
    "var lh=(location.host||'').replace(/:[0-9]+$/,'');"
    "if(h===lh)u=u.slice(0,s)+r(u.slice(s));"
    "}}}"
    "return new WS(u,p);};window.WebSocket.prototype=WS.prototype;"
    "['CONNECTING','OPEN','CLOSING','CLOSED'].forEach(function(k){window.WebSocket[k]=WS[k];});}"
    "try{Object.defineProperty(navigator,'serviceWorker',"
    "{configurable:true,get:function(){return undefined;}});}catch(e){}"
    "})();</script>"
) % ("'" + BASE + "'")

# Served while the llama server is not up yet (model still loading).
LOADING_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>llama.cpp Web UI &mdash; starting</title>
<style>
 body{font-family:system-ui,sans-serif;background:#101014;color:#e5e5ea;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
 .card{background:#1c1c22;border:1px solid #2e2e36;border-radius:12px;
        padding:32px 40px;max-width:520px;text-align:center}
 h1{font-size:20px;margin:0 0 12px}
 p{color:#9a9aa3;line-height:1.5}
 .spin{display:inline-block;width:18px;height:18px;border:2px solid #44444e;
        border-top-color:#e5e5ea;border-radius:50%;animation:s 1s linear infinite;
        vertical-align:-4px;margin-right:8px}
 @keyframes s{to{transform:rotate(360deg)}}
</style></head>
<body><div class="card">
 <h1><span class="spin"></span>Starting llama.cpp server</h1>
 <p>The inference server is loading the model. Large GGUF files can take
    several minutes on first start. This page refreshes automatically
    &mdash; just wait.</p>
</div></body></html>
"""


def _prefix(path):
    """Prefix a root-absolute path with the OOD base (idempotent)."""
    if path == BASE or path.startswith(BASE + "/"):
        return path
    return BASE + path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ood-llama-proxy/1.1"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        if DEBUG:
            sys.stderr.write("[proxy] %s\n" % (fmt % args))

    def do_GET(self):     self._proxy()
    def do_HEAD(self):    self._proxy()
    def do_POST(self):    self._proxy()
    def do_PUT(self):     self._proxy()
    def do_PATCH(self):   self._proxy()
    def do_DELETE(self):  self._proxy()
    def do_OPTIONS(self): self._proxy()

    # ------------------------------------------------------- path handling
    def _strip_prefix(self, url):
        for u in (url, unquote(url)):
            if u.startswith(BASE + "/"):
                return u[len(BASE):]
            if u == BASE:
                return "/"
        return url

    # ------------------------------------------------------------ html bits
    def _patch_html(self, html):
        out = html

        # defensive: strip accidental absolute upstream origins
        out = re.sub(r"https?://127\.0\.0\.1:%d(?=[\"'/?)\s]|$)" % UPSTREAM_PORT, "", out)
        out = re.sub(r"https?://localhost:%d(?=[\"'/?)\s]|$)" % UPSTREAM_PORT, "", out)

        # inject the shim first thing inside <head> (runs before the
        # deferred SvelteKit module script)
        m = re.search(r"<head[^>]*>", out, re.IGNORECASE)
        if m:
            out = out[:m.end()] + "\n" + SHIM + out[m.end():]
        else:
            out = SHIM + out
        return out

    def _patch_link_header(self, value):
        # SvelteKit modulepreload: </_app/immutable/bundle...js>; rel=...
        return re.sub(
            r"<(/[^;>]+)>",
            lambda m: "<%s>" % _prefix(m.group(1)),
            value)

    # ------------------------------------------------------------- sending
    def _relay_headers(self, headers, keep_content_length, strip_content_encoding=False):
        """Send upstream response headers, normalizing them for the client."""
        for k, v in headers:
            lk = k.lower()
            if lk == "transfer-encoding":
                continue
            if lk == "content-encoding" and strip_content_encoding:
                continue
            if lk == "content-length" and not keep_content_length:
                continue
            if lk == "access-control-allow-origin":
                client_origin = self.headers.get("Origin")
                if client_origin:
                    self.send_header(k, client_origin)
                    self.send_header("Access-Control-Allow-Credentials", "true")
                continue
            if lk == "link":
                self.send_header(k, self._patch_link_header(v))
                continue
            self.send_header(k, v)

    def _maybe_decompress(self, data, encoding):
        """Decompress a response body if needed (None = unsupported/failed)."""
        if not encoding or encoding == "identity":
            return data
        if encoding == "gzip":
            try:
                return zlib.decompress(data, 47)
            except zlib.error:
                return None
        if encoding == "deflate":
            for wbits in (15, -15):
                try:
                    return zlib.decompress(data, wbits)
                except zlib.error:
                    continue
            return None
        return None

    def _send_upstream(self, resp):
        rheaders = resp.getheaders()
        ctype = resp.getheader("Content-Type") or ""
        enc = (resp.getheader("Content-Encoding") or "").lower()
        is_html = "text/html" in ctype
        has_cl = resp.getheader("Content-Length") is not None
        chunked = resp.getheader("Transfer-Encoding", "").lower() == "chunked"

        if is_html and not chunked:
            data = resp.read(MAX_HTML + 1)
            decompressed = self._maybe_decompress(data, enc)
            self.send_response(resp.status)
            if decompressed is not None and len(decompressed) <= MAX_HTML:
                patched = self._patch_html(decompressed.decode("utf-8", "replace")).encode("utf-8")
                self._relay_headers(rheaders, keep_content_length=False,
                                    strip_content_encoding=True)
                self.send_header("Content-Length", str(len(patched)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                self.wfile.write(patched)
            else:
                self.log_message("HTML response cannot be decompressed/patched (enc=%r, %d bytes), streaming raw",
                                 enc, len(data))
                self._relay_headers(rheaders, keep_content_length=has_cl,
                                    strip_content_encoding=False)
                if not has_cl:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                self.wfile.write(data)
            self.wfile.flush()
            return

        # Streamed response (SSE, downloads, ...): pipe through unmodified.
        self.send_response(resp.status)
        self._relay_headers(rheaders, keep_content_length=has_cl)
        if not has_cl:
            # chunked upstream -> we de-chunked implicitly, so use close-delimiting
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        try:
            if resp.length is None and not resp.chunked:
                # No Content-Length: read1() returns bytes as they arrive.
                # (resp.read(n) would block until n bytes or EOF, which
                # would buffer an entire SSE stream before forwarding it.)
                read = resp.fp.read1
            else:
                read = resp.read
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_loading_page(self):
        data = LOADING_PAGE.encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------- forward
    def _proxy(self):
        url = self._strip_prefix(self.path)
        parts = urlsplit(url)
        path = parts.path + ("?" + parts.query if parts.query else "")

        headers = {}
        for k, v in self.headers.items():
            if k.lower() in ("host", "origin", "referer",
                             "connection", "proxy-authorization", "upgrade"):
                continue
            headers[k] = v
        if DEBUG:
            # Presence/length only - never log the key value itself.
            auth = self.headers.get("Authorization")
            self.log_message("%s %s auth=%s (%d chars)",
                             self.command, path,
                             "present" if auth else "ABSENT", len(auth or ""))
        # NOTE: accept-encoding is passed through untouched. This llama.cpp
        # build serves its UI assets pre-gzipped and returns 415 to requests
        # whose Accept-Encoding lacks "gzip".
        headers["Host"] = "%s:%d" % (UPSTREAM_HOST, UPSTREAM_PORT)
        headers["Origin"] = "http://localhost:%d" % UPSTREAM_PORT
        headers["Referer"] = "http://localhost:%d%s/" % (UPSTREAM_PORT, BASE)

        body = None
        if self.command in ("POST", "PUT", "PATCH"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                body = self.rfile.read(length)
                headers["Content-Length"] = str(len(body))

        conn = None
        try:
            conn = http.client.HTTPConnection(
                UPSTREAM_HOST, UPSTREAM_PORT, timeout=CONNECT_TIMEOUT)
            conn.request(self.command, path, body=body, headers=headers)
            resp = conn.getresponse()
            # long-lived streams must not be cut off by the socket timeout
            try:
                conn.sock.settimeout(RESPONSE_TIMEOUT)
            except Exception:
                pass
            self._send_upstream(resp)
        except (http.client.HTTPException, OSError) as e:
            self.log_message("UPSTREAM ERROR: %s", e)
            try:
                self._send_loading_page()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def main():
    if LISTEN_PORT <= 0:
        sys.stderr.write("ERROR: PORT environment variable is required\n")
        sys.exit(1)
    sys.stderr.write("[proxy] listening on %s:%d, upstream %s:%d, base %r\n"
                     % (LISTEN_HOST, LISTEN_PORT, UPSTREAM_HOST, UPSTREAM_PORT, BASE))
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
