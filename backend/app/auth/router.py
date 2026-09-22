from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from eth_account.messages import encode_defunct
from eth_account import Account

from app.cdp import CdpUser, sign_in_with_email, validate_access_token, verify_email_otp
from app.config import get_settings
from app.db import get_db
from app.models import Nonce, User

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class SiweVerify(BaseModel):
    message: str
    signature: str
    address: str


class CdpAccessToken(BaseModel):
    accessToken: str
    address: str | None = None


class CdpEmailStart(BaseModel):
    email: str


class CdpEmailVerify(BaseModel):
    flowId: str
    otp: str
    address: str | None = None


def _issue(address: str, is_operator: bool) -> str:
    payload = {
        "sub": address.lower(),
        "op": is_operator,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "invalid token") from exc
    addr = data["sub"]
    user = (await db.execute(select(User).where(User.address == addr))).scalar_one_or_none()
    if user is None:
        raise HTTPException(401, "unknown user")
    return user


def require_operator(user: User = Depends(get_current_user)) -> User:
    if not user.is_operator:
        raise HTTPException(403, "operator only")
    return user


@router.get("/nonce/{address}")
async def nonce(address: str, db: AsyncSession = Depends(get_db)):
    value = secrets.token_hex(16)
    row = await db.get(Nonce, address.lower())
    if row:
        row.nonce = value
    else:
        db.add(Nonce(address=address.lower(), nonce=value))
    await db.commit()
    return {"nonce": value}


@router.post("/siwe")
async def siwe(body: SiweVerify, db: AsyncSession = Depends(get_db)):
    if not body.signature or body.signature == "0x" or body.signature == "0x00":
        raise HTTPException(400, "invalid signature")
    
    message_encoded = encode_defunct(text=body.message)
    try:
        recovered = Account.recover_message(message_encoded, signature=body.signature)
    except Exception as e:
        raise HTTPException(400, f"ecrecover failed: {e}")
    
    if recovered.lower() != body.address.lower():
        raise HTTPException(400, "signature does not match address")
    
    addr = recovered.lower()
    
    if addr not in body.message.lower():
        raise HTTPException(400, "address not in message")
    
    row = await db.get(Nonce, addr)
    if row is None:
        raise HTTPException(401, "nonce not found")
    
    if row.nonce not in body.message:
        raise HTTPException(401, "nonce mismatch")
    
    result = await db.execute(
        delete(Nonce).where(Nonce.address == addr).where(Nonce.nonce == row.nonce)
    )
    await db.commit()
    
    if result.rowcount != 1:
        raise HTTPException(401, "nonce already consumed")
    
    # Determine if this address is the operator
    is_operator = False
    if settings.operator_private_key:
        try:
            operator_acct = Account.from_key(settings.operator_private_key)
            is_operator = (addr == operator_acct.address.lower())
        except Exception:
            pass
    
    user = (await db.execute(select(User).where(User.address == addr))).scalar_one_or_none()
    if user is None:
        user = User(address=addr, is_operator=is_operator)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        # Update operator flag if it changed
        if user.is_operator != is_operator:
            user.is_operator = is_operator
            await db.commit()
    
    return {"token": _issue(addr, user.is_operator), "address": addr}


async def _upsert_cdp_session(db: AsyncSession, cdp_user: CdpUser) -> dict:
    addr = cdp_user.smart_account.lower()
    user = (await db.execute(select(User).where(User.address == addr))).scalar_one_or_none()
    if user is None:
        user = User(address=addr, is_operator=False, cdp_user_id=cdp_user.user_id)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        user.is_operator = False
        user.cdp_user_id = cdp_user.user_id
        await db.commit()
    return {"token": _issue(addr, False), "address": addr}


@router.post("/cdp")
async def cdp_session(body: CdpAccessToken, db: AsyncSession = Depends(get_db)):
    cdp_user = await validate_access_token(body.accessToken)
    return await _upsert_cdp_session(db, cdp_user)


@router.post("/cdp/email")
async def cdp_email(body: CdpEmailStart):
    return await sign_in_with_email(body.email)


@router.post("/cdp/verify")
async def cdp_verify(body: CdpEmailVerify, db: AsyncSession = Depends(get_db)):
    cdp_user = await verify_email_otp(body.flowId, body.otp)
    return await _upsert_cdp_session(db, cdp_user)
