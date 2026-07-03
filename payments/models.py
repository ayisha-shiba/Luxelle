from django.db import models

from core.models import Order


class Payment(models.Model):
    """One online payment attempt against an Order.

    Razorpay payments happen in two phases:
      1. Server creates a Razorpay order  -> we store `razorpay_order_id`, status="created".
      2. Browser pays and returns the payment id + signature -> we verify and
         update `razorpay_payment_id`, `razorpay_signature`, status="paid"/"failed".
    """

    STATUS_CREATED = "created"
    STATUS_PAID    = "paid"
    STATUS_FAILED  = "failed"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_PAID,    "Paid"),
        (STATUS_FAILED,  "Failed"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")

    # Phase 1: created by our server before the user pays.
    razorpay_order_id   = models.CharField(max_length=100, db_index=True)
    # Phase 2: returned by Razorpay's checkout after a successful payment.
    razorpay_payment_id = models.CharField(max_length=100, blank=True)
    razorpay_signature  = models.CharField(max_length=255, blank=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2)  # rupees we charged
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CREATED, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.razorpay_order_id} - {self.status}"


class PendingRazorpayOrder(models.Model):
    razorpay_order_id = models.CharField(max_length=100, unique=True, db_index=True)
    user = models.ForeignKey("core.CustomUser", on_delete=models.CASCADE)
    checkout_data = models.JSONField()          # serialized cart snapshot and options
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PendingRZP({self.razorpay_order_id} | {self.user.email})"

