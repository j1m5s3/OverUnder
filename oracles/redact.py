"""Mask secrets in oracle log lines and the tick summary.

Stage errors can quote env secrets: web3/requests transport errors embed the RPC
URL (provider URLs carry an API key in the path or query), and other exceptions
can echo keys, the JWT secret, bearer tokens or Cursor credentials. Every
stderr line a stage prints goes through `log_error`, and job.main redacts the
JSON summary with `redact`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlparse

SECRET_ENV = (
    "JWT_SECRET",
    "OPERATOR_PRIVATE_KEY",
    "AGENT_ALPHA_KEY",
    "AGENT_BETA_KEY",
    "AGENT_GAMMA_KEY",
    "CURSOR_API_KEY",
    "CURSOR_SEARCH_MCP_URL",
    "CURSOR_SEARCH_MCP_HEADERS",
    "ANVIL_RPC_URL",
    "OU_RPC_URL",
    "OU_QUESTION_ID_KEY",
)
# Env vars whose value is a URL: any quote of it keeps scheme://host only.
URL_ENV = ("ANVIL_RPC_URL", "OU_RPC_URL", "CURSOR_SEARCH_MCP_URL")
MASK = "[redacted]"
_MIN_SECRET_LEN = 8
# Path/query pieces shorter than this (e.g. "v2", "rpc") are not worth masking alone.
_MIN_URL_PIECE = 12

_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")


def _fragments_of(value: str, *, url: bool = False) -> set[str]:
    parsed = urlparse(value)
    is_url = bool(parsed.scheme and parsed.netloc)
    # A bare scheme://host[:port] is not secret; only a URL's path, query and credentials are.
    out = set() if url and is_url else {value}
    if value.lower().startswith("0x"):
        out.add(value[2:])
    if value.startswith("{"):
        # CURSOR_SEARCH_MCP_HEADERS is a JSON object; json.dumps would escape it whole.
        try:
            headers = json.loads(value)
        except ValueError:
            headers = {}
        for item in headers.values() if isinstance(headers, dict) else ():
            if isinstance(item, str):
                out.add(item)
                out.add(item.split(" ", 1)[-1])
    if is_url:
        # Transport errors quote the path or query without the host.
        for cred in (parsed.username, parsed.password):
            if cred:
                out.add(cred)
        if parsed.path.strip("/") and len(parsed.path) >= _MIN_URL_PIECE:
            out.add(parsed.path)
        if parsed.query and len(parsed.query) >= _MIN_URL_PIECE:
            out.add(parsed.query)
        for part in parsed.path.split("/") + parsed.query.split("&"):
            piece = part.split("=", 1)[-1]
            if len(piece) >= _MIN_URL_PIECE:
                out.add(piece)
    return out


def _url_prefixes() -> list[tuple[str, str]]:
    """(full URL, scheme://host) pairs for URL env values that carry a path, query or credentials."""
    out = []
    for name in URL_ENV:
        value = os.getenv(name, "").strip()
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.hostname:
            continue
        port = f":{parsed.port}" if parsed.port else ""
        label = f"{parsed.scheme}://{parsed.hostname}{port}"
        if value.rstrip("/") != label:
            out.append((value, label))
    return sorted(out, key=lambda pair: len(pair[0]), reverse=True)


def secret_fragments() -> list[str]:
    fragments: set[str] = set()
    for name in SECRET_ENV:
        value = os.getenv(name, "").strip()
        if len(value) >= _MIN_SECRET_LEN:
            fragments |= _fragments_of(value, url=name in URL_ENV)
    return sorted((f for f in fragments if len(f) >= _MIN_SECRET_LEN), key=len, reverse=True)


def redact(text: object) -> str:
    """Mask env secrets, RPC/MCP URL paths and queries, bearer tokens and JWTs."""
    out = str(text)
    for full, label in _url_prefixes():
        out = out.replace(full, f"{label}/{MASK}")
    for fragment in secret_fragments():
        out = out.replace(fragment, MASK)
    out = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", out)
    return _JWT.sub(MASK, out)


def log_error(message: object) -> None:
    """Print one redacted line to stderr."""
    print(redact(message), file=sys.stderr)
