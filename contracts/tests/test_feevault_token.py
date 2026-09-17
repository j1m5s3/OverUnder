import boa
import pytest

from tests.conftest import COOLDOWN, deploy_protocol


@pytest.fixture
def proto():
    return deploy_protocol()


def test_nav_and_redeem_cooldown(proto):
    usdc = proto["usdc"]
    ou = proto["ou"]
    vault = proto["vault"]
    treasury = proto["accounts"]["treasury"]
    holder = proto["accounts"]["trader_a"]

    fees = 1_000_000
    with boa.env.prank(holder.address):
        usdc.faucet(fees)
        usdc.transfer(vault.address, fees)

    supply = ou.totalSupply()
    nav = vault.nav()
    assert nav == fees * 10**18 // supply

    gift = 10**18
    with boa.env.prank(treasury.address):
        ou.transfer(holder.address, gift)

    expected = vault.previewRedeem(gift)
    with boa.env.prank(holder.address):
        ou.approve(vault.address, gift)
        vault.requestRedeem(gift)
        with boa.reverts():
            vault.claim()
        boa.env.time_travel(seconds=COOLDOWN)
        vault.claim()

    assert usdc.balanceOf(holder.address) == expected
    assert ou.totalSupply() == supply - gift
