import secrets

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from _relayer_helpers import make_settings, order_body, relayer_env


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_siwe_and_create_market(client):
    acct = Account.create()
    addr = acct.address
    n = await client.get(f"/api/v1/auth/nonce/{addr}")
    assert n.status_code == 200
    nonce = n.json()["nonce"]
    message = f"login {addr} nonce {nonce}"
    signed = acct.sign_message(encode_defunct(text=message))
    r = await client.post(
        "/api/v1/auth/siwe",
        json={"address": addr, "signature": "0x" + signed.signature.hex().removeprefix("0x"), "message": message},
    )
    assert r.status_code == 200
    token = r.json()["token"]

    listed = await client.get("/api/v1/markets")
    assert listed.status_code == 200

    onramp = await client.get(f"/api/v1/ramps/onramp-url?address={addr}")
    assert onramp.status_code == 200
    assert "coinbase" in onramp.json()["url"] or onramp.json()["provider"] == "coinbase"

    nav = await client.get("/api/v1/fee-vault/nav")
    assert nav.status_code == 200
    assert "nav" in nav.json()

    # Orders are EIP-712 verified at the edge (OU-T003); sign a real one against a pinned Exchange domain.
    cid = "0x" + secrets.token_hex(32)
    order = order_body(acct, cid=cid, is_buy=True, price=500_000, amount=1_000_000)
    with relayer_env(make_settings(), chain=None):
        posted = await client.post("/api/v1/orders", headers={"Authorization": f"Bearer {token}"}, json=order)
    assert posted.status_code == 200, posted.text
    book = await client.get(f"/api/v1/orderbook/{cid}")
    assert book.status_code == 200
    assert len(book.json()["bids"]) == 1


class _Call:
    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _FakeCtf:
    class functions:  # noqa: N801
        @staticmethod
        def positionId(condition_id, outcome):
            return _Call(outcome + 1)

        @staticmethod
        def balanceOf(owner, position_id):
            return _Call(7_000_000 if position_id == 1 else 0)


class _FakeWeb3:
    providers: list[tuple[str, dict]] = []
    contract_factory = staticmethod(lambda address, abi: _FakeCtf())

    @staticmethod
    def HTTPProvider(url, **kw):  # noqa: N802
        _FakeWeb3.providers.append((url, kw))
        return url

    to_checksum_address = staticmethod(lambda a: a)

    def __init__(self, provider):
        self.eth = type("Eth", (), {"contract": staticmethod(type(self).contract_factory)})()

    def is_connected(self):
        return True


@pytest.mark.asyncio
async def test_portfolio_structure(client, monkeypatch):
    import web3
    import app.contract_addresses as ca
    from app.db import SessionLocal
    from app.models import Market

    cid = "0x" + "9e" * 32
    async with SessionLocal() as s:
        if await s.get(Market, cid) is None:
            s.add(Market(condition_id=cid, question="Portfolio probe?", close_time=2_000_000_000))
            await s.commit()
    monkeypatch.setattr(ca, "get_contract_addresses", lambda chain_id=None: {"ConditionalTokens": "0x" + "33" * 20})
    monkeypatch.setattr(web3, "Web3", _FakeWeb3)
    _FakeWeb3.providers.clear()
    addr = "0x" + "22" * 20
    try:
        r = await client.get(f"/api/v1/portfolio/{addr}")
    finally:
        async with SessionLocal() as s:
            row = await s.get(Market, cid)
            if row is not None:
                await s.delete(row)
                await s.commit()
    assert r.status_code == 200
    data = r.json()
    assert data["address"] == addr
    assert {"question": "Portfolio probe?", "side": "YES", "sizeMicros": 7_000_000, "conditionId": cid, "outcome": 0} in data["positions"]
    assert all(p["side"] == "YES" for p in data["positions"] if p["conditionId"] == cid)
    assert isinstance(data["openOrders"], list)
    assert isinstance(data["trades"], list)
    # Bounded RPC like the AMM quote: request timeout, no web3 retry loop.
    _, kwargs = _FakeWeb3.providers[-1]
    assert kwargs == {"request_kwargs": {"timeout": 10.0}, "exception_retry_configuration": None}


@pytest.mark.asyncio
async def test_portfolio_fails_closed_without_ctf(client):
    r = await client.get(f"/api/v1/portfolio/0x{'22' * 20}")
    assert r.status_code == 503


class _PortfolioProbe:
    """Seed one market and point the portfolio router at a fake CTF built by `contract`."""

    def __init__(self, monkeypatch, contract):
        import web3
        import app.contract_addresses as ca

        self.cid = "0x" + "9f" * 32
        fake = type("_ProbeWeb3", (_FakeWeb3,), {"contract_factory": staticmethod(lambda address, abi: contract())})
        monkeypatch.setattr(ca, "get_contract_addresses", lambda chain_id=None: {"ConditionalTokens": "0x" + "33" * 20})
        monkeypatch.setattr(web3, "Web3", fake)

    async def __aenter__(self):
        from app.db import SessionLocal
        from app.models import Market

        async with SessionLocal() as s:
            if await s.get(Market, self.cid) is None:
                s.add(Market(condition_id=self.cid, question="Portfolio RPC probe?", close_time=2_000_000_000))
                await s.commit()
        return self

    async def __aexit__(self, *exc):
        from app.db import SessionLocal
        from app.models import Market

        async with SessionLocal() as s:
            row = await s.get(Market, self.cid)
            if row is not None:
                await s.delete(row)
                await s.commit()


@pytest.mark.asyncio
async def test_portfolio_rpc_error_is_503_without_leaking_url(client, monkeypatch, caplog):
    class _Broken:
        class functions:  # noqa: N801
            @staticmethod
            def positionId(condition_id, outcome):
                raise ConnectionError("HTTPSConnectionPool(host='rpc.example', url=/v2/SECRETKEY)")

    async with _PortfolioProbe(monkeypatch, _Broken):
        r = await client.get(f"/api/v1/portfolio/0x{'22' * 20}")
    assert r.status_code == 503
    assert r.json() == {"detail": "Failed to query CTF balances"}
    assert "SECRETKEY" not in r.text and "SECRETKEY" not in caplog.text


@pytest.mark.asyncio
async def test_slow_portfolio_rpc_does_not_block_the_event_loop(client, monkeypatch):
    """CTF eth_calls run in a worker thread: /health answers while a portfolio read is stuck on RPC."""
    import asyncio
    import time as _time

    class _Slow:
        def __init__(self, value):
            self._value = value

        def call(self):
            _time.sleep(0.3)
            return self._value

    class _SlowCtf:
        class functions:  # noqa: N801
            @staticmethod
            def positionId(condition_id, outcome):
                return _Slow(outcome + 1)

            @staticmethod
            def balanceOf(owner, position_id):
                return _Call(3_000_000 if position_id == 2 else 0)

    async with _PortfolioProbe(monkeypatch, _SlowCtf) as probe:
        started = _time.monotonic()
        task = asyncio.create_task(client.get(f"/api/v1/portfolio/0x{'22' * 20}"))
        await asyncio.sleep(0.1)
        health = await client.get("/health")
        health_done = _time.monotonic() - started
        r = await task
    assert health.status_code == 200
    assert health_done < 0.3
    assert r.status_code == 200
    assert {"question": "Portfolio RPC probe?", "side": "NO", "sizeMicros": 3_000_000,
            "conditionId": probe.cid, "outcome": 1} in r.json()["positions"]


@pytest.mark.asyncio
async def test_fee_vault_nav_reads_the_vault_and_falls_back_when_rpc_fails(client, monkeypatch):
    import web3
    import app.contract_addresses as ca

    class _Vault:
        class functions:  # noqa: N801
            @staticmethod
            def nav():
                return _Call(1_050_000)

    class _DownVault:
        class functions:  # noqa: N801
            @staticmethod
            def nav():
                raise TimeoutError("read timed out")

    monkeypatch.setattr(ca, "get_contract_addresses", lambda chain_id=None: {"FeeVault": "0x" + "44" * 20})
    for contract, expected in ((_Vault, {"nav": 1_050_000}), (_DownVault, {"nav": 0, "simulated": True})):
        fake = type("_VaultWeb3", (_FakeWeb3,), {"contract_factory": staticmethod(lambda address, abi, c=contract: c())})
        monkeypatch.setattr(web3, "Web3", fake)
        _FakeWeb3.providers.clear()
        r = await client.get("/api/v1/fee-vault/nav")
        assert r.status_code == 200
        assert {k: r.json()[k] for k in expected} == expected
        assert ("simulated" in r.json()) == ("simulated" in expected)
        assert _FakeWeb3.providers[-1][1]["request_kwargs"] == {"timeout": 10.0}
