"""Minimal unit tests for the mollie addon."""

from app.addons.payments.mollie.addon import MollieAddon


def test_addon_identity():
    assert MollieAddon.addon_id == "mollie"
    assert MollieAddon.addon_category == "payment"
