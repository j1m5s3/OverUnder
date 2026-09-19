from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.router import get_current_user, require_operator
from app.config import get_settings

router = APIRouter(prefix="/emissions", tags=["emissions"])
settings = get_settings()


class Recipient(BaseModel):
    address: str
    amount: str


class DistributeRequest(BaseModel):
    program: int
    recipients: list[Recipient]


@router.post("/distribute")
async def distribute(req: DistributeRequest, user: dict = Depends(require_operator)):
    """Distribute OU emissions from treasury to recipients.
    
    Operator-only. Calls EmissionsDistributor.distribute which transfers from treasury.
    Never mints. Program IDs: 0=LP, 1=maker, 2=agent, 3=quest.
    """
    if not settings.emissions_distributor_address:
        raise HTTPException(status_code=503, detail="Emissions distributor not configured")

    if not settings.ou_token_address or not settings.relayer_private_key:
        raise HTTPException(status_code=503, detail="OU token or relayer not configured")

    if req.program not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="Invalid program ID (must be 0-3)")

    if len(req.recipients) == 0:
        raise HTTPException(status_code=400, detail="No recipients provided")

    if len(req.recipients) > 100:
        raise HTTPException(status_code=400, detail="Too many recipients (max 100)")

    # Prepare contract call
    try:
        from eth_account import Account
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(settings.anvil_rpc_url))
        account = Account.from_key(settings.relayer_private_key)

        # Load EmissionsDistributor ABI
        distributor_address = w3.to_checksum_address(settings.emissions_distributor_address)

        # Build arrays
        recipient_addresses = [w3.to_checksum_address(r.address) for r in req.recipients]
        amounts = [int(r.amount) for r in req.recipients]

        # Simple ABI for distribute call
        # distribute(uint256,address[],uint256[])
        from eth_abi import encode

        # Function selector for distribute(uint256,address[],uint256[])
        func_selector = Web3.keccak(text="distribute(uint256,address[],uint256[])")[:4]

        # Encode parameters
        encoded_params = encode(
            ["uint256", "address[]", "uint256[]"], [req.program, recipient_addresses, amounts]
        )

        data = func_selector + encoded_params

        # Build and send transaction
        nonce = w3.eth.get_transaction_count(account.address)
        tx = {
            "from": account.address,
            "to": distributor_address,
            "data": data,
            "nonce": nonce,
            "gas": 500000,
            "gasPrice": w3.eth.gas_price,
            "chainId": settings.chain_id,
        }

        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)

        return {
            "txHash": tx_hash.hex(),
            "program": req.program,
            "recipientCount": len(req.recipients),
            "totalAmount": sum(amounts),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Distribution failed: {str(e)}")
