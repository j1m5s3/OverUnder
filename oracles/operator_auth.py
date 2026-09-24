"""Operator auth preflight for the oracle tick: probe, then SIWE bootstrap once.

The API's `require_operator` decides on `User.is_operator`, not the JWT `op`
claim. A fresh database has no operator row, so the job signs in once with SIWE
using OPERATOR_PRIVATE_KEY; the API sets the flag from its own key. The SIWE
token is discarded; stages keep minting their own operator JWT.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

from redact import log_error
from scores.publish import mint_operator_jwt

PROBE_PATH = "/api/v1/markets/schedule"
NONCE_PATH = "/api/v1/auth/nonce/{address}"
SIWE_PATH = "/api/v1/auth/siwe"
STATEMENT = "OverUnder oracle operator bootstrap"
BOOTSTRAP_ON = frozenset({(401, "unknown user"), (403, "operator only")})


def _operator_key() -> str:
    key = os.getenv("OPERATOR_PRIVATE_KEY", "").strip()
    if not key:
        raise RuntimeError("OPERATOR_PRIVATE_KEY missing")
    try:
        Account.from_key(key)
    except Exception:
        raise RuntimeError("OPERATOR_PRIVATE_KEY invalid") from None
    return key


def operator_address() -> str:
    return Account.from_key(_operator_key()).address


def _chain_id() -> int:
    try:
        return int(os.getenv("CHAIN_ID") or "0") or 1
    except ValueError:
        return 1


def build_siwe_message(*, api_url: str, address: str, nonce: str, chain_id: int, issued_at: datetime) -> str:
    domain = urlparse(api_url).netloc or api_url
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    stamp = issued_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n"
        "\n"
        f"{STATEMENT}\n"
        "\n"
        f"URI: {api_url}\n"
        "Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {stamp}"
    )


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"]
    return ""


def probe(client: httpx.Client, api_url: str, token: str) -> tuple[int, str]:
    response = client.post(
        f"{api_url}{PROBE_PATH}",
        json=[],
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.status_code, _detail(response)


def siwe_bootstrap(client: httpx.Client, api_url: str, *, now: datetime | None = None) -> None:
    key = _operator_key()
    address = Account.from_key(key).address
    response = client.get(f"{api_url}{NONCE_PATH.format(address=address.lower())}")
    if response.status_code != 200:
        raise RuntimeError(f"siwe failed: {response.status_code} {_detail(response)}".rstrip())
    try:
        nonce = response.json().get("nonce")
    except (ValueError, AttributeError):
        nonce = None
    if not isinstance(nonce, str) or not nonce:
        raise RuntimeError("siwe failed: nonce missing from response")
    message = build_siwe_message(
        api_url=api_url,
        address=address,
        nonce=nonce,
        chain_id=_chain_id(),
        issued_at=now or datetime.now(timezone.utc),
    )
    signed = Account.sign_message(encode_defunct(text=message), private_key=key)
    signature = "0x" + bytes(signed.signature).hex()
    response = client.post(
        f"{api_url}{SIWE_PATH}",
        json={"message": message, "signature": signature, "address": address},
    )
    if response.status_code != 200:
        raise RuntimeError(f"siwe failed: {response.status_code} {_detail(response)}".rstrip())


def _cause(status: int, detail: str, operator: str, bootstrapped: bool) -> str:
    lowered = detail.strip().lower()
    if status == 401 and lowered == "invalid token":
        return (
            "invalid token: job JWT_SECRET differs from API JWT_SECRET "
            "(API reads OU_JWT_SECRET unstripped; check trailing newline)"
        )
    if status == 401 and lowered == "unknown user":
        return "unknown user after SIWE bootstrap" if bootstrapped else "unknown user"
    if status == 401:
        return f"unauthorized: {detail or 'no detail'}"
    if status == 403 and lowered == "operator only":
        return f"operator only: API OPERATOR_PRIVATE_KEY does not derive {operator} (or has whitespace)"
    if status == 403:
        return f"forbidden: {detail or 'non-JSON response'}"
    if status == 404:
        return "probe route missing: check OU_API_URL"
    if status >= 500:
        return f"api unreachable: {status}"
    return f"unexpected probe status {status}: {detail or 'no detail'}"


def auth_preflight(*, client: httpx.Client | None = None, now: datetime | None = None) -> dict:
    result: dict = {"ok": False, "operator": None, "status": None, "detail": "", "bootstrapped": False, "cause": ""}
    api_url = os.getenv("OU_API_URL", "").strip().rstrip("/")
    if not api_url:
        result["cause"] = "config: OU_API_URL missing"
        return result
    try:
        operator = operator_address().lower()
    except RuntimeError as exc:
        result["cause"] = f"config: {exc}"
        return result
    result["operator"] = operator
    if not os.getenv("JWT_SECRET", "").strip():
        result["cause"] = "config: JWT_SECRET missing"
        return result
    try:
        token = mint_operator_jwt()
    except Exception as exc:
        result["cause"] = f"config: operator JWT mint failed ({type(exc).__name__})"
        return result
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        status, detail = probe(http, api_url, token)
        if (status, detail.strip().lower()) in BOOTSTRAP_ON:
            log_error(f"auth preflight: probe {status} {detail}; SIWE bootstrap for {operator}")
            try:
                siwe_bootstrap(http, api_url, now=now)
            except RuntimeError as exc:
                result.update(status=status, detail=detail, cause=str(exc))
                return result
            result["bootstrapped"] = True
            status, detail = probe(http, api_url, token)
        result.update(status=status, detail=detail)
        if 200 <= status < 300:
            result["ok"] = True
        else:
            result["cause"] = _cause(status, detail, operator, result["bootstrapped"])
    except httpx.HTTPError as exc:
        result["cause"] = f"api unreachable: {type(exc).__name__}"
    finally:
        if own:
            http.close()
    if not result["ok"]:
        log_error(f"auth preflight failed: {result['cause']}")
    return result
