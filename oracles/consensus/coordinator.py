"""Collect 3 attestations and submitConsensus when unanimous."""

from __future__ import annotations

import os
import time
from pathlib import Path

from eth_account import Account

from agents.alpha import AlphaAgent
from agents.beta import BetaAgent
from agents.gamma import GammaAgent

ROOT = Path(__file__).resolve().parents[2]


def _sign(private_key: str, oracle: str, chain_id: int, condition_id: bytes, outcome: int, evidence_hash: bytes, deadline: int) -> bytes:
    import sys

    sys.path.append(str(ROOT / "contracts" / "tests"))
    from eip712 import sign_attestation

    return sign_attestation(private_key, oracle, chain_id, condition_id, outcome, evidence_hash, deadline)


class Coordinator:
    def __init__(self, agents=None):
        self.agents = agents or [AlphaAgent(), BetaAgent(), GammaAgent()]
        self.keys = {
            "alpha": os.getenv("AGENT_ALPHA_KEY", ""),
            "beta": os.getenv("AGENT_BETA_KEY", ""),
            "gamma": os.getenv("AGENT_GAMMA_KEY", ""),
        }

    def run(self, question: str) -> dict:
        reports = [(a.name, a.research(question)) for a in self.agents]
        outcomes = {name: att.outcome for name, att in reports}
        unanimous = len(set(outcomes.values())) == 1
        return {
            "unanimous": unanimous,
            "outcome": reports[0][1].outcome if unanimous else None,
            "reports": [
                {
                    "agent": name,
                    "outcome": att.outcome,
                    "summary": att.summary,
                    "evidenceHash": "0x" + att.evidence_hash.hex(),
                }
                for name, att in reports
            ],
        }

    def sign_unanimous(self, oracle: str, chain_id: int, condition_id: bytes, evidence_hash: bytes, outcome: int) -> list[bytes]:
        deadline = int(time.time()) + 3600
        sigs = []
        for name in ("alpha", "beta", "gamma"):
            key = self.keys[name]
            if not key:
                raise RuntimeError(f"missing key for {name}")
            sigs.append(_sign(key, oracle, chain_id, condition_id, outcome, evidence_hash, deadline))
        return sigs
