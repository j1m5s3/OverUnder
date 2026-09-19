import boa
import pytest

from tests.conftest import deploy_protocol


@pytest.fixture
def proto():
    return deploy_protocol()


@pytest.fixture
def emissions_distributor(proto):
    """Deploy EmissionsDistributor using proto accounts."""
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent
    
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"].address
    operator = proto["accounts"]["operator"].address

    distributor = boa.load(
        ROOT / "src" / "EmissionsDistributor.vy",
        ou_token.address,
        treasury,
        operator,
    )
    return distributor


def test_emissions_distributor_deploy(proto, emissions_distributor):
    """Test EmissionsDistributor deployment."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"].address
    operator = proto["accounts"]["operator"].address
    
    assert emissions_distributor.ou() == ou_token.address
    assert emissions_distributor.treasury() == treasury
    assert emissions_distributor.operator() == operator


def test_distribute_transfers_from_treasury(proto, emissions_distributor):
    """Test distribute transfers OU from treasury to recipients."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    recipient_b = proto["accounts"]["trader_b"]
    
    # Treasury approves distributor
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 2_000_000 * 10**18)

    treasury_balance_before = ou_token.balanceOf(treasury.address)
    total_supply_before = ou_token.totalSupply()

    # Operator distributes LP rewards (program 0)
    recipients = [recipient_a.address, recipient_b.address]
    amounts = [1_000_000 * 10**18, 500_000 * 10**18]

    with boa.env.prank(operator.address):
        emissions_distributor.distribute(0, recipients, amounts)

    # Check treasury balance decreased
    assert ou_token.balanceOf(treasury.address) == treasury_balance_before - sum(amounts)

    # Check recipients received tokens
    assert ou_token.balanceOf(recipient_a.address) == amounts[0]
    assert ou_token.balanceOf(recipient_b.address) == amounts[1]

    # Check totalSupply unchanged
    assert ou_token.totalSupply() == total_supply_before


def test_distribute_enforces_operator_only(proto, emissions_distributor):
    """Test distribute can only be called by operator."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    recipient_a = proto["accounts"]["trader_a"]
    non_operator = proto["accounts"]["generator"]
    
    # Treasury approves distributor
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    # Non-operator tries to distribute
    with boa.env.prank(non_operator.address):
        with boa.reverts("operator only"):
            emissions_distributor.distribute(0, [recipient_a.address], [1_000_000 * 10**18])


def test_distribute_all_program_ids(proto, emissions_distributor):
    """Test distribute works for all program IDs (0-3)."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    
    # Treasury approves distributor
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 4_000_000 * 10**18)

    total_supply_before = ou_token.totalSupply()

    # Distribute for each program
    for program_id in range(4):
        with boa.env.prank(operator.address):
            emissions_distributor.distribute(program_id, [recipient_a.address], [1_000_000 * 10**18])

    # Total supply unchanged after all distributions
    assert ou_token.totalSupply() == total_supply_before


def test_distribute_reverts_on_length_mismatch(proto, emissions_distributor):
    """Test distribute reverts when recipients and amounts length mismatch."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    recipient_b = proto["accounts"]["trader_b"]
    
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        with boa.reverts("length mismatch"):
            emissions_distributor.distribute(
                0, [recipient_a.address, recipient_b.address], [1_000_000 * 10**18]
            )


def test_distribute_reverts_on_empty_recipients(proto, emissions_distributor):
    """Test distribute reverts when recipients list is empty."""
    operator = proto["accounts"]["operator"]
    
    with boa.env.prank(operator.address):
        with boa.reverts("empty"):
            emissions_distributor.distribute(0, [], [])


def test_distribute_reverts_on_zero_amount(proto, emissions_distributor):
    """Test distribute reverts when amount is zero."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        with boa.reverts("zero amount"):
            emissions_distributor.distribute(0, [recipient_a.address], [0])


def test_distribute_maintains_nav(proto, emissions_distributor):
    """Test that emissions (transfers) don't affect NAV calculation."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    usdc = proto["usdc"]
    vault = proto["vault"]
    
    # Setup: Give vault some USDC backing
    with boa.env.prank(treasury.address):
        usdc.faucet(10_000_000)
        usdc.transfer(vault.address, 10_000_000)

    nav_before = vault.nav()

    # Treasury approves and distributes
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        emissions_distributor.distribute(0, [recipient_a.address], [1_000_000 * 10**18])

    # NAV unchanged (USDC backing / totalSupply both unchanged)
    nav_after = vault.nav()
    assert nav_after == nav_before


def test_feevault_not_increased_by_emissions(proto, emissions_distributor):
    """Test that FeeVault OU balance is not increased by emissions."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    vault = proto["vault"]
    
    vault_balance_before = ou_token.balanceOf(vault.address)

    # Treasury approves and distributes
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        emissions_distributor.distribute(0, [recipient_a.address], [1_000_000 * 10**18])

    # FeeVault OU balance unchanged
    vault_balance_after = ou_token.balanceOf(vault.address)
    assert vault_balance_after == vault_balance_before


def test_total_supply_flat_after_emissions(proto, emissions_distributor):
    """Test totalSupply remains constant after emissions."""
    ou_token = proto["ou"]
    treasury = proto["accounts"]["treasury"]
    operator = proto["accounts"]["operator"]
    recipient_a = proto["accounts"]["trader_a"]
    
    total_supply_before = ou_token.totalSupply()

    # Treasury approves and distributes
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 5_000_000 * 10**18)

    with boa.env.prank(operator.address):
        emissions_distributor.distribute(0, [recipient_a.address], [5_000_000 * 10**18])

    # Total supply unchanged
    total_supply_after = ou_token.totalSupply()
    assert total_supply_after == total_supply_before
