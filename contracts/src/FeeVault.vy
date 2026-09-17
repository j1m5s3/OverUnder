#pragma version 0.4.3

interface IERC20:
    def transfer(to: address, amount: uint256) -> bool: nonpayable
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable
    def balanceOf(owner: address) -> uint256: view
    def totalSupply() -> uint256: view

interface IRevenueToken:
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable
    def burn(amount: uint256): nonpayable
    def totalSupply() -> uint256: view
    def balanceOf(owner: address) -> uint256: view

event RedeemRequested:
    account: indexed(address)
    amount: uint256
    availableAt: uint256

event RedeemClaimed:
    account: indexed(address)
    ouAmount: uint256
    usdcAmount: uint256

usdc: public(address)
ou: public(address)
cooldown: public(uint256)

struct Request:
    amount: uint256
    availableAt: uint256

requests: public(HashMap[address, Request])

@deploy
def __init__(usdc: address, ou: address, cooldown: uint256):
    assert usdc != empty(address) and ou != empty(address)
    self.usdc = usdc
    self.ou = ou
    self.cooldown = cooldown

@external
@view
def nav() -> uint256:
    """USDC (6 decimals) redeemable for 1e18 OU."""
    supply: uint256 = staticcall IRevenueToken(self.ou).totalSupply()
    if supply == 0:
        return 0
    bal: uint256 = staticcall IERC20(self.usdc).balanceOf(self)
    return bal * 10**18 // supply

@external
@view
def previewRedeem(ou_amount: uint256) -> uint256:
    supply: uint256 = staticcall IRevenueToken(self.ou).totalSupply()
    if supply == 0:
        return 0
    bal: uint256 = staticcall IERC20(self.usdc).balanceOf(self)
    return ou_amount * bal // supply

@external
def requestRedeem(ou_amount: uint256):
    assert ou_amount > 0
    req: Request = self.requests[msg.sender]
    assert req.amount == 0, "pending redeem"
    assert extcall IRevenueToken(self.ou).transferFrom(msg.sender, self, ou_amount)
    available: uint256 = block.timestamp + self.cooldown
    self.requests[msg.sender] = Request(amount=ou_amount, availableAt=available)
    log RedeemRequested(account=msg.sender, amount=ou_amount, availableAt=available)

@external
def claim():
    req: Request = self.requests[msg.sender]
    assert req.amount > 0, "no request"
    assert block.timestamp >= req.availableAt, "cooldown"
    self.requests[msg.sender] = Request(amount=0, availableAt=0)
    supply: uint256 = staticcall IRevenueToken(self.ou).totalSupply()
    bal: uint256 = staticcall IERC20(self.usdc).balanceOf(self)
    usdc_out: uint256 = req.amount * bal // supply
    extcall IRevenueToken(self.ou).burn(req.amount)
    if usdc_out > 0:
        assert extcall IERC20(self.usdc).transfer(msg.sender, usdc_out)
    log RedeemClaimed(account=msg.sender, ouAmount=req.amount, usdcAmount=usdc_out)
