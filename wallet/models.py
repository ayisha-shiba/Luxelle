from decimal import Decimal

from django.conf import settings
from django.db import models


class Wallet(models.Model):

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet"
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} - Rs. {self.balance}"


class WalletTransaction(models.Model):

    # Sub-type: lets us filter by refund / top-up / order-payment without
    # doing fragile LIKE queries on the reason text field.
    SUB_REFUND  = "refund"
    SUB_TOPUP   = "topup"
    SUB_ORDER   = "order"   # debit: order payment via wallet
    SUB_OTHER   = "other"
    SUB_CHOICES = [
        (SUB_REFUND, "Refund"),
        (SUB_TOPUP,  "Top Up"),
        (SUB_ORDER,  "Order"),
        (SUB_OTHER,  "Other"),
    ]

    CREDIT = "credit"
    DEBIT  = "debit"
    TYPE_CHOICES = [
        (CREDIT, "Credit"),
        (DEBIT,  "Debit"),
    ]

    wallet   = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    txn_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    sub_type = models.CharField(max_length=10, choices=SUB_CHOICES, default=SUB_OTHER, db_index=True)
    amount   = models.DecimalField(max_digits=12, decimal_places=2)
    reason   = models.CharField(max_length=255)

    order      = models.ForeignKey("core.Order",     on_delete=models.SET_NULL, null=True, blank=True, related_name="wallet_transactions")
    order_item = models.ForeignKey("core.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="wallet_transactions")

    # Razorpay payment ID stored for top-up idempotency guard
    razorpay_payment_id = models.CharField(max_length=100, blank=True, db_index=True)

    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.txn_type == self.CREDIT else "-"
        return f"{sign}Rs. {self.amount} ({self.reason})"
