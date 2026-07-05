from decimal import Decimal

from django.db import transaction

from .models import Wallet, WalletTransaction


class InsufficientBalance(Exception):
    """Raised when a debit would push the wallet below zero."""


def get_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


@transaction.atomic
def credit(user, amount, reason, order=None, order_item=None, sub_type=None):
    """Add money to the wallet and record the ledger entry."""
    amount = Decimal(amount)
    if amount <= 0:
        return None

    if sub_type is None:
        sub_type = WalletTransaction.SUB_OTHER

    wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet, txn_type=WalletTransaction.CREDIT, sub_type=sub_type,
        amount=amount, reason=reason, order=order, order_item=order_item,
        balance_after=wallet.balance,
    )


@transaction.atomic
def debit(user, amount, reason, order=None, sub_type=None):
    """Remove money from the wallet. Raises InsufficientBalance if needed."""
    amount = Decimal(amount)
    wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
    if amount > wallet.balance:
        raise InsufficientBalance("Wallet balance is too low for this payment.")

    if sub_type is None:
        sub_type = WalletTransaction.SUB_ORDER

    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet, txn_type=WalletTransaction.DEBIT, sub_type=sub_type,
        amount=amount, reason=reason, order=order, balance_after=wallet.balance,
    )


def already_refunded(order_item):
    """True if this item already produced a refund credit."""
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
        sub_type=WalletTransaction.SUB_REFUND,
    )


@transaction.atomic
def top_up(user, amount, razorpay_payment_id):
    """Credit wallet from a verified Razorpay top-up payment.

    Idempotent: returns existing transaction if this payment was already
    processed so double-submits are safe.
    """
    amount = Decimal(amount)
    existing = WalletTransaction.objects.filter(
        razorpay_payment_id=razorpay_payment_id,
        txn_type=WalletTransaction.CREDIT,
    ).first()
    if existing:
        return existing, False   # already credited

    wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])

    txn = WalletTransaction.objects.create(
        wallet=wallet,
        txn_type=WalletTransaction.CREDIT,
        sub_type=WalletTransaction.SUB_TOPUP,
        amount=amount,
        reason=f"Wallet top-up via Razorpay",
        razorpay_payment_id=razorpay_payment_id,
        balance_after=wallet.balance,
    )
    return txn, True
