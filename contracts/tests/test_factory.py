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
    assert amm.lpBalance(cid, factory.address) == seed


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
