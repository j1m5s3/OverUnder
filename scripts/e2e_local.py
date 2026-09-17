#!/usr/bin/env python3
"""Local happy-path + fallback e2e against in-process boa (no Anvil required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
sys.path.insert(0, str(CONTRACTS))
sys.path.insert(0, str(ROOT / "oracles"))

import boa

from tests.conftest import COOLDOWN, deploy_protocol
from tests.eip712 import sign_attestation, sign_order
from consensus.coordinator import Coordinator
from agents.alpha import AlphaAgent
from agents.beta import BetaAgent
from agents.gamma import GammaAgent
from agents.base import MockSearch
from wildcard.generator import propose


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


def happy_path(proto) -> dict:
    usdc, ctf, factory, exchange, amm, oracle, vault, ou = (
        proto["usdc"],
        proto["ctf"],
        proto["factory"],
        proto["exchange"],
        proto["amm"],
        proto["oracle"],
        proto["vault"],
        proto["ou"],
    )
    operator = proto["accounts"]["operator"]
    generator = proto["accounts"]["generator"]
    seller = proto["accounts"]["trader_a"]
    buyer = proto["accounts"]["trader_b"]
    treasury = proto["accounts"]["treasury"]

    close = boa.env.timestamp + 60
    with boa.env.prank(operator.address):
        parent = factory.createPrimaryMarket(b"\xaa" * 32, close, "Chiefs vs Broncos")

    kids = propose("Chiefs vs Broncos", close_time=close)
    seed = 200_000_000
    with boa.env.prank(generator.address):
        usdc.faucet(seed)
        usdc.approve(factory.address, seed)
        child = factory.createWildcardMarket(
            b"\xbb" * 32, parent, close, kids[0].question[:256], seed
        )

    amount = 1_000_000
    price = 500_000
    with boa.env.prank(seller.address):
        usdc.faucet(amount)
        usdc.approve(ctf.address, amount)
        ctf.splitPosition(parent, amount)
        ctf.setApprovalForAll(exchange.address, True)
    with boa.env.prank(buyer.address):
        usdc.faucet(2_000_000)
        usdc.approve(exchange.address, 2_000_000)

    expiry = boa.env.timestamp + 30
    sell = {
        "maker": seller.address,
        "isBuy": False,
        "conditionId": parent,
        "outcome": 0,
        "price": price,
        "amount": amount,
        "salt": 1,
        "nonce": 0,
        "expiry": expiry,
    }
    buy = dict(sell)
    buy["maker"] = buyer.address
    buy["isBuy"] = True
    buy["salt"] = 2
    exchange.matchOrders(
        _tup(buy),
        _tup(sell),
        amount,
        sign_order(buyer.key, exchange.address, proto["chain_id"], buy),
        sign_order(seller.key, exchange.address, proto["chain_id"], sell),
    )

    with boa.env.prank(buyer.address):
        usdc.faucet(5_000_000)
        usdc.approve(amm.address, 5_000_000)
        amm.buyWithUSDC(child, True, 5_000_000, 0)

    search = MockSearch(["Chiefs defeated Broncos. Kelce fumbled once."])
    result = Coordinator(
        agents=[AlphaAgent(search=search), BetaAgent(search=search), GammaAgent(search=search)]
    ).run("Who won Chiefs vs Broncos?")
    assert result["unanimous"]

    boa.env.time_travel(seconds=61)
    evidence = bytes.fromhex(result["reports"][0]["evidenceHash"][2:])
    deadline = boa.env.timestamp + 1000
    sigs = [
        sign_attestation(
            proto["accounts"][name].key,
            oracle.address,
            proto["chain_id"],
            parent,
            0,
            evidence,
            deadline,
        )
        for name in ("alpha", "beta", "gamma")
    ]
    oracle.submitConsensus(parent, 0, evidence, deadline, sigs)

    yes_id = ctf.positionId(parent, 0)
    with boa.env.prank(buyer.address):
        bal = ctf.balanceOf(buyer.address, yes_id)
        ctf.redeemPositions(parent, 0, bal)

    gift = 10**18
    with boa.env.prank(treasury.address):
        ou.transfer(buyer.address, gift)
    with boa.env.prank(buyer.address):
        ou.approve(vault.address, gift)
        vault.requestRedeem(gift)
        boa.env.time_travel(seconds=COOLDOWN)
        vault.claim()

    return {
        "parent": "0x" + parent.hex(),
        "wildcard": "0x" + child.hex(),
        "vaultUsdc": usdc.balanceOf(vault.address),
        "nav": vault.nav(),
        "buyerUsdc": usdc.balanceOf(buyer.address),
    }


def fallback_path(proto) -> None:
    usdc, ctf, factory, oracle = proto["usdc"], proto["ctf"], proto["factory"], proto["oracle"]
    operator = proto["accounts"]["operator"]
    user = proto["accounts"]["trader_a"]
    close = boa.env.timestamp + 5
    with boa.env.prank(operator.address):
        cid = factory.createPrimaryMarket(b"\xcc" * 32, close, "Fallback market")
    with boa.env.prank(user.address):
        usdc.faucet(50)
        usdc.approve(ctf.address, 50)
        ctf.splitPosition(cid, 50)
    boa.env.time_travel(seconds=6)
    evidence = b"\x11" * 32
    deadline = boa.env.timestamp + 1000
    for name, outcome in (("alpha", 0), ("beta", 0), ("gamma", 1)):
        sig = sign_attestation(
            proto["accounts"][name].key,
            oracle.address,
            proto["chain_id"],
            cid,
            outcome,
            evidence,
            deadline,
        )
        oracle.submitAttestation(cid, outcome, evidence, deadline, sig)
    with boa.env.prank(user.address):
        oracle.castVote(cid, 0)
    boa.env.time_travel(seconds=86_400)
    oracle.resolveFallback(cid)
    assert oracle.resolved(cid)


def main() -> None:
    import os

    os.chdir(CONTRACTS)
    proto = deploy_protocol()
    summary = happy_path(proto)
    fallback_path(proto)
    out = CONTRACTS / "deployments" / "31337.json"
    out.parent.mkdir(exist_ok=True)
    payload = {
        "chainId": 31337,
        "MockUSDC": proto["usdc"].address,
        "RevenueToken": proto["ou"].address,
        "ConditionalTokens": proto["ctf"].address,
        "FeeVault": proto["vault"].address,
        "ConsensusOracle": proto["oracle"].address,
        "Exchange": proto["exchange"].address,
        "MarketAMM": proto["amm"].address,
        "MarketFactory": proto["factory"].address,
        "e2e": summary,
    }
    out.write_text(json.dumps(payload, indent=2))
    print("E2E OK")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
