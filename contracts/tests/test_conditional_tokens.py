import boa
import pytest

from tests.conftest import deploy_protocol


@pytest.fixture
def proto():
    return deploy_protocol()


def test_split_merge_redeem(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    factory = proto["factory"]
    operator = proto["accounts"]["operator"].address
    user = proto["accounts"]["trader_a"].address

    close = boa.env.timestamp + 3600
    qid = b"\x11" * 32
    with boa.env.prank(operator):
        usdc.faucet(10_000_000)
        usdc.approve(factory.address, 10_000_000)
        cid = factory.createPrimaryMarket(qid, close, "Chiefs vs Broncos", 10_000_000)

    with boa.env.prank(user):
        usdc.faucet(1_000_000)
        usdc.approve(ctf.address, 1_000_000)
        ctf.splitPosition(cid, 1_000_000)

    yes_id = ctf.positionId(cid, 0)
    no_id = ctf.positionId(cid, 1)
    assert ctf.balanceOf(user, yes_id) == 1_000_000
    assert ctf.balanceOf(user, no_id) == 1_000_000
    assert usdc.balanceOf(user) == 0

    with boa.env.prank(user):
        ctf.mergePositions(cid, 400_000)
    assert usdc.balanceOf(user) == 400_000
    assert ctf.balanceOf(user, yes_id) == 600_000


def test_redeem_after_payout(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    oracle = proto["oracle"]
    factory = proto["factory"]
    operator = proto["accounts"]["operator"].address
    user = proto["accounts"]["trader_a"].address

    close = boa.env.timestamp + 10
    with boa.env.prank(operator):
        usdc.faucet(10_000_000)
        usdc.approve(factory.address, 10_000_000)
        cid = factory.createPrimaryMarket(b"\x22" * 32, close, "Yes or no", 10_000_000)

    with boa.env.prank(user):
        usdc.faucet(100)
        usdc.approve(ctf.address, 100)
        ctf.splitPosition(cid, 100)

    boa.env.time_travel(seconds=11)
    with boa.env.prank(oracle.address):
        ctf.reportPayouts(cid, [1, 0])

    yes_id = ctf.positionId(cid, 0)
    with boa.env.prank(user):
        ctf.redeemPositions(cid, 0, 100)
    assert usdc.balanceOf(user) == 100


def test_primary_requires_seed(proto):
    factory = proto["factory"]
    operator = proto["accounts"]["operator"].address
    close = boa.env.timestamp + 10
    with boa.env.prank(operator):
        with boa.reverts("seed required"):
            factory.createPrimaryMarket(b"\x33" * 32, close, "No seed market", 0)
