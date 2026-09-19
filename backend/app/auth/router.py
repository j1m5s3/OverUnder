from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
import jwt
import httpx
import json
from datetime import datetime, timedelta, timezone
from eth_account.messages import encode_defunct
from eth_account import Account

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


@router.post("/privy")
async def privy(body: PrivyVerify, db: AsyncSession = Depends(get_db)):
    if not body.token:
        raise HTTPException(400, "missing privy token")
    
    anvil_bypass = getattr(settings, 'auth_anvil_bypass', False) or False
    if anvil_bypass and settings.chain_id == 31337:
        if body.token == "privy-demo-anvil-only":
            addr = "0x" + "de" * 20
            
            # Check if this is the operator address
            is_operator = False
            if settings.operator_private_key:
                try:
                    operator_acct = Account.from_key(settings.operator_private_key)
                    is_operator = (addr.lower() == operator_acct.address.lower())
                except Exception:
                    pass
            
            user = (await db.execute(select(User).where(User.address == addr))).scalar_one_or_none()
            if user is None:
                user = User(address=addr, is_operator=is_operator)
                db.add(user)
                await db.commit()
                await db.refresh(user)
            else:
                if user.is_operator != is_operator:
                    user.is_operator = is_operator
                    await db.commit()
            return {"token": _issue(addr, user.is_operator), "address": addr}
    
    if not settings.privy_app_id:
        raise HTTPException(503, "Privy not configured")
    
    try:
        jwks_resp = await httpx.AsyncClient().get(
            "https://auth.privy.io/.well-known/jwks.json",
            timeout=5.0
        )
        jwks_resp.raise_for_status()
        jwks = jwks_resp.json()
    except Exception as e:
        raise HTTPException(503, f"Failed to fetch Privy JWKS: {e}")
    
    try:
        unverified_header = jwt.get_unverified_header(body.token)
        kid = unverified_header.get("kid")
        
        key = None
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == kid:
                key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
                break
        
        if key is None:
            raise HTTPException(401, "Privy key not found in JWKS")
        
        claims = jwt.decode(
            body.token,
            key,
            algorithms=["RS256"],
            audience=settings.privy_app_id,
            issuer="privy.io",
        )
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Privy token verification failed: {e}")
    
    linked_accounts = claims.get("linked_accounts", [])
    
    if isinstance(linked_accounts, str):
        try:
            linked_accounts = json.loads(linked_accounts)
        except json.JSONDecodeError:
            raise HTTPException(401, "Invalid linked_accounts format in Privy claims")
    
    wallet_address = None
    for account in linked_accounts:
        if isinstance(account, dict) and account.get("type") == "wallet":
            wallet_address = account.get("address")
            break
    
    if not wallet_address:
        raise HTTPException(401, "No linked wallet in Privy claims")
    
    addr = wallet_address.lower()
    
    # Check if this is the operator address
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
