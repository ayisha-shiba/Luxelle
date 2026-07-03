from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from core.models import Order
from .models import Payment, PendingRazorpayOrder
from . import services


@login_required
@never_cache
def payment_start(request, order_number):
    """Phase 1 page: create a Razorpay order and show the checkout."""
    checkout_data = request.session.get("pending_razorpay_checkout")
    if not checkout_data or checkout_data.get("order_number") != order_number:
        # Try to restore from database Order first (e.g. Retry Payment or timed out)
        order = Order.objects.filter(order_number=order_number, user=request.user).first()
        if order:
            checkout_data = {
                "order_number": order.order_number,
                "address": {
                    "full_name": order.ship_full_name,
                    "phone": order.ship_phone,
                    "address_line1": order.ship_address_line1,
                    "address_line2": order.ship_address_line2,
                    "city": order.ship_city,
                    "state": order.ship_state,
                    "postal_code": order.ship_postal_code,
                    "country": order.ship_country,
                },
                "coupon_id": order.coupon_id,
                "payment_method": order.payment_method,
                "items": [
                    {
                        "variant_id": item.variant_id,
                        "quantity": item.quantity,
                    }
                    for item in order.items.all()
                ],
                "total_amount": str(order.total),
            }
            request.session["pending_razorpay_checkout"] = checkout_data
        else:
            # Try to restore checkout data from PendingRazorpayOrder
            pending_rzp = PendingRazorpayOrder.objects.filter(
                checkout_data__order_number=order_number,
                user=request.user
            ).first()
            if pending_rzp:
                checkout_data = pending_rzp.checkout_data
                request.session["pending_razorpay_checkout"] = checkout_data
            else:
                messages.error(request, "Session expired or order details not found.")
                return redirect("checkout")

    # Double check stock availability
    from core.models import ProductVariant
    for item in checkout_data["items"]:
        variant = ProductVariant.objects.filter(pk=item["variant_id"]).first()
        if not variant or variant.is_deleted or not variant.is_listed or variant.stock < item["quantity"]:
            messages.error(request, f"Some items in your cart (like {variant.variant_name if variant else 'an item'}) have become unavailable.")
            return redirect("cart")

    from decimal import Decimal
    total_amount = Decimal(checkout_data["total_amount"])

    # Check if PendingRazorpayOrder already exists for this order number
    pending_rzp = PendingRazorpayOrder.objects.filter(
        checkout_data__order_number=order_number,
        user=request.user
    ).first()

    if not pending_rzp:
        pending_rzp = services.create_pending_razorpay_order(request.user, checkout_data, total_amount)

    order_context = {
        "order_number": order_number,
        "total": total_amount,
        "ship_full_name": checkout_data["address"]["full_name"],
        "ship_phone": checkout_data["address"]["phone"],
    }

    context = {
        "order": order_context,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "razorpay_order_id": pending_rzp.razorpay_order_id,
        "amount_paise": services.to_paise(total_amount),
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

    # Verify signature
    ok = services.verify_razorpay_signature(params)

    # Find the PendingRazorpayOrder
    pending_rzp = PendingRazorpayOrder.objects.filter(
        razorpay_order_id=params["razorpay_order_id"],
        user=request.user
    ).first()

    if not pending_rzp:
        # Check if order was already successfully created (idempotency/refresh safety)
        order = Order.objects.filter(
            payments__razorpay_order_id=params["razorpay_order_id"],
            payments__status=Payment.STATUS_PAID
        ).first()
        if order:
            messages.success(request, "Payment successful. Your order is confirmed.")
            return redirect("order_success", order_number=order.order_number)

        messages.error(request, "Order details not found.")
        return redirect("cart")

    order_number = pending_rzp.checkout_data["order_number"]

    if not ok:
        messages.error(request, "Payment signature verification failed. Please try again.")
        return redirect("payment_failure", order_number=order_number)

    # Order creation, Order items creation, Stock deduction, Cart clearing, Payment status update
    from django.db import transaction
    from core.models import Order, OrderItem, OrderStatusEvent, ProductVariant, Cart
    from coupons.models import Coupon
    from coupons import services as coupon_services
    from offers import services as offers_services

    checkout_data = pending_rzp.checkout_data
    address_data = checkout_data["address"]
    coupon_id = checkout_data.get("coupon_id")

    active_coupon = None
    if coupon_id:
        active_coupon = Coupon.objects.filter(pk=coupon_id).first()

    try:
        with transaction.atomic():
            # Check if order already exists (it should, as checkout_view creates it upfront)
            order = Order.objects.filter(order_number=order_number).first()
            if not order:
                order = Order.objects.create(
                    user=request.user,
                    order_number=order_number,
                    ship_full_name=address_data["full_name"],
                    ship_phone=address_data["phone"],
                    ship_address_line1=address_data["address_line1"],
                    ship_address_line2=address_data.get("address_line2", ""),
                    ship_city=address_data["city"],
                    ship_state=address_data["state"],
                    ship_postal_code=address_data["postal_code"],
                    ship_country=address_data.get("country", "India"),
                    payment_method=Order.PAYMENT_RAZORPAY,
                    coupon=active_coupon,
                )

                for item_data in checkout_data["items"]:
                    variant = ProductVariant.objects.select_for_update().get(pk=item_data["variant_id"])
                    qty = item_data["quantity"]
                    if variant.stock < qty:
                        raise ValueError(f"{variant.variant_name} just went out of stock.")

                    unit_price = offers_services.best_offer_for(variant)["effective_price"]
                    line_total = unit_price * qty

                    order_item = OrderItem.objects.create(
                        order=order,
                        variant=variant,
                        product_name=variant.product.name,
                        variant_name=variant.variant_name,
                        sku=variant.sku,
                        unit_price=unit_price,
                        original_price=variant.original_price,
                        quantity=qty,
                        line_total=line_total,
                        status=OrderItem.STATUS_PENDING,
                    )

                    OrderStatusEvent.objects.create(
                        order_item=order_item,
                        status=OrderItem.STATUS_PENDING,
                        note="Order placed successfully.",
                    )

                    variant.stock -= qty
                    variant.save(update_fields=["stock"])

                order.recalculate_totals()

                if active_coupon:
                    coupon_services.record_usage(active_coupon, request.user, order, order.coupon_discount)
            else:
                # Order exists. Update status of items, deduct stock and record coupon.
                for item_data in checkout_data["items"]:
                    order_item = order.items.filter(variant_id=item_data["variant_id"]).first()
                    if order_item and order_item.status == OrderItem.STATUS_PAYMENT_FAILED:
                        variant = ProductVariant.objects.select_for_update().get(pk=item_data["variant_id"])
                        qty = item_data["quantity"]
                        if variant.stock < qty:
                            raise ValueError(f"{variant.variant_name} just went out of stock.")

                        variant.stock -= qty
                        variant.save(update_fields=["stock"])

                        order_item.status = OrderItem.STATUS_PENDING
                        order_item.save(update_fields=["status"])

                        OrderStatusEvent.objects.create(
                            order_item=order_item,
                            status=OrderItem.STATUS_PENDING,
                            note="Payment completed successfully. Order placed.",
                        )

                order.recalculate_totals()

                if active_coupon:
                    coupon_services.record_usage(active_coupon, request.user, order, order.coupon_discount)

            # Create Payment record
            payment, created = Payment.objects.get_or_create(
                order=order,
                razorpay_order_id=params["razorpay_order_id"],
                defaults={
                    "razorpay_payment_id": params["razorpay_payment_id"],
                    "razorpay_signature": params["razorpay_signature"],
                    "amount": order.total,
                    "status": Payment.STATUS_PAID,
                }
            )
            if not created and payment.status != Payment.STATUS_PAID:
                payment.razorpay_payment_id = params["razorpay_payment_id"]
                payment.razorpay_signature = params["razorpay_signature"]
                payment.status = Payment.STATUS_PAID
                payment.save(update_fields=["razorpay_payment_id", "razorpay_signature", "status", "updated_at"])

            # Delete cart items
            cart, _ = Cart.objects.get_or_create(user=request.user)
            cart.items.all().delete()

            # Clean session variables
            request.session.pop("coupon_id", None)
            request.session.pop("pending_razorpay_checkout", None)

            # Clean up pending razorpay order
            pending_rzp.delete()

    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("cart")

    messages.success(request, "Payment successful. Your order is confirmed.")
    return redirect("order_success", order_number=order.order_number)


@login_required
@never_cache
def payment_failure(request, order_number):
    """Failure page with retry + continue-shopping options."""
    # Check if order actually exists in DB
    order = Order.objects.filter(order_number=order_number, user=request.user).first()
    if order:
        return render(request, "payments/payment_failure.html", {"order": order})

    # If it is a pending (uncreated) order, we use a placeholder object/dictionary
    order_context = {
        "order_number": order_number,
        "is_pending_only": True,
    }
    return render(request, "payments/payment_failure.html", {"order": order_context})
