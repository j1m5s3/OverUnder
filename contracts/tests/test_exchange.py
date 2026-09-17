import boa
import pytest

from tests.conftest import deploy_protocol
from tests.eip712 import sign_order


@pytest.fixture
def proto():
    return deploy_protocol()


def _order(maker, is_buy, cid, price, amount, expiry, nonce=0, salt=1, outcome=0):
    return {
        "maker": maker,
        "isBuy": is_buy,
        "conditionId": cid,
        "outcome": outcome,
        "price": price,
        "amount": amount,
        "salt": salt,
        "nonce": nonce,
        "expiry": expiry,
    }


def _tup(o: dict):
    return (
        o["maker"],
        o["isBuy"],
        o["conditionId"],
        o["outcome"],
        o["price"],
        o["amount"],
        o["salt"],
        o["nonce"],
        o["expiry"],
    )


def test_clob_fill_takes_taker_fee(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    factory = proto["factory"]
    exchange = proto["exchange"]
    vault = proto["vault"]
    seller = proto["accounts"]["trader_a"]
    buyer = proto["accounts"]["trader_b"]
    operator = proto["accounts"]["operator"].address

    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator):
        cid = factory.createPrimaryMarket(b"\x33" * 32, close, "Chiefs win")

    amount = 1_000_000
    price = 1_000_000
    with boa.env.prank(seller.address):
        usdc.faucet(amount)
        usdc.approve(ctf.address, amount)
        ctf.splitPosition(cid, amount)
        ctf.setApprovalForAll(exchange.address, True)

    with boa.env.prank(buyer.address):
        usdc.faucet(2_000_000)
        usdc.approve(exchange.address, 2_000_000)

    expiry = boa.env.timestamp + 5000
    sell = _order(seller.address, False, cid, price, amount, expiry, salt=1)
    buy = _order(buyer.address, True, cid, price, amount, expiry, salt=2)
    sell_sig = sign_order(seller.key, exchange.address, proto["chain_id"], sell)
    buy_sig = sign_order(buyer.key, exchange.address, proto["chain_id"], buy)

    fill = 1_000_000
    exchange.matchOrders(_tup(buy), _tup(sell), fill, buy_sig, sell_sig)

    volume = fill * price // 1_000_000
    fee = volume * 75 // 10_000
    yes_id = ctf.positionId(cid, 0)
    assert ctf.balanceOf(buyer.address, yes_id) == fill
    assert usdc.balanceOf(seller.address) == volume
    assert usdc.balanceOf(vault.address) == fee
    assert usdc.balanceOf(buyer.address) == 2_000_000 - volume - fee


def test_cancel_order(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    factory = proto["factory"]
    exchange = proto["exchange"]
    seller = proto["accounts"]["trader_a"]
    buyer = proto["accounts"]["trader_b"]
    operator = proto["accounts"]["operator"].address
    close = boa.env.timestamp + 10_000
    with boa.env.prank(operator):
        cid = factory.createPrimaryMarket(b"\x34" * 32, close, "Cancel me")

    with boa.env.prank(seller.address):
        usdc.faucet(10)
        usdc.approve(ctf.address, 10)
        ctf.splitPosition(cid, 10)
        ctf.setApprovalForAll(exchange.address, True)
    with boa.env.prank(buyer.address):
        usdc.faucet(20)
        usdc.approve(exchange.address, 20)

    expiry = boa.env.timestamp + 5000
    sell = _order(seller.address, False, cid, 1_000_000, 10, expiry)
    buy = _order(buyer.address, True, cid, 1_000_000, 10, expiry, salt=9)
    with boa.env.prank(seller.address):
        exchange.cancelOrder(_tup(sell))
    buy_sig = sign_order(buyer.key, exchange.address, proto["chain_id"], buy)
    sell_sig = sign_order(seller.key, exchange.address, proto["chain_id"], sell)
    with boa.reverts():
        exchange.matchOrders(_tup(buy), _tup(sell), 10, buy_sig, sell_sig)
