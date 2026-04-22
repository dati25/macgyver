#!/usr/bin/env python3
"""Run a Rossum hook payload against a local hook module.

Usage:
    python3 run_hook.py --module path/to/hook.py --payload path/to/payload.json
                       [--entry rossum_hook_request_handler]
                       [--secrets path/to/.env]
                       [--log-level DEBUG]

SAFETY: non-GET HTTP calls (POST/PUT/PATCH/DELETE) are blocked by default.
All non-loopback socket connections are blocked unless sanctioned by a
known HTTP library patcher performing a GET. This is fail-closed: any
library we haven't explicitly patched cannot reach the network at all.
"""

import argparse
import contextvars
import importlib.util
import json
import logging
import os
import socket
import sys
import traceback


# --- Network guards ---

_BLOCKED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK_PREFIXES = ("127.", "::1", "0:0:0:0:0:0:0:1")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_sanctioned = contextvars.ContextVar("sanctioned", default=False)


def _is_loopback(host):
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    return any(host.startswith(p) for p in _LOOPBACK_PREFIXES)


def _log_blocked(source, method, url):
    print(f"[run_hook] BLOCKED {source} {method.upper()} {url}", file=sys.stderr)


class BlockedByReadOnly(Exception):
    """Raised when a non-GET network call is made through an unhandled path."""


def _sanctioned_call(fn, *args, **kwargs):
    token = _sanctioned.set(True)
    try:
        return fn(*args, **kwargs)
    finally:
        _sanctioned.reset(token)


async def _sanctioned_acall(coro_fn, *args, **kwargs):
    token = _sanctioned.set(True)
    try:
        return await coro_fn(*args, **kwargs)
    finally:
        _sanctioned.reset(token)


def _install_socket_backstop():
    """Block non-loopback socket connections unless sanctioned."""
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_create = socket.create_connection

    def _check(address):
        host = address[0] if isinstance(address, tuple) else address
        if _sanctioned.get() or _is_loopback(host):
            return
        raise PermissionError(
            f"[run_hook] BLOCKED socket connect to {host!r} — "
            "network is disabled except for sanctioned GET requests through "
            "patched HTTP libraries. If this is a legitimate GET, the library "
            "needs its own Layer 1 patcher."
        )

    def guarded_connect(self, address):
        _check(address)
        return orig_connect(self, address)

    def guarded_connect_ex(self, address):
        _check(address)
        return orig_connect_ex(self, address)

    def guarded_create(address, *args, **kwargs):
        _check(address)
        return orig_create(address, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create


def _patch_requests():
    try:
        import requests
    except ImportError:
        return
    orig_send = requests.Session.send

    def send(self, request, **kwargs):
        method = (request.method or "").upper()
        if method in _BLOCKED_METHODS:
            _log_blocked("requests", method, request.url)
            raise BlockedByReadOnly(
                f"requests {method} {request.url} blocked by run_hook"
            )
        return _sanctioned_call(orig_send, self, request, **kwargs)

    requests.Session.send = send


def _patch_httpx():
    try:
        import httpx
    except ImportError:
        return

    orig_send = httpx.Client.send

    def send(self, request, **kwargs):
        method = (request.method or "").upper()
        if method in _BLOCKED_METHODS:
            _log_blocked("httpx", method, str(request.url))
            raise BlockedByReadOnly(
                f"httpx {method} {request.url} blocked by run_hook"
            )
        return _sanctioned_call(orig_send, self, request, **kwargs)

    httpx.Client.send = send

    orig_asend = httpx.AsyncClient.send

    async def asend(self, request, **kwargs):
        method = (request.method or "").upper()
        if method in _BLOCKED_METHODS:
            _log_blocked("httpx-async", method, str(request.url))
            raise BlockedByReadOnly(
                f"httpx-async {method} {request.url} blocked by run_hook"
            )
        return await _sanctioned_acall(orig_asend, self, request, **kwargs)

    httpx.AsyncClient.send = asend


def _patch_http_client():
    """Catches urllib3 (via requests/boto3) and direct http.client usage."""
    import http.client

    orig_request = http.client.HTTPConnection.request

    def request(self, method, url, body=None, headers=None, *args, **kwargs):
        if (method or "").upper() in _BLOCKED_METHODS:
            host = getattr(self, "host", "?")
            _log_blocked("http.client", method, f"{host}{url}")
            raise BlockedByReadOnly(
                f"http.client {method} {host}{url} blocked by run_hook"
            )
        return _sanctioned_call(
            orig_request, self, method, url, body, headers or {}, *args, **kwargs
        )

    http.client.HTTPConnection.request = request


def _patch_urllib():
    import urllib.request

    orig_open = urllib.request.OpenerDirector.open

    def open_(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        if hasattr(fullurl, "get_method"):
            method = fullurl.get_method().upper()
            url = fullurl.full_url
        else:
            method = "POST" if data is not None else "GET"
            url = fullurl
        if method in _BLOCKED_METHODS:
            _log_blocked("urllib", method, url)
            raise BlockedByReadOnly(f"urllib {method} {url} blocked by run_hook")
        return _sanctioned_call(orig_open, self, fullurl, data, timeout)

    urllib.request.OpenerDirector.open = open_


def install_network_guards():
    _install_socket_backstop()
    for fn in (_patch_requests, _patch_httpx, _patch_http_client, _patch_urllib):
        try:
            fn()
        except Exception as exc:
            print(
                f"[run_hook] failed to install {fn.__name__}: {exc!r}",
                file=sys.stderr,
            )


# --- Runner core ---


def _parse_env_file(path):
    out = {}
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                print(f"[run_hook] skipping malformed .env line: {raw!r}", file=sys.stderr)
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            out[key] = value
    return out


def _load_module(module_path):
    abs_path = os.path.abspath(module_path)
    module_dir = os.path.dirname(abs_path)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    module_name = os.path.splitext(os.path.basename(abs_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for {abs_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description="Run a Rossum hook payload against local hook code.")
    parser.add_argument("--module", required=True, help="Path to the hook .py file.")
    parser.add_argument("--payload", required=True, help="Path to the payload JSON file.")
    parser.add_argument(
        "--entry",
        default="rossum_hook_request_handler",
        help="Entry function name (default: rossum_hook_request_handler).",
    )
    parser.add_argument(
        "--secrets",
        help="Optional path to a KEY=VALUE .env file; values are merged into payload['secrets'].",
    )
    parser.add_argument(
        "--log-level",
        default="DEBUG",
        help="Python logging level for the hook (default: DEBUG).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.DEBUG),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    install_network_guards()

    with open(args.payload) as fh:
        payload = json.load(fh)

    if args.secrets:
        secrets = _parse_env_file(args.secrets)
        payload.setdefault("secrets", {})
        payload["secrets"].update(secrets)
        print(f"[run_hook] merged {len(secrets)} secret(s) from {args.secrets}", file=sys.stderr)
    else:
        redacted = [k for k, v in (payload.get("secrets") or {}).items() if isinstance(v, str) and "redacted" in v]
        if redacted:
            print(
                f"[run_hook] warning: payload['secrets'] contains redacted values ({redacted}). "
                f"Pass --secrets to inject real values if the hook reads them.",
                file=sys.stderr,
            )

    try:
        module = _load_module(args.module)
    except Exception:
        print(f"[run_hook] failed to import {args.module}:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    entry = getattr(module, args.entry, None)
    if entry is None:
        print(f"[run_hook] module has no function named {args.entry!r}", file=sys.stderr)
        sys.exit(1)

    try:
        result = entry(payload)
    except Exception:
        print("[run_hook] hook raised an exception:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    try:
        print(json.dumps(result, indent=2, default=str))
    except TypeError:
        print(repr(result))


if __name__ == "__main__":
    main()
