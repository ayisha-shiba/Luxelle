import logging

logger = logging.getLogger(__name__)

import razorpay
from decimal import Decimal
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
    # Log the amount being sent to Razorpay (in paise)
    amount_paise = int(to_paise(order.total))
    if amount_paise > 2000000000:
        logger.error("Razorpay order amount exceeds maximum limit: %s", amount_paise)
        raise RazorpayOrderError("Order amount exceeds Razorpay maximum limit.")

    logger.debug("Creating Razorpay order for Order %s: amount=%s paise (₹%s)", order.order_number, amount_paise, order.total)
    try:
        rzp_order = client.order.create({
            "amount":   amount_paise,
            "currency": "INR",
            "receipt":  str(order.order_number),
            "payment_capture": 1,
        })
    except razorpay.errors.BadRequestError as exc:
        # Log full exception details for debugging
        logger.error("Razorpay order creation failed for Order %s: %s", order.order_number, exc, exc_info=True)
        raise RazorpayOrderError(
            f"Razorpay order creation failed: {exc}"
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

    # Log the amount being sent (paise) for pending Razorpay order
    amount_paise = int(to_paise(total_amount))
    if amount_paise > 2000000000:
        logger.error("Pending Razorpay order amount exceeds maximum limit: %s", amount_paise)
        raise RazorpayOrderError("Pending order amount exceeds Razorpay maximum limit.")

    logger.debug("Creating pending Razorpay order for temporary order %s: amount=%s paise (₹%s)", order_number, amount_paise, total_amount)
    try:
        rzp_order = client.order.create({
            "amount":   amount_paise,
            "currency": "INR",
            "receipt":  order_number,
            "payment_capture": 1,
        })
    except razorpay.errors.BadRequestError as exc:
        logger.error("Pending Razorpay order creation failed for %s: %s", order_number, exc, exc_info=True)
        raise RazorpayOrderError(
            f"Razorpay pending order creation failed: {exc}"
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

