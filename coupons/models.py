from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Coupon(models.Model):
    PERCENTAGE = "percentage"
    FLAT       = "flat"
    TYPE_CHOICES = [
        (PERCENTAGE, "Percentage"),
        (FLAT,       "Flat Amount"),
    ]

    code          = models.CharField(max_length=30, unique=True, db_index=True)
    description   = models.CharField(max_length=200, blank=True)

    discount_type  = models.CharField(max_length=10, choices=TYPE_CHOICES, default=PERCENTAGE)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    # Cap on a percentage coupon's rupee value (ignored for flat coupons).
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Smallest cart subtotal the coupon can be used on.
    min_order_amount    = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    valid_from = models.DateTimeField()
    valid_to   = models.DateTimeField()

    # Total times the coupon may be redeemed across all users (0 = unlimited).
    usage_limit = models.PositiveIntegerField(default=0)
    is_active   = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        now = timezone.now()
        return self.is_active and self.valid_from <= now <= self.valid_to

    @property
    def times_used(self):
        return self.usages.count()


class CouponUsage(models.Model):
    """One redemption. Enforces once-per-user and feeds the total usage count."""

    coupon  = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coupon_usages")
    order   = models.ForeignKey("core.Order", on_delete=models.CASCADE, null=True, blank=True, related_name="coupon_usages")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("coupon", "user")

    def __str__(self):
        return f"{self.coupon.code} by {self.user}"
