#pragma version 0.4.3

interface IERC20:
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable

interface ICTF:
    def safeTransferFrom(sender: address, receiver: address, id: uint256, amount: uint256, data: Bytes[1024]): nonpayable
    def positionId(conditionId: bytes32, outcome: uint8) -> uint256: view
    def isResolved(conditionId: bytes32) -> bool: view

event OrderCancelled:
    orderHash: indexed(bytes32)
    maker: indexed(address)

event OrderFilled:
    takerHash: indexed(bytes32)
    makerHash: indexed(bytes32)
    fillAmount: uint256
    volume: uint256
    fee: uint256

struct Order:
    maker: address
    isBuy: bool
    conditionId: bytes32
    outcome: uint8
    price: uint256
    amount: uint256
    salt: uint256
    nonce: uint256
    expiry: uint256

TAKER_FEE_BPS: public(constant(uint256)) = 75
BPS_DENOM: constant(uint256) = 10_000
PRICE_SCALE: constant(uint256) = 1_000_000

ctf: public(address)
usdc: public(address)
feeVault: public(address)
operator: public(address)
filled: public(HashMap[bytes32, uint256])
cancelled: public(HashMap[bytes32, bool])
nonces: public(HashMap[address, uint256])

DOMAIN_TYPEHASH: constant(bytes32) = keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
ORDER_TYPEHASH: constant(bytes32) = keccak256("Order(address maker,bool isBuy,bytes32 conditionId,uint8 outcome,uint256 price,uint256 amount,uint256 salt,uint256 nonce,uint256 expiry)")

@deploy
def __init__(ctf: address, usdc: address, feeVault: address, operator: address):
    assert ctf != empty(address) and usdc != empty(address) and feeVault != empty(address)
    self.ctf = ctf
    self.usdc = usdc
    self.feeVault = feeVault
    self.operator = operator

@internal
@view
def _domain_separator() -> bytes32:
    return keccak256(
        abi_encode(
            DOMAIN_TYPEHASH,
            keccak256("OverUnder Exchange"),
            keccak256("1"),
            chain.id,
            self,
        )
    )

@internal
@pure
def _struct_hash(o: Order) -> bytes32:
    return keccak256(
        abi_encode(
            ORDER_TYPEHASH,
            o.maker,
            o.isBuy,
            o.conditionId,
            o.outcome,
            o.price,
            o.amount,
            o.salt,
            o.nonce,
            o.expiry,
        )
    )

@external
@view
def hashOrder(o: Order) -> bytes32:
    return keccak256(concat(b"\x19\x01", self._domain_separator(), self._struct_hash(o)))

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
@view
def _validate(o: Order, sig: Bytes[65]) -> bytes32:
    assert o.maker != empty(address)
    assert o.outcome < 2
    assert o.amount > 0 and o.price > 0
    assert block.timestamp <= o.expiry
    assert o.nonce == self.nonces[o.maker]
    digest: bytes32 = keccak256(concat(b"\x19\x01", self._domain_separator(), self._struct_hash(o)))
    assert not self.cancelled[digest]
    signer: address = self._recover(digest, sig)
    assert signer == o.maker, "bad sig"
    return digest

@external
def cancelOrder(o: Order):
    digest: bytes32 = keccak256(concat(b"\x19\x01", self._domain_separator(), self._struct_hash(o)))
    assert msg.sender == o.maker or msg.sender == self.operator, "not maker"
    self.cancelled[digest] = True
    log OrderCancelled(orderHash=digest, maker=o.maker)

@external
def incrementNonce():
    self.nonces[msg.sender] += 1

@external
def matchOrders(taker: Order, maker: Order, fillAmount: uint256, takerSig: Bytes[65], makerSig: Bytes[65]):
    assert fillAmount > 0
    assert not staticcall ICTF(self.ctf).isResolved(taker.conditionId)
    assert taker.conditionId == maker.conditionId
    assert taker.outcome == maker.outcome
    assert taker.isBuy != maker.isBuy
    if taker.isBuy:
        assert taker.price >= maker.price, "no cross"
    else:
        assert maker.price >= taker.price, "no cross"
    t_hash: bytes32 = self._validate(taker, takerSig)
    m_hash: bytes32 = self._validate(maker, makerSig)
    assert self.filled[t_hash] + fillAmount <= taker.amount
    assert self.filled[m_hash] + fillAmount <= maker.amount
    self.filled[t_hash] += fillAmount
    self.filled[m_hash] += fillAmount
    volume: uint256 = fillAmount * maker.price // PRICE_SCALE
    fee: uint256 = volume * TAKER_FEE_BPS // BPS_DENOM
    token_id: uint256 = staticcall ICTF(self.ctf).positionId(taker.conditionId, taker.outcome)
    empty_data: Bytes[1024] = b""
    if taker.isBuy:
        assert extcall IERC20(self.usdc).transferFrom(taker.maker, maker.maker, volume)
        if fee > 0:
            assert extcall IERC20(self.usdc).transferFrom(taker.maker, self.feeVault, fee)
        extcall ICTF(self.ctf).safeTransferFrom(maker.maker, taker.maker, token_id, fillAmount, empty_data)
    else:
        pay: uint256 = volume - fee
        assert extcall IERC20(self.usdc).transferFrom(maker.maker, taker.maker, pay)
        if fee > 0:
            assert extcall IERC20(self.usdc).transferFrom(maker.maker, self.feeVault, fee)
        extcall ICTF(self.ctf).safeTransferFrom(taker.maker, maker.maker, token_id, fillAmount, empty_data)
    log OrderFilled(takerHash=t_hash, makerHash=m_hash, fillAmount=fillAmount, volume=volume, fee=fee)
