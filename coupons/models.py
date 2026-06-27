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
        (FLAT,       "Fixed Amount (₹)"),
    ]

    # Applicability choices
    GLOBAL       = "global"
    SPECIFIC     = "specific"
    TARGET_CHOICES = [
        (GLOBAL,   "All Products (Global)"),
        (SPECIFIC, "Specific Products/Categories"),
    ]

    # Payment method choices (stored as comma-separated)
    PM_ALL    = "all"
    PM_COD    = "cod"
    PM_WALLET = "wallet"
    PM_ONLINE = "online"

    code          = models.CharField(max_length=30, unique=True, db_index=True)
    title         = models.CharField(max_length=100, blank=True, help_text="Display name for this coupon")
    description   = models.TextField(blank=True)

    discount_type  = models.CharField(max_length=10, choices=TYPE_CHOICES, default=PERCENTAGE)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    # Cap on a percentage coupon's rupee value (ignored for flat coupons).
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Smallest cart subtotal the coupon can be used on.
    min_order_amount    = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # Applicability
    applies_to   = models.CharField(max_length=10, choices=TARGET_CHOICES, default=GLOBAL)

    valid_from = models.DateTimeField()
    valid_to   = models.DateTimeField()

    # Usage restrictions
    usage_limit    = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    per_user_limit = models.PositiveIntegerField(default=0, help_text="0 = unlimited per user")
    first_time_only = models.BooleanField(default=False, help_text="Only for first-time users")

    # Payment method restrictions (comma-separated: all, cod, wallet, online)
    payment_methods = models.CharField(max_length=50, default="all", blank=True)

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
    def status(self):
        """Return human-readable status."""
        now = timezone.now()
        if not self.is_active:
            return "disabled"
        if self.valid_from > now:
            return "scheduled"
        if self.valid_to < now:
            return "expired"
        return "active"

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
        unique_together = (("coupon", "user"),)

    def __str__(self):
        return f"{self.coupon.code} by {self.user}"
