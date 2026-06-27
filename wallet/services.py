"""All wallet balance changes go through here so the balance and the ledger
stay in lockstep. Callers (checkout, cancel, return-approval) never touch
`wallet.balance` directly."""
from decimal import Decimal

from django.db import transaction

from .models import Wallet, WalletTransaction


class InsufficientBalance(Exception):
    """Raised when a debit would push the wallet below zero."""


def get_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


@transaction.atomic
def credit(user, amount, reason, order=None, order_item=None):
    """Add money to the wallet (a refund) and record the ledger entry."""
    amount = Decimal(amount)
    if amount <= 0:
        return None

    # Lock the row so two concurrent refunds can't both read a stale balance.
    wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet, txn_type=WalletTransaction.CREDIT, amount=amount,
        reason=reason, order=order, order_item=order_item,
        balance_after=wallet.balance,
    )


@transaction.atomic
def debit(user, amount, reason, order=None):
    """Remove money from the wallet (paying with wallet). Raises
    InsufficientBalance if the wallet can't cover it."""
    amount = Decimal(amount)
    wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
    if amount > wallet.balance:
        raise InsufficientBalance("Wallet balance is too low for this payment.")

    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet, txn_type=WalletTransaction.DEBIT, amount=amount,
        reason=reason, order=order, balance_after=wallet.balance,
    )


def already_refunded(order_item):
    """True if this item already produced a refund credit — guards against
    double-refunding the same line."""
    return WalletTransaction.objects.filter(
        order_item=order_item, txn_type=WalletTransaction.CREDIT
    ).exists()


def refund_item(order_item, amount, reason):
    """Refund a single order line to its owner's wallet, once."""
    if amount <= 0 or already_refunded(order_item):
        return None
    return credit(
        order_item.order.user, amount, reason,
        order=order_item.order, order_item=order_item,
    )
