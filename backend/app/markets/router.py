from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.router import require_operator
from app.db import get_db
from app.models import Market, User

router = APIRouter(prefix="/markets", tags=["markets"])


class CreateMarketIn(BaseModel):
    question: str
    resolution_criteria: str = ""
    close_time: int
    question_id: str = Field(description="bytes32 hex")
    parent_condition_id: str = ""
    seed_usdc: int = 0
    market_type: int = 0
    suggested_probability: float = 0.5


@router.get("")
async def list_markets(
    parent_id: str | None = Query(default=None, alias="parentId"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Market).where(Market.paused.is_(False))
    if parent_id:
        stmt = stmt.where(Market.parent_condition_id == parent_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [_public(m) for m in rows]


@router.get("/{condition_id}")
async def get_market(condition_id: str, db: AsyncSession = Depends(get_db)):
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    return _public(m)


@router.post("")
async def create_market(
    body: CreateMarketIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    from app.config import get_settings
    import json
    from pathlib import Path
    from web3 import Web3
    from eth_account import Account

    settings = get_settings()
    
    if not settings.operator_private_key:
        raise HTTPException(500, "operator_private_key not configured")
    
    if body.seed_usdc <= 0:
        raise HTTPException(400, "seed_usdc must be > 0")
    
    root = Path(__file__).resolve().parents[3]
    deploy_path = root / "contracts" / "deployments" / f"{settings.chain_id}.json"
    
    if not deploy_path.exists():
        raise HTTPException(500, "deployment file not found")
    
    deploy = json.loads(deploy_path.read_text())
    factory_abi = json.loads((root / "backend" / "app" / "abi" / "MarketFactory.json").read_text())
    usdc_abi = json.loads((root / "backend" / "app" / "abi" / "MockUSDC.json").read_text())
    
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        raise HTTPException(500, "RPC not available")
    
    operator_acct = Account.from_key(settings.operator_private_key)
    factory = w3.eth.contract(address=Web3.to_checksum_address(deploy["MarketFactory"]), abi=factory_abi)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(deploy["MockUSDC"]), abi=usdc_abi)
    
    balance = usdc.functions.balanceOf(operator_acct.address).call()
    if balance < body.seed_usdc:
        try:
            faucet_tx = usdc.functions.faucet(body.seed_usdc).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed_faucet = operator_acct.sign_transaction(faucet_tx)
            faucet_txh = w3.eth.send_raw_transaction(signed_faucet.raw_transaction)
            w3.eth.wait_for_transaction_receipt(faucet_txh)
        except Exception as e:
            raise HTTPException(500, f"failed to fund operator: {e}")
    
    allowance = usdc.functions.allowance(operator_acct.address, factory.address).call()
    if allowance < body.seed_usdc:
        try:
            approve_tx = usdc.functions.approve(factory.address, body.seed_usdc).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed_approve = operator_acct.sign_transaction(approve_tx)
            approve_txh = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_txh)
        except Exception as e:
            raise HTTPException(500, f"failed to approve USDC: {e}")
    
    question_id_bytes = bytes.fromhex(body.question_id[2:] if body.question_id.startswith("0x") else body.question_id)
    
    try:
        if body.market_type == 0:
            create_tx = factory.functions.createPrimaryMarket(
                question_id_bytes,
                body.close_time,
                body.question,
                body.seed_usdc,
            ).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 1_000_000,
                "gasPrice": w3.eth.gas_price,
            })
        else:
            parent_bytes = bytes.fromhex(body.parent_condition_id[2:] if body.parent_condition_id.startswith("0x") else body.parent_condition_id)
            create_tx = factory.functions.createWildcardMarket(
                question_id_bytes,
                parent_bytes,
                body.close_time,
                body.question,
                body.seed_usdc,
            ).build_transaction({
                "from": operator_acct.address,
                "nonce": w3.eth.get_transaction_count(operator_acct.address),
                "chainId": settings.chain_id,
                "gas": 1_000_000,
                "gasPrice": w3.eth.gas_price,
            })
        
        signed_create = operator_acct.sign_transaction(create_tx)
        create_txh = w3.eth.send_raw_transaction(signed_create.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(create_txh)
        
        if receipt["status"] != 1:
            raise HTTPException(500, "market creation transaction failed")
        
        logs = factory.events.MarketCreated().process_receipt(receipt)
        if not logs:
            raise HTTPException(500, "no MarketCreated event in receipt")
        
        event = logs[0]["args"]
        condition_id = "0x" + event["conditionId"].hex()
        parent_condition_id = "0x" + event["parentConditionId"].hex()
        
        existing = await db.get(Market, condition_id)
        if existing is None:
            market = Market(
                condition_id=condition_id,
                parent_condition_id="" if parent_condition_id == "0x" + "00" * 32 else parent_condition_id,
                question=event["question"],
                resolution_criteria=body.resolution_criteria,
                market_type=event["marketType"],
                close_time=event["closeTime"],
                suggested_probability=body.suggested_probability,
            )
            db.add(market)
        else:
            existing.question = event["question"]
            existing.market_type = event["marketType"]
            existing.close_time = event["closeTime"]
            existing.resolution_criteria = body.resolution_criteria
            existing.suggested_probability = body.suggested_probability
        
        await db.commit()
        
        final = await db.get(Market, condition_id)
        return _public(final)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"failed to create market on chain: {e}")


@router.post("/{condition_id}/pause")
async def pause_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    from app.config import get_settings
    import json
    from pathlib import Path
    from web3 import Web3
    from eth_account import Account

    settings = get_settings()
    
    m = await db.get(Market, condition_id)
    if m is None:
        raise HTTPException(404, "market not found")
    
    if not settings.operator_private_key:
        raise HTTPException(500, "operator_private_key not configured")
    
    root = Path(__file__).resolve().parents[3]
    deploy_path = root / "contracts" / "deployments" / f"{settings.chain_id}.json"
    
    if not deploy_path.exists():
        raise HTTPException(500, "deployment file not found")
    
    deploy = json.loads(deploy_path.read_text())
    factory_abi = json.loads((root / "backend" / "app" / "abi" / "MarketFactory.json").read_text())
    
    w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
    if not w3.is_connected():
        raise HTTPException(500, "RPC not available")
    
    operator_acct = Account.from_key(settings.operator_private_key)
    factory = w3.eth.contract(address=Web3.to_checksum_address(deploy["MarketFactory"]), abi=factory_abi)
    
    cid_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
    
    try:
        pause_tx = factory.functions.setPaused(cid_bytes, True).build_transaction({
            "from": operator_acct.address,
            "nonce": w3.eth.get_transaction_count(operator_acct.address),
            "chainId": settings.chain_id,
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
        })
        
        signed = operator_acct.sign_transaction(pause_tx)
        txh = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(txh)
        
        if receipt["status"] != 1:
            raise HTTPException(500, "setPaused transaction failed")
        
        m.paused = True
        await db.commit()
        return _public(m)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"failed to pause market on chain: {e}")


def _public(m: Market) -> dict:
    return {
        "conditionId": m.condition_id,
        "parentConditionId": m.parent_condition_id or None,
        "question": m.question,
        "resolutionCriteria": m.resolution_criteria,
        "marketType": m.market_type,
        "closeTime": m.close_time,
        "paused": m.paused,
        "resolved": m.resolved,
        "payoutYes": m.payout_yes,
        "payoutNo": m.payout_no,
        "suggestedProbability": m.suggested_probability,
    }
