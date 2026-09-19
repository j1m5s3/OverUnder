import boa
import pytest


@pytest.fixture
def treasury(accounts):
    return accounts[5]


@pytest.fixture
def operator(accounts):
    return accounts[6]


@pytest.fixture
def recipient_a(accounts):
    return accounts[7]


@pytest.fixture
def recipient_b(accounts):
    return accounts[8]


@pytest.fixture
def ou_token(treasury):
    """Deploy OU token with treasury as initial holder."""
    from tests.conftest import ROOT

    ou = boa.load(ROOT / "src" / "RevenueToken.vy", treasury.address)
    return ou


@pytest.fixture
def emissions_distributor(ou_token, treasury, operator):
    """Deploy EmissionsDistributor."""
    from tests.conftest import ROOT

    distributor = boa.load(
        ROOT / "src" / "EmissionsDistributor.vy",
        ou_token.address,
        treasury.address,
        operator.address,
    )
    return distributor


def test_emissions_distributor_deploy(emissions_distributor, ou_token, treasury, operator):
    """Test EmissionsDistributor deployment."""
    assert emissions_distributor.ou() == ou_token.address
    assert emissions_distributor.treasury() == treasury.address
    assert emissions_distributor.operator() == operator.address


def test_distribute_transfers_from_treasury(
    ou_token, emissions_distributor, treasury, operator, recipient_a, recipient_b
):
    """Test distribute transfers OU from treasury to recipients."""
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


def test_distribute_enforces_operator_only(
    ou_token, emissions_distributor, treasury, recipient_a, accounts
):
    """Test distribute can only be called by operator."""
    # Treasury approves distributor
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    # Non-operator tries to distribute
    non_operator = accounts[0]
    with boa.env.prank(non_operator.address):
        with boa.reverts("operator only"):
            emissions_distributor.distribute(0, [recipient_a.address], [1_000_000 * 10**18])


def test_distribute_all_program_ids(
    ou_token, emissions_distributor, treasury, operator, recipient_a
):
    """Test distribute works for all program IDs (0-3)."""
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


def test_distribute_reverts_on_length_mismatch(
    ou_token, emissions_distributor, treasury, operator, recipient_a, recipient_b
):
    """Test distribute reverts when recipients and amounts length mismatch."""
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        with boa.reverts("length mismatch"):
            emissions_distributor.distribute(
                0, [recipient_a.address, recipient_b.address], [1_000_000 * 10**18]
            )


def test_distribute_reverts_on_empty_recipients(ou_token, emissions_distributor, operator):
    """Test distribute reverts when recipients list is empty."""
    with boa.env.prank(operator.address):
        with boa.reverts("empty"):
            emissions_distributor.distribute(0, [], [])


def test_distribute_reverts_on_zero_amount(
    ou_token, emissions_distributor, treasury, operator, recipient_a
):
    """Test distribute reverts when amount is zero."""
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        with boa.reverts("zero amount"):
            emissions_distributor.distribute(0, [recipient_a.address], [0])


def test_distribute_maintains_nav(
    ou_token, emissions_distributor, treasury, operator, recipient_a, usdc, vault
):
    """Test that emissions (transfers) don't affect NAV calculation."""
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


def test_feevault_not_increased_by_emissions(
    ou_token, emissions_distributor, treasury, operator, recipient_a, vault
):
    """Test that FeeVault OU balance is not increased by emissions."""
    vault_balance_before = ou_token.balanceOf(vault.address)

    # Treasury approves and distributes
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 1_000_000 * 10**18)

    with boa.env.prank(operator.address):
        emissions_distributor.distribute(0, [recipient_a.address], [1_000_000 * 10**18])

    # FeeVault OU balance unchanged
    vault_balance_after = ou_token.balanceOf(vault.address)
    assert vault_balance_after == vault_balance_before


def test_total_supply_flat_after_emissions(
    ou_token, emissions_distributor, treasury, operator, recipient_a
):
    """Test totalSupply remains constant after emissions."""
    total_supply_before = ou_token.totalSupply()

    # Treasury approves and distributes
    with boa.env.prank(treasury.address):
        ou_token.approve(emissions_distributor.address, 5_000_000 * 10**18)

    with boa.env.prank(operator.address):
        emissions_distributor.distribute(0, [recipient_a.address], [5_000_000 * 10**18])

    # Total supply unchanged
    total_supply_after = ou_token.totalSupply()
    assert total_supply_after == total_supply_before
