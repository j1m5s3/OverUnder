from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.config import get_settings

CDP_HOST = "api.cdp.coinbase.com"
CDP_BASE = "https://api.cdp.coinbase.com/platform"


@dataclass
class CdpUser:
    user_id: str
    smart_account: str
    access_token: str | None = None


def require_cdp():
    settings = get_settings()
    if not settings.cdp_project_id or not settings.cdp_api_key_secret:
        raise HTTPException(503, "CDP not configured")
    return settings


def parse_cdp_user(obj: object, access_token: str | None = None) -> CdpUser:
    if isinstance(obj, CdpUser):
        return obj
    data = obj if isinstance(obj, dict) else None
    nested = data.get("user") if isinstance(data, dict) else None

    def _attr(name: str, alt: str | None = None):
        if data and data.get(name) is not None:
            return data.get(name)
        if alt and data and data.get(alt) is not None:
            return data.get(alt)
        if isinstance(nested, dict) and nested.get(name) is not None:
            return nested.get(name)
        if alt and isinstance(nested, dict) and nested.get(alt) is not None:
            return nested.get(alt)
        return getattr(obj, name, None) if not isinstance(obj, dict) else None

    user_id = (
        _attr("user_id", "userId")
        or _attr("id")
        or _attr("end_user_id", "endUserId")
    )
    accounts = (
        _attr("evm_smart_accounts", "evmSmartAccounts")
        or []
    )
    smart = None
    if accounts:
        first = accounts[0]
        if isinstance(first, str):
            smart = first
        elif isinstance(first, dict):
            smart = first.get("address")
        else:
            smart = getattr(first, "address", None)
    if not user_id or not smart:
        raise HTTPException(401, "CDP token missing smart account")
    token = access_token
    if data:
        token = token or data.get("accessToken") or data.get("access_token")
    return CdpUser(user_id=str(user_id), smart_account=str(smart).lower(), access_token=token)


def _developer_jwt(method: str, request_path: str) -> str:
    settings = require_cdp()
    try:
        from cdp.auth.utils.jwt import JwtOptions, generate_jwt
    except ImportError as exc:
        raise HTTPException(503, "CDP not configured") from exc
    try:
        return generate_jwt(
            JwtOptions(
                api_key_id=settings.cdp_api_key_id,
                api_key_secret=settings.cdp_api_key_secret,
                request_method=method,
                request_host=CDP_HOST,
                request_path=request_path,
            )
        )
    except Exception as exc:
        raise HTTPException(503, "CDP not configured") from exc


async def _cdp_request(method: str, path: str, json_body: dict | None = None) -> dict:
    require_cdp()
    request_path = path if path.startswith("/platform") else f"/platform{path}"
    url_path = path[len("/platform") :] if path.startswith("/platform") else path
    headers = {
        "Authorization": f"Bearer {_developer_jwt(method, request_path)}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.request(
            method,
            f"{CDP_BASE}{url_path}",
            headers=headers,
            json=json_body,
        )
    if res.status_code >= 400:
        raise HTTPException(res.status_code if res.status_code in (400, 401, 403, 404) else 502, "CDP request failed")
    if not res.content:
        return {}
    try:
        return res.json()
    except ValueError as exc:
        raise HTTPException(502, "CDP request failed") from exc


async def validate_access_token(access_token: str) -> CdpUser:
    settings = require_cdp()
    if not access_token:
        raise HTTPException(400, "missing CDP access token")
    try:
        from cdp import CdpClient
    except ImportError as exc:
        raise HTTPException(503, "CDP not configured") from exc
    try:
        kwargs = {"api_key_secret": settings.cdp_api_key_secret}
        if settings.cdp_api_key_id:
            kwargs["api_key_id"] = settings.cdp_api_key_id
        async with CdpClient(**kwargs) as cdp:
            end_user = await cdp.end_user.validate_access_token(access_token=access_token)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, "invalid CDP access token") from exc
    return parse_cdp_user(end_user, access_token=access_token)


async def sign_in_with_email(email: str) -> dict:
    settings = require_cdp()
    if not email:
        raise HTTPException(400, "missing email")
    project_id = settings.cdp_project_id
    data = await _cdp_request(
        "POST",
        f"/v2/embedded-wallet-api/projects/{project_id}/auth/init",
        {"type": "email", "email": email},
    )
    flow_id = data.get("flowId") or data.get("flow_id")
    if not flow_id:
        raise HTTPException(502, "CDP request failed")
    return {"flowId": flow_id}


async def verify_email_otp(flow_id: str, otp: str) -> CdpUser:
    require_cdp()
    if not flow_id or not otp:
        raise HTTPException(400, "missing flowId or otp")
    project_id = require_cdp().cdp_project_id
    data = await _cdp_request(
        "POST",
        f"/v2/embedded-wallet-api/projects/{project_id}/auth/verify/email",
        {"flowId": flow_id, "otp": otp},
    )
    token = data.get("accessToken") or data.get("access_token")
    if token:
        return await validate_access_token(token)
    return parse_cdp_user(data)


async def send_user_operation(*, user_id: str, address: str, calls: list[dict]) -> dict:
    require_cdp()
    path = f"/v2/embedded-wallet-api/end-users/{user_id}/evm/smart-accounts/{address}/send"
    return await _cdp_request(
        "POST",
        path,
        {
            "network": "base-sepolia",
            "calls": calls,
            "useCdpPaymaster": True,
        },
    )
