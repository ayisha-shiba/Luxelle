from decimal import Decimal

import razorpay
from django.conf import settings

from .models import Payment


class RazorpayOrderError(Exception):
    pass


def get_client():
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def to_paise(amount):
    return int(Decimal(amount) * 100)


def create_razorpay_order(order):
    client = get_client()

    try:
        rzp_order = client.order.create({
            "amount":   to_paise(order.total),
            "currency": "INR",
            "receipt":  str(order.order_number),
            "payment_capture": 1,   
        })
    except razorpay.errors.BadRequestError as exc:
        raise RazorpayOrderError(
            "Unable to create the online payment order because the amount exceeds Razorpay limits. "
            "Please try a smaller order, use a different payment method, or contact support."
        ) from exc

    return Payment.objects.create(
        order=order,
        razorpay_order_id=rzp_order["id"],
        amount=order.total,
        status=Payment.STATUS_CREATED,
    )


def verify_payment(params):
    payment = Payment.objects.filter(
        razorpay_order_id=params.get("razorpay_order_id")
    ).first()
    if payment is None:
        return None, False

    client = get_client()
    try:
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


def create_pending_razorpay_order(user, checkout_data, total_amount):
    client = get_client()

    from core.models import Order
    order_number = checkout_data.get("order_number")
    if not order_number:
        temp_order = Order(user=user)
        order_number = temp_order._generate_order_number()
        checkout_data["order_number"] = order_number

    try:
        rzp_order = client.order.create({
            "amount":   to_paise(total_amount),
            "currency": "INR",
            "receipt":  order_number,
            "payment_capture": 1,
        })
    except razorpay.errors.BadRequestError as exc:
        raise RazorpayOrderError(
            "Unable to create the online payment order because the amount exceeds Razorpay limits. "
            "Please try a smaller order, use a different payment method, or contact support."
        ) from exc

    from .models import PendingRazorpayOrder
    return PendingRazorpayOrder.objects.create(
        razorpay_order_id=rzp_order["id"],
        user=user,
        checkout_data=checkout_data,
        amount=total_amount,
    )


def verify_razorpay_signature(params):
    """Verify signature returned by Razorpay."""
    client = get_client()
    try:
        client.utility.verify_payment_signature(params)
        return True
    except razorpay.errors.SignatureVerificationError:
        return False

