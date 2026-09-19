import boa
import pytest

from tests.conftest import deploy_protocol


@pytest.fixture
def proto():
    return deploy_protocol()


def _seeded_wildcard(proto, seed=200_000_000):
    usdc = proto["usdc"]
    factory = proto["factory"]
    operator = proto["accounts"]["operator"].address
    generator = proto["accounts"]["generator"]
    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        parent = factory.createPrimaryMarket(b"\x41" * 32, close, "Chiefs vs Broncos", seed)
    with boa.env.prank(generator.address):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        child = factory.createWildcardMarket(
            b"\x42" * 32, parent, close, "Kelce fumble >= 1", seed
        )
    return parent, child


def test_amm_buy_moves_price_and_splits_fee(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    amm = proto["amm"]
    vault = proto["vault"]
    user = proto["accounts"]["trader_a"]
    _parent, child = _seeded_wildcard(proto)

    pool_before = amm.pools(child)
    usdc_in = 10_000_000
    with boa.env.prank(user.address):
        usdc.faucet(usdc_in)
        usdc.approve(amm.address, usdc_in)
        quoted = amm.quoteBuy(child, True, usdc_in)
        out = amm.buyWithUSDC(child, True, usdc_in, quoted)

    yes_id = ctf.positionId(child, 0)
    assert ctf.balanceOf(user.address, yes_id) == out
    vault_fee = usdc_in * 50 // 10_000
    assert usdc.balanceOf(vault.address) == vault_fee
    pool_after = amm.pools(child)
    assert pool_after[0] < pool_before[0]
    assert pool_after[1] > pool_before[1]


def test_add_liquidity(proto):
    usdc = proto["usdc"]
    amm = proto["amm"]
    user = proto["accounts"]["trader_b"]
    _parent, child = _seeded_wildcard(proto, seed=50_000_000)
    with boa.env.prank(user.address):
        usdc.faucet(10_000_000)
        usdc.approve(amm.address, 10_000_000)
        minted = amm.addLiquidity(child, 10_000_000)
    assert minted > 0
    assert amm.lpBalance(child, user.address) == minted
