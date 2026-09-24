import boa
import pytest

from tests.conftest import deploy_protocol


@pytest.fixture
def proto():
    return deploy_protocol()


def test_primary_seed_creates_pool(proto):
    usdc = proto["usdc"]
    factory = proto["factory"]
    amm = proto["amm"]
    operator = proto["accounts"]["operator"]
    seed = 50_000_000
    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator.address):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        cid = factory.createPrimaryMarket(b"\x71" * 32, close, "Seeded primary", seed)
    pool = amm.pools(cid)
    assert pool[3] is True
    assert pool[0] == seed
    assert pool[1] == seed
    # seedPoolFor credits the operator, not the factory, so the seed can be withdrawn after close.
    assert amm.lpBalance(cid, operator.address) == seed
    assert amm.lpBalance(cid, factory.address) == 0
    assert usdc.balanceOf(factory.address) == 0


def test_operator_withdraws_primary_seed_after_close(proto):
    usdc, factory, amm = proto["usdc"], proto["factory"], proto["amm"]
    operator = proto["accounts"]["operator"].address
    seed = 20_000_000
    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        cid = factory.createPrimaryMarket(b"\x74" * 32, close, "Withdrawable primary", seed)
        # Operator seeds are locked like listing seeds until closeTime.
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, 1)
    boa.env.time_travel(seconds=close - boa.env.timestamp)
    min_lp = amm.MIN_LP()
    with boa.env.prank(operator):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, seed)
        paid = amm.removeLiquidity(cid, seed - min_lp)
    assert paid == seed - min_lp
    assert usdc.balanceOf(operator) == seed - min_lp
    assert amm.lpBalance(cid, operator) == min_lp
    assert amm.pools(cid)[2] == min_lp


def test_wildcard_seed_lp_to_generator(proto):
    usdc, factory, amm = proto["usdc"], proto["factory"], proto["amm"]
    operator = proto["accounts"]["operator"].address
    generator = proto["accounts"]["generator"].address
    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator):
        usdc.faucet(10_000_000)
        usdc.approve(factory.address, 10_000_000)
        parent = factory.createPrimaryMarket(b"\x75" * 32, close, "Parent", 10_000_000)
    with boa.env.prank(generator):
        usdc.faucet(5_000_000)
        usdc.approve(factory.address, 5_000_000)
        child = factory.createWildcardMarket(b"\x76" * 32, parent, close, "Child", 5_000_000)
    assert amm.lpBalance(child, generator) == 5_000_000
    assert amm.lpBalance(child, factory.address) == 0


def test_primary_zero_seed_reverts(proto):
    factory = proto["factory"]
    operator = proto["accounts"]["operator"].address
    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator):
        with boa.reverts("seed required"):
            factory.createPrimaryMarket(b"\x72" * 32, close, "Unseeded primary", 0)


def test_non_operator_cannot_create_primary(proto):
    factory = proto["factory"]
    trader = proto["accounts"]["trader_a"].address
    close = boa.env.timestamp + 10_000
    with boa.env.prank(trader):
        with boa.reverts("not operator"):
            factory.createPrimaryMarket(b"\x73" * 32, close, "Nope", 1_000_000)
