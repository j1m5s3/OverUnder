"""Collect 3 attestations. run() does not submitConsensus or submitAttestation."""

from __future__ import annotations

import os
import time

from eth_account import Account

from agents.alpha import AlphaAgent
from agents.beta import BetaAgent
from agents.gamma import GammaAgent
from consensus.eip712 import sign_attestation

CONSENSUS_DEADLINE_SECONDS = 3600


def _sign(private_key: str, oracle: str, chain_id: int, condition_id: bytes, outcome: int, evidence_hash: bytes, deadline: int) -> bytes:
    return sign_attestation(private_key, oracle, chain_id, condition_id, outcome, evidence_hash, deadline)


class Coordinator:
    def __init__(self, agents=None):
        self.agents = agents or [AlphaAgent(), BetaAgent(), GammaAgent()]
        self.keys = {
            "alpha": os.getenv("AGENT_ALPHA_KEY", "").strip(),
            "beta": os.getenv("AGENT_BETA_KEY", "").strip(),
            "gamma": os.getenv("AGENT_GAMMA_KEY", "").strip(),
        }

    def run(
        self, question: str, context: str | None = None, as_of: str | None = None, kickoff: str | None = None
    ) -> dict:
        """`context` is untrusted data fenced in the prompt; `as_of` and `kickoff` are trusted job guidance.

        `kickoff` (sports) pins research to the game on that date: a live agent whose
        game_date does not match reports outcome 2. Outcome 2 means an agent could not
        verify a final result (undetermined).
        """
        extra = {k: v for k, v in (("context", context), ("as_of", as_of), ("kickoff", kickoff)) if v is not None}
        reports = [(a.name, a.research(question, **extra)) for a in self.agents]
        outcomes = {name: att.outcome for name, att in reports}
        unanimous = len(set(outcomes.values())) == 1
        return {
            "unanimous": unanimous,
            "outcome": reports[0][1].outcome if unanimous else None,
            "reports": [
                {
                    "agent": name,
                    "outcome": att.outcome,
                    "confidence": float(att.confidence),
                    "summary": att.summary,
                    "evidenceHash": "0x" + att.evidence_hash.hex(),
                }
                for name, att in reports
            ],
        }

    def _key(self, name: str) -> str:
        key = self.keys.get(name) or ""
        if not key:
            raise RuntimeError(f"missing key for {name}")
        return key

    def agent_address(self, name: str) -> str:
        return Account.from_key(self._key(name)).address

    def sign_one(self, name: str, oracle: str, chain_id: int, condition_id: bytes, evidence_hash: bytes, outcome: int, deadline: int) -> bytes:
        """One agent's EIP-712 attestation for submitAttestation (ADR-0002 fallback)."""
        return _sign(self._key(name), oracle, chain_id, condition_id, outcome, evidence_hash, deadline)

    def sign_unanimous(
        self,
        oracle: str,
        chain_id: int,
        condition_id: bytes,
        evidence_hash: bytes,
        outcome: int,
        now: int | None = None,
    ) -> tuple[int, list[bytes]]:
        """Three signatures for submitConsensus; `now` is the chain clock (defaults to wall time)."""
        deadline = int(now if now is not None else time.time()) + CONSENSUS_DEADLINE_SECONDS
        sigs = []
        for name in ("alpha", "beta", "gamma"):
            sigs.append(_sign(self._key(name), oracle, chain_id, condition_id, outcome, evidence_hash, deadline))
        return deadline, sigs
