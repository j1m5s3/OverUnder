#pragma version 0.4.3

event Transfer:
    sender: indexed(address)
    receiver: indexed(address)
    value: uint256

event Approval:
    owner: indexed(address)
    spender: indexed(address)
    value: uint256

INITIAL_SUPPLY: public(constant(uint256)) = 100_000_000 * 10**18

name: public(String[32])
symbol: public(String[8])
decimals: public(uint8)
totalSupply: public(uint256)
balanceOf: public(HashMap[address, uint256])
allowance: public(HashMap[address, HashMap[address, uint256]])
treasury: public(address)

@deploy
def __init__(treasury: address):
    assert treasury != empty(address)
    self.name = "OverUnder"
    self.symbol = "OU"
    self.decimals = 18
    self.treasury = treasury
    self.totalSupply = INITIAL_SUPPLY
    self.balanceOf[treasury] = INITIAL_SUPPLY
    log Transfer(sender=empty(address), receiver=treasury, value=INITIAL_SUPPLY)

@external
def transfer(to: address, amount: uint256) -> bool:
    self.balanceOf[msg.sender] -= amount
    self.balanceOf[to] += amount
    log Transfer(sender=msg.sender, receiver=to, value=amount)
    return True

@external
def approve(spender: address, amount: uint256) -> bool:
    self.allowance[msg.sender][spender] = amount
    log Approval(owner=msg.sender, spender=spender, value=amount)
    return True

@external
def transferFrom(owner: address, to: address, amount: uint256) -> bool:
    allowed: uint256 = self.allowance[owner][msg.sender]
    if allowed != max_value(uint256):
        self.allowance[owner][msg.sender] = allowed - amount
    self.balanceOf[owner] -= amount
    self.balanceOf[to] += amount
    log Transfer(sender=owner, receiver=to, value=amount)
    return True

@external
def burn(amount: uint256):
    self.balanceOf[msg.sender] -= amount
    self.totalSupply -= amount
    log Transfer(sender=msg.sender, receiver=empty(address), value=amount)
