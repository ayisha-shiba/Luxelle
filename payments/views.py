from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from core.models import Order

from . import services
from .models import Payment


@login_required
@never_cache
def payment_start(request, order_number):
    """Phase 1 page: create a Razorpay order and show the checkout."""
    order = get_object_or_404(
        Order, order_number=order_number, user=request.user,
        payment_method=Order.PAYMENT_RAZORPAY,
    )

    # Already paid? Don't let them pay twice.
    if order.payments.filter(status=Payment.STATUS_PAID).exists():
        return redirect("order_success", order_number=order.order_number)

    payment = services.create_razorpay_order(order)

    context = {
        "order": order,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "razorpay_order_id": payment.razorpay_order_id,
        "amount_paise": services.to_paise(order.total),
    }
    return render(request, "payments/payment_page.html", context)


@login_required
@never_cache
def payment_callback(request):
    """Phase 2: the browser posts Razorpay's response here after payment."""
    if request.method != "POST":
        return redirect("orders")

    params = {
        "razorpay_order_id":   request.POST.get("razorpay_order_id", ""),
        "razorpay_payment_id": request.POST.get("razorpay_payment_id", ""),
        "razorpay_signature":  request.POST.get("razorpay_signature", ""),
    }

    payment, ok = services.verify_payment(params)

    if not ok:
        order_number = payment.order.order_number if payment else None
        messages.error(request, "Payment could not be verified. Please try again.")
        if order_number:
            return redirect("payment_failure", order_number=order_number)
        return redirect("orders")

    messages.success(request, "Payment successful. Your order is confirmed.")
    return redirect("order_success", order_number=payment.order.order_number)


@login_required
@never_cache
def payment_failure(request, order_number):
    """Failure page with retry + continue-shopping options."""
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    return render(request, "payments/payment_failure.html", {"order": order})
