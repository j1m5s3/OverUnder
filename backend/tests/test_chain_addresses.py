"""GET /api/v1/chain/addresses: public, Settings-only, the same addresses /aa/cdp-send allowlists."""

from unittest.mock import patch

from web3 import Web3

from app.aa.router import SELECTOR_APPROVE, _validate_call
from app.config import Settings

USDC = "0x" + "a1" * 20
CTF = "0x" + "b2" * 20
AMM = "0x" + "c3" * 20
FACTORY = "0x" + "d4" * 20
ORACLE = "0x" + "e5" * 20
VAULT = "0x" + "f6" * 20
EXCHANGE = "0x" + "07" * 20


def _settings(**kw) -> Settings:
    base = dict(
        chain_id=84532,
        usdc_address=USDC,
        ctf_address=CTF,
        amm_address=AMM,
        factory_address=FACTORY,
        oracle_address=ORACLE,
        fee_vault_address=VAULT,
        exchange_address=EXCHANGE,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


async def test_chain_addresses_serves_settings_checksummed(client):
    s = _settings()
    with patch("app.chain.router.get_settings", return_value=s):
        r = await client.get("/api/v1/chain/addresses")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "chainId", "MockUSDC", "ConditionalTokens", "MarketAMM", "MarketFactory", "ConsensusOracle", "FeeVault", "Exchange",
    }
    assert body["chainId"] == 84532
    expected = {
        "MockUSDC": USDC, "ConditionalTokens": CTF, "MarketAMM": AMM, "MarketFactory": FACTORY,
        "ConsensusOracle": ORACLE, "FeeVault": VAULT, "Exchange": EXCHANGE,
    }
    for key, addr in expected.items():
        assert body[key] == Web3.to_checksum_address(addr)  # EIP-55

    # The served trade targets are exactly what cdp-send sponsors: approving the served AMM on the
    # served USDC passes the allowlist.
    call = SELECTOR_APPROVE + bytes(12) + bytes.fromhex(body["MarketAMM"][2:]) + (2**256 - 1).to_bytes(32, "big")
    assert _validate_call(body["MockUSDC"], call, 0, s)


async def test_chain_addresses_unset_or_invalid_are_null(client):
    s = _settings(amm_address="", factory_address="not-an-address", exchange_address="  ")
    with patch("app.chain.router.get_settings", return_value=s):
        body = (await client.get("/api/v1/chain/addresses")).json()
    assert body["MarketAMM"] is None and body["MarketFactory"] is None and body["Exchange"] is None
    assert body["MockUSDC"].lower() == USDC


async def test_chain_addresses_needs_no_auth_and_ignores_deployment_files(client):
    # conftest pins every *_ADDRESS to "" (the real Settings path, no patch): nothing configured, nothing served,
    # even though a local contracts/deployments/*.json may exist on a dev machine.
    r = await client.get("/api/v1/chain/addresses")
    assert r.status_code == 200
    body = r.json()
    assert body["chainId"] == 999999
    assert all(body[k] is None for k in body if k != "chainId")
