#pragma version 0.4.3

interface IRevenueToken:
    def transferFrom(owner: address, to: address, amount: uint256) -> bool: nonpayable
    def totalSupply() -> uint256: view

event EmissionDistributed:
    program: indexed(uint256)
    recipient: indexed(address)
    amount: uint256

operator: public(address)
ou: public(address)
treasury: public(address)

@deploy
def __init__(ou: address, treasury: address, operator: address):
    assert ou != empty(address) and treasury != empty(address) and operator != empty(address)
    self.ou = ou
    self.treasury = treasury
    self.operator = operator

@external
def distribute(program: uint256, recipients: DynArray[address, 100], amounts: DynArray[uint256, 100]):
    """Transfer OU from treasury to recipients. Operator-only. Never mints.
    
    program: 0=LP, 1=maker, 2=agent, 3=quest
    recipients: addresses to receive OU
    amounts: OU amounts (18 decimals) per recipient
    """
    assert msg.sender == self.operator, "operator only"
    assert len(recipients) == len(amounts), "length mismatch"
    assert len(recipients) > 0, "empty"
    
    supply_before: uint256 = staticcall IRevenueToken(self.ou).totalSupply()
    
    for i: uint256 in range(len(recipients), bound=100):
        recipient: address = recipients[i]
        amount: uint256 = amounts[i]
        assert recipient != empty(address), "zero recipient"
        assert amount > 0, "zero amount"
        
        # Transfer from treasury (requires treasury approval)
        assert extcall IRevenueToken(self.ou).transferFrom(self.treasury, recipient, amount)
        log EmissionDistributed(program=program, recipient=recipient, amount=amount)
    
    # Assert totalSupply unchanged (no mint)
    supply_after: uint256 = staticcall IRevenueToken(self.ou).totalSupply()
    assert supply_before == supply_after, "supply changed"
