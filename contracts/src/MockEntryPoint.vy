#pragma version 0.4.3

# Minimal EntryPoint v0.7 mock for local paymaster testing
# Production uses canonical EntryPoint deployment

deposits: public(HashMap[address, uint256])

@external
@payable
def depositTo(account: address):
    """Accept paymaster deposits"""
    self.deposits[account] += msg.value

@external
@view
def getDepositInfo(account: address) -> (uint256, bool, uint112, uint48, uint48):
    """
    Returns (deposit, staked, stake, unstakeDelay, withdrawTime)
    Mock: only deposit matters, rest are zero
    """
    return (self.deposits[account], False, 0, 0, 0)

@external
@view
def balanceOf(account: address) -> uint256:
    """Alias for deposits[account]"""
    return self.deposits[account]
