"""EIP-712 order digest and signature checks against Exchange.vy semantics."""

import os

import pytest
from eth_account import Account

from app.orderbook.eip712 import (
    BadOrderSignature,
    OrderFields,
    order_digest,
    order_hash_hex,
    recover_order_signer,
    u256,
    verify_order_signature,
)
from _relayer_helpers import CHAIN, EXCHANGE, contracts_dir, sign_order

CID = b"\xab" * 32


def _fields(acct, **kw) -> tuple[OrderFields, dict]:
    raw = {
        "maker": acct.address,
        "isBuy": True,
        "conditionId": CID,
        "outcome": 0,
        "price": 500_000,
        "amount": 10**12,
        "salt": 2**255 + 7,
        "nonce": 0,
        "expiry": 1_999_999_999,
    }
    raw.update(kw)
    of = OrderFields(
        maker=raw["maker"], is_buy=raw["isBuy"], condition_id=raw["conditionId"], outcome=raw["outcome"],
        price=raw["price"], amount=raw["amount"], salt=raw["salt"], nonce=raw["nonce"], expiry=raw["expiry"],
    )
    return of, raw


def test_digest_matches_contract_hashOrder():
    boa = pytest.importorskip("boa")
    path = os.path.join(contracts_dir(), "src", "Exchange.vy")
    ex = boa.load(path, "0x" + "01" * 20, "0x" + "02" * 20, "0x" + "03" * 20, "0x" + "04" * 20)
    chain_id = boa.env.evm.patch.chain_id
    acct = Account.create()
    of, _ = _fields(acct)
    onchain = ex.hashOrder(of.as_tuple())
    assert order_digest(of, ex.address, chain_id) == onchain
    assert order_hash_hex(of, ex.address, chain_id) == "0x" + onchain.hex()
    sell, _ = _fields(acct, isBuy=False, outcome=1, salt=1, amount=5_000_000_000, nonce=3)
    assert order_digest(sell, ex.address, chain_id) == ex.hashOrder(sell.as_tuple())


def test_verify_accepts_valid_sig():
    acct = Account.create()
    of, raw = _fields(acct)
    sig = sign_order(acct.key, EXCHANGE, CHAIN, raw)
    digest = verify_order_signature(of, sig, EXCHANGE, CHAIN)
    assert digest == order_digest(of, EXCHANGE, CHAIN)
    assert recover_order_signer(digest, sig) == acct.address


def test_verify_accepts_v_normalized_to_0_1():
    acct = Account.create()
    of, raw = _fields(acct)
    sig = bytearray(bytes.fromhex(sign_order(acct.key, EXCHANGE, CHAIN, raw)[2:]))
    assert sig[64] in (27, 28)
    sig[64] -= 27
    verify_order_signature(of, bytes(sig), EXCHANGE, CHAIN)
    verify_order_signature(of, "0x" + bytes(sig).hex(), EXCHANGE, CHAIN)


def test_rejects_tampered_field():
    acct = Account.create()
    of, raw = _fields(acct)
    sig = sign_order(acct.key, EXCHANGE, CHAIN, raw)
    tampered, _ = _fields(acct, price=500_001)
    with pytest.raises(BadOrderSignature):
        verify_order_signature(tampered, sig, EXCHANGE, CHAIN)


def test_rejects_wrong_chain():
    acct = Account.create()
    of, raw = _fields(acct)
    sig = sign_order(acct.key, EXCHANGE, 84532, raw)
    with pytest.raises(BadOrderSignature):
        verify_order_signature(of, sig, EXCHANGE, CHAIN)


def test_rejects_wrong_verifying_contract():
    acct = Account.create()
    of, raw = _fields(acct)
    sig = sign_order(acct.key, "0x" + "09" * 20, CHAIN, raw)
    with pytest.raises(BadOrderSignature):
        verify_order_signature(of, sig, EXCHANGE, CHAIN)


def test_rejects_other_signer():
    acct, other = Account.create(), Account.create()
    of, raw = _fields(acct)
    sig = sign_order(other.key, EXCHANGE, CHAIN, raw)
    with pytest.raises(BadOrderSignature):
        verify_order_signature(of, sig, EXCHANGE, CHAIN)


@pytest.mark.parametrize("sig", ["0x", "0x00", "0x" + "11" * 64, "0x" + "11" * 66, "not-hex", "0xzz" + "00" * 64])
def test_rejects_short_or_empty_sig(sig):
    acct = Account.create()
    of, _ = _fields(acct)
    with pytest.raises(BadOrderSignature):
        verify_order_signature(of, sig, EXCHANGE, CHAIN)


@pytest.mark.parametrize("v", [29, 35, 36, 2])
def test_rejects_v_29_and_v_35(v):
    acct = Account.create()
    of, raw = _fields(acct)
    sig = bytearray(bytes.fromhex(sign_order(acct.key, EXCHANGE, CHAIN, raw)[2:]))
    sig[64] = v
    with pytest.raises(BadOrderSignature):
        verify_order_signature(of, bytes(sig), EXCHANGE, CHAIN)


def test_rejects_zero_r_s():
    acct = Account.create()
    of, _ = _fields(acct)
    with pytest.raises(BadOrderSignature):
        verify_order_signature(of, b"\x00" * 64 + b"\x1b", EXCHANGE, CHAIN)


def test_u256_parsing():
    assert u256(5) == 5
    assert u256("12") == 12
    assert u256("0x10") == 16
    assert u256(hex(2**255 + 7)) == 2**255 + 7
    for bad in (-1, 2**256, "", "0xg", True, 1.5):
        with pytest.raises(ValueError):
            u256(bad)
