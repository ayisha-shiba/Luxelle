"""Coupon validation, discount math and redemption recording.

`validate_coupon` is the gatekeeper (used both when applying at checkout and
when finalising the order); `compute_discount` is pure math so totals can be
recomputed any time (e.g. after a cancellation).
"""
from decimal import Decimal

from core.pricing import money
from .models import Coupon, CouponUsage


class CouponError(Exception):
    """Human-readable reason a coupon can't be applied."""


def compute_discount(coupon, subtotal):
    """Rupee discount this coupon gives on `subtotal`. Never exceeds subtotal."""
    subtotal = Decimal(subtotal)
    if coupon.discount_type == Coupon.PERCENTAGE:
        discount = subtotal * Decimal(coupon.discount_value) / Decimal(100)
        if coupon.max_discount_amount:
            discount = min(discount, Decimal(coupon.max_discount_amount))
    else:  # flat
        discount = Decimal(coupon.discount_value)

    return money(min(discount, subtotal))


def validate_coupon(code, user, subtotal):
    """Return a usable Coupon for this user/subtotal or raise CouponError."""
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Enter a coupon code.")

    coupon = Coupon.objects.filter(code=code).first()
    if not coupon:
        raise CouponError("Invalid coupon code.")
    if not coupon.is_live:
        raise CouponError("This coupon is not active.")
    if Decimal(subtotal) < Decimal(coupon.min_order_amount):
        raise CouponError(f"Minimum order of Rs. {coupon.min_order_amount} required for this coupon.")
    if coupon.usage_limit and coupon.times_used >= coupon.usage_limit:
        raise CouponError("This coupon has reached its usage limit.")
    if CouponUsage.objects.filter(coupon=coupon, user=user).exists():
        raise CouponError("You have already used this coupon.")

    return coupon


def record_usage(coupon, user, order, discount_amount):
    """Mark the coupon as redeemed by this user. Idempotent per (coupon, user)."""
    CouponUsage.objects.get_or_create(
        coupon=coupon, user=user,
        defaults={"order": order, "discount_amount": discount_amount},
    )
