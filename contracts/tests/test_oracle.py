import boa
import pytest

from tests.conftest import deploy_protocol
from tests.eip712 import sign_attestation


@pytest.fixture
def proto():
    return deploy_protocol()


def _primary(proto, close_delta=10):
    usdc = proto["usdc"]
    factory = proto["factory"]
    operator = proto["accounts"]["operator"].address
    close = boa.env.timestamp + close_delta
    with boa.env.prank(operator):
        usdc.faucet(10_000_000)
        usdc.approve(factory.address, 10_000_000)
        cid = factory.createPrimaryMarket(b"\x51" * 32, close, "Who won", 10_000_000)
    return cid, close


def _sigs(proto, cid, outcome, evidence, deadline):
    oracle = proto["oracle"]
    chain = proto["chain_id"]
    sigs = []
    for name in ("alpha", "beta", "gamma"):
        sigs.append(
            sign_attestation(
                proto["accounts"][name].key,
                oracle.address,
                chain,
                cid,
                outcome,
                evidence,
                deadline,
            )
        )
    return sigs


def test_unanimous_consensus_resolves(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    oracle = proto["oracle"]
    user = proto["accounts"]["trader_a"]
    cid, close = _primary(proto, close_delta=5)

    with boa.env.prank(user.address):
        usdc.faucet(50)
        usdc.approve(ctf.address, 50)
        ctf.splitPosition(cid, 50)

    boa.env.time_travel(seconds=6)
    evidence = b"\xab" * 32
    deadline = boa.env.timestamp + 1000
    sigs = _sigs(proto, cid, 0, evidence, deadline)
    oracle.submitConsensus(cid, 0, evidence, deadline, sigs)
    assert oracle.resolved(cid)
    assert ctf.payoutDenominator(cid) == 1
    assert ctf.payoutNumerators(cid, 0) == 1


def test_fallback_majority_and_vote(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    oracle = proto["oracle"]
    user = proto["accounts"]["trader_a"]
    cid, close = _primary(proto, close_delta=5)

    with boa.env.prank(user.address):
        usdc.faucet(80)
        usdc.approve(ctf.address, 80)
        ctf.splitPosition(cid, 80)

    boa.env.time_travel(seconds=6)
    evidence = b"\xcd" * 32
    deadline = boa.env.timestamp + 10_000
    chain = proto["chain_id"]
    for name, outcome in (("alpha", 0), ("beta", 0), ("gamma", 1)):
        sig = sign_attestation(
            proto["accounts"][name].key,
            oracle.address,
            chain,
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
    assert ctf.payoutNumerators(cid, 0) == 1


def test_opposing_vote_requires_arbitration(proto):
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    oracle = proto["oracle"]
    user = proto["accounts"]["trader_a"]
    operator = proto["accounts"]["operator"]
    cid, _ = _primary(proto, close_delta=5)

    with boa.env.prank(user.address):
        usdc.faucet(80)
        usdc.approve(ctf.address, 80)
        ctf.splitPosition(cid, 80)

    boa.env.time_travel(seconds=6)
    evidence = b"\xef" * 32
    deadline = boa.env.timestamp + 10_000
    chain = proto["chain_id"]
    for name, outcome in (("alpha", 0), ("beta", 0), ("gamma", 1)):
        sig = sign_attestation(
            proto["accounts"][name].key,
            oracle.address,
            chain,
            cid,
            outcome,
            evidence,
            deadline,
        )
        oracle.submitAttestation(cid, outcome, evidence, deadline, sig)

    with boa.env.prank(user.address):
        oracle.castVote(cid, 1)

    boa.env.time_travel(seconds=86_400)
    with boa.reverts():
        oracle.resolveFallback(cid)
    with boa.env.prank(operator.address):
        oracle.resolveArbitrated(cid, 1)
    assert ctf.payoutNumerators(cid, 1) == 1
