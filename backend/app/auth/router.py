from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
import jwt
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import get_db
from app.models import Nonce, User

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class SiweVerify(BaseModel):
    message: str
    signature: str
    address: str


class PrivyVerify(BaseModel):
    token: str
    address: str


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
    addr = body.address.lower()
    if addr not in body.message.lower():
        raise HTTPException(400, "address mismatch")
    row = await db.get(Nonce, addr)
    if row is None or row.nonce not in body.message:
        raise HTTPException(400, "bad nonce")
    user = (await db.execute(select(User).where(User.address == addr))).scalar_one_or_none()
    if user is None:
        user = User(address=addr, is_operator=False)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return {"token": _issue(addr, user.is_operator), "address": addr}


@router.post("/privy")
async def privy(body: PrivyVerify, db: AsyncSession = Depends(get_db)):
    addr = body.address.lower()
    if not body.token:
        raise HTTPException(400, "missing privy token")
    # Privy JWT verification is a seam: when PRIVY_APP_SECRET is set, decode later.
    user = (await db.execute(select(User).where(User.address == addr))).scalar_one_or_none()
    if user is None:
        user = User(address=addr, is_operator=False)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return {"token": _issue(addr, user.is_operator), "address": addr}
