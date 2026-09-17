#pragma version 0.4.3

interface ICTF:
    def reportPayouts(conditionId: bytes32, payouts: uint256[2]): nonpayable
    def balanceOf(owner: address, id: uint256) -> uint256: view
    def positionId(conditionId: bytes32, outcome: uint8) -> uint256: view
    def isResolved(conditionId: bytes32) -> bool: view

event AgentSet:
    index: uint8
    account: indexed(address)

event AttestationRecorded:
    conditionId: indexed(bytes32)
    agent: indexed(address)
    outcome: uint8
    evidenceHash: bytes32

event ConsensusSubmitted:
    conditionId: indexed(bytes32)
    outcome: uint8

event VoteCast:
    conditionId: indexed(bytes32)
    voter: indexed(address)
    outcome: uint8
    weight: uint256

event FallbackResolved:
    conditionId: indexed(bytes32)
    outcome: uint8

event Arbitrated:
    conditionId: indexed(bytes32)
    outcome: uint8

WINDOW: public(constant(uint256)) = 86400
ARBITRATION_GRACE: public(constant(uint256)) = 172800

ctf: public(address)
operator: public(address)
factory: public(address)
agents: public(address[3])
isAgent: public(HashMap[address, bool])

closeTime: public(HashMap[bytes32, uint256])
agentSubmitted: public(HashMap[bytes32, HashMap[address, bool]])
agentOutcome: public(HashMap[bytes32, HashMap[address, uint8]])
attestationCount: public(HashMap[bytes32, uint8])
resolved: public(HashMap[bytes32, bool])
voted: public(HashMap[bytes32, HashMap[address, bool]])
voteWeight: public(HashMap[bytes32, uint256[2]])

DOMAIN_TYPEHASH: constant(bytes32) = keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
ATTEST_TYPEHASH: constant(bytes32) = keccak256("Attestation(bytes32 conditionId,uint8 outcome,bytes32 evidenceHash,uint256 deadline)")

@deploy
def __init__(ctf: address, operator: address, agents: address[3]):
    assert ctf != empty(address) and operator != empty(address)
    self.ctf = ctf
    self.operator = operator
    for i: uint256 in range(3):
        a: address = agents[i]
        assert a != empty(address)
        self.agents[i] = a
        self.isAgent[a] = True
        log AgentSet(index=convert(i, uint8), account=a)

@external
def setFactory(factory: address):
    assert msg.sender == self.operator
    self.factory = factory

@external
def registerMarket(conditionId: bytes32, closeTime: uint256):
    assert msg.sender == self.factory or msg.sender == self.operator
    assert closeTime > 0
    assert self.closeTime[conditionId] == 0
    self.closeTime[conditionId] = closeTime

@internal
@view
def _domain_separator() -> bytes32:
    return keccak256(
        abi_encode(
            DOMAIN_TYPEHASH,
            keccak256("OverUnder Oracle"),
            keccak256("1"),
            chain.id,
            self,
        )
    )

@internal
@view
def _attest_digest(conditionId: bytes32, outcome: uint8, evidenceHash: bytes32, deadline: uint256) -> bytes32:
    struct_hash: bytes32 = keccak256(abi_encode(ATTEST_TYPEHASH, conditionId, outcome, evidenceHash, deadline))
    return keccak256(concat(b"\x19\x01", self._domain_separator(), struct_hash))

@internal
@view
def _recover(digest: bytes32, sig: Bytes[65]) -> address:
    r: bytes32 = convert(slice(sig, 0, 32), bytes32)
    s: bytes32 = convert(slice(sig, 32, 32), bytes32)
    v: uint256 = convert(slice(sig, 64, 1), uint256)
    if v < 27:
        v += 27
    return ecrecover(digest, v, r, s)

@internal
def _record(conditionId: bytes32, agent: address, outcome: uint8, evidenceHash: bytes32):
    assert self.isAgent[agent], "not agent"
    assert not self.agentSubmitted[conditionId][agent], "dup attestation"
    assert outcome < 2
    self.agentSubmitted[conditionId][agent] = True
    self.agentOutcome[conditionId][agent] = outcome
    self.attestationCount[conditionId] += 1
    log AttestationRecorded(conditionId=conditionId, agent=agent, outcome=outcome, evidenceHash=evidenceHash)

@internal
def _payouts(outcome: uint8) -> uint256[2]:
    out: uint256[2] = [0, 0]
    if outcome == 0:
        out = [1, 0]
    else:
        out = [0, 1]
    return out

@internal
def _resolve(conditionId: bytes32, outcome: uint8):
    assert not self.resolved[conditionId]
    self.resolved[conditionId] = True
    extcall ICTF(self.ctf).reportPayouts(conditionId, self._payouts(outcome))

@external
def submitAttestation(conditionId: bytes32, outcome: uint8, evidenceHash: bytes32, deadline: uint256, sig: Bytes[65]):
    assert block.timestamp >= self.closeTime[conditionId], "not closed"
    assert block.timestamp <= deadline
    digest: bytes32 = self._attest_digest(conditionId, outcome, evidenceHash, deadline)
    agent: address = self._recover(digest, sig)
    self._record(conditionId, agent, outcome, evidenceHash)

@external
def submitConsensus(conditionId: bytes32, outcome: uint8, evidenceHash: bytes32, deadline: uint256, sigs: DynArray[Bytes[65], 3]):
    assert block.timestamp >= self.closeTime[conditionId], "not closed"
    assert block.timestamp <= deadline
    assert len(sigs) == 3, "need 3 sigs"
    digest: bytes32 = self._attest_digest(conditionId, outcome, evidenceHash, deadline)
    seen: address[3] = [empty(address), empty(address), empty(address)]
    for i: uint256 in range(3):
        agent: address = self._recover(digest, sigs[i])
        for j: uint256 in range(3):
            if j < i:
                assert seen[j] != agent, "dup signer"
        seen[i] = agent
        if not self.agentSubmitted[conditionId][agent]:
            self._record(conditionId, agent, outcome, evidenceHash)
        else:
            assert self.agentOutcome[conditionId][agent] == outcome, "conflict"
    self._resolve(conditionId, outcome)
    log ConsensusSubmitted(conditionId=conditionId, outcome=outcome)

@external
def castVote(conditionId: bytes32, outcome: uint8):
    assert outcome < 2
    assert not self.resolved[conditionId]
    assert self.closeTime[conditionId] > 0
    assert block.timestamp >= self.closeTime[conditionId]
    assert not self.voted[conditionId][msg.sender]
    yes_id: uint256 = staticcall ICTF(self.ctf).positionId(conditionId, 0)
    no_id: uint256 = staticcall ICTF(self.ctf).positionId(conditionId, 1)
    weight: uint256 = staticcall ICTF(self.ctf).balanceOf(msg.sender, yes_id) + staticcall ICTF(self.ctf).balanceOf(msg.sender, no_id)
    assert weight > 0, "no position"
    self.voted[conditionId][msg.sender] = True
    self.voteWeight[conditionId][outcome] += weight
    log VoteCast(conditionId=conditionId, voter=msg.sender, outcome=outcome, weight=weight)

@internal
@view
def _agent_majority(conditionId: bytes32) -> (bool, uint8):
    yes_c: uint256 = 0
    no_c: uint256 = 0
    for i: uint256 in range(3):
        a: address = self.agents[i]
        if self.agentSubmitted[conditionId][a]:
            if self.agentOutcome[conditionId][a] == 0:
                yes_c += 1
            else:
                no_c += 1
    if yes_c >= 2:
        return True, 0
    if no_c >= 2:
        return True, 1
    return False, 0

@external
def resolveFallback(conditionId: bytes32):
    assert not self.resolved[conditionId]
    assert block.timestamp >= self.closeTime[conditionId] + WINDOW, "window"
    ok: bool = False
    agent_out: uint8 = 0
    ok, agent_out = self._agent_majority(conditionId)
    assert ok, "no agent majority"
    yes_v: uint256 = self.voteWeight[conditionId][0]
    no_v: uint256 = self.voteWeight[conditionId][1]
    total_v: uint256 = yes_v + no_v
    if total_v > 0:
        vote_out: uint8 = 0
        if no_v > yes_v:
            vote_out = 1
        if vote_out != agent_out:
            # 2/3 of voted weight opposing agents -> arbitration
            opposing: uint256 = no_v if agent_out == 0 else yes_v
            if opposing * 3 >= total_v * 2:
                raise "arbitration required"
    self._resolve(conditionId, agent_out)
    log FallbackResolved(conditionId=conditionId, outcome=agent_out)

@external
def resolveArbitrated(conditionId: bytes32, outcome: uint8):
    assert msg.sender == self.operator
    assert outcome < 2
    assert not self.resolved[conditionId]
    assert block.timestamp >= self.closeTime[conditionId] + WINDOW
    self._resolve(conditionId, outcome)
    log Arbitrated(conditionId=conditionId, outcome=outcome)
