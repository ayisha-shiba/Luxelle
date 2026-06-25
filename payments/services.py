"""Razorpay integration logic, kept out of the views.

Two public functions mirror the two phases of a Razorpay payment:
  - create_razorpay_order(order)  -> Phase 1 (before the user pays)
  - verify_payment(params)        -> Phase 2 (after the user pays)
"""
from decimal import Decimal

import razorpay
from django.conf import settings

from .models import Payment


def get_client():
    """Build an authenticated Razorpay client from the settings keys."""
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def to_paise(amount):
    """Razorpay works in the smallest currency unit. Rs. 1 == 100 paise (integer)."""
    return int(Decimal(amount) * 100)


def create_razorpay_order(order):
    """Phase 1: create a Razorpay order and a local Payment row to track it.

    Returns the saved Payment instance (which holds the razorpay_order_id the
    browser checkout needs).
    """
    client = get_client()

    rzp_order = client.order.create({
        "amount":   to_paise(order.total),
        "currency": "INR",
        "receipt":  str(order.order_number),
        "payment_capture": 1,   # auto-capture once paid; no separate capture step
    })

    return Payment.objects.create(
        order=order,
        razorpay_order_id=rzp_order["id"],
        amount=order.total,
        status=Payment.STATUS_CREATED,
    )


def verify_payment(params):
    """Phase 2: verify the signature Razorpay sent back and settle the Payment.

    `params` is the dict the browser posts to our callback:
        razorpay_order_id, razorpay_payment_id, razorpay_signature

    Returns (payment, ok). On a bad signature we mark the Payment failed and
    return ok=False instead of trusting the client.
    """
    payment = Payment.objects.filter(
        razorpay_order_id=params.get("razorpay_order_id")
    ).first()
    if payment is None:
        return None, False

    client = get_client()
    try:
        # Recomputes HMAC(order_id|payment_id, key_secret) and compares to the
        # signature. Raises SignatureVerificationError if it doesn't match.
        client.utility.verify_payment_signature(params)
    except razorpay.errors.SignatureVerificationError:
        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=["status", "updated_at"])
        return payment, False

    payment.razorpay_payment_id = params.get("razorpay_payment_id", "")
    payment.razorpay_signature  = params.get("razorpay_signature", "")
    payment.status = Payment.STATUS_PAID
    payment.save(update_fields=[
        "razorpay_payment_id", "razorpay_signature", "status", "updated_at",
    ])
    return payment, True
