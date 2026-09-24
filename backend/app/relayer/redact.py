"""Mask secrets in relayer / emissions error text before it is stored, returned or logged.

web3 / requests transport errors quote the RPC URL ("... for url: https://host/v2/<KEY>",
"Max retries exceeded with url: /v2/<KEY>"). ANVIL_RPC_URL is a Secret Manager secret and a
hosted provider carries its API key in the path or query, so raw exception text must never
reach RelayJob.last_error (served to order makers), API details or Cloud Logging.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

from app.config import get_settings

MASK = "[redacted]"
# Path/query pieces shorter than this ("v2", "rpc") are not worth masking alone.
_MIN_URL_PIECE = 12

_WITH_URL = re.compile(r"(?i)((?:for|with) url: )\S+")
# scheme://[userinfo@]host[:port][/path?query#frag] -> scheme://host[:port]
_ANY_URL = re.compile(r"(?i)\b((?:https?|wss?)://)(?:[^@/\s'\"<>]*@)?([^/\s'\"<>?#]+)[^\s'\"<>]*")


def _url_fragments(url: str) -> set[str]:
    out: set[str] = set()
    p = urlparse(url)
    if not (p.scheme and p.netloc):
        if len(url) >= _MIN_URL_PIECE:
            out.add(url)
        return out
    for cred in (p.username, p.password):
        if cred:
            out.add(cred)
    for piece in (p.path, p.query, p.path + ("?" + p.query if p.query else "")):
        if len(piece) >= _MIN_URL_PIECE:
            out.add(piece)
    for seg in p.path.split("/"):
        if len(seg) >= _MIN_URL_PIECE:
            out.add(seg)
    for _, v in parse_qsl(p.query, keep_blank_values=True):
        if len(v) >= _MIN_URL_PIECE:
            out.add(v)
    return out


def _secret_keys(s) -> set[str]:
    out: set[str] = set()
    for name in ("relayer_private_key", "operator_private_key"):
        v = (getattr(s, name, "") or "").strip()
        if len(v) >= 32:
            out.add(v)
            out.add(v[2:] if v.lower().startswith("0x") else "0x" + v)
    return out


def redact(text: str | None) -> str:
    """Error text with RPC URL secrets, private keys and URL paths removed. Never raises."""
    if not text:
        return ""
    out = str(text)
    try:
        s = get_settings()
        rpc = (getattr(s, "anvil_rpc_url", "") or "").strip()
        if rpc:
            p = urlparse(rpc)
            if p.scheme and p.netloc:
                out = out.replace(rpc, f"{p.scheme}://{p.hostname or ''}{':' + str(p.port) if p.port else ''}")
            for frag in sorted(_url_fragments(rpc), key=len, reverse=True):
                out = out.replace(frag, MASK)
        for key in sorted(_secret_keys(s), key=len, reverse=True):
            out = re.sub(re.escape(key), MASK, out, flags=re.IGNORECASE)
    except Exception:
        pass
    out = _WITH_URL.sub(lambda m: m.group(1) + MASK, out)
    out = _ANY_URL.sub(lambda m: m.group(1) + m.group(2), out)
    return out


def rpc_reason(exc: BaseException, limit: int = 500) -> str:
    """Stable, secret-free reason for an RPC failure: 'transient (ReadTimeout)' or the redacted node message."""
    from app.relayer.chain import classify_rpc_error

    kind = classify_rpc_error(exc)
    if kind == "transient":
        return f"transient ({type(exc).__name__})"
    return redact(str(exc))[:limit]
