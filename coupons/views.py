from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.models import Cart

from . import services
from .models import Coupon, CouponUsage

SESSION_KEY = "coupon_id"


def _cart_subtotal(user):
    cart = Cart.objects.filter(user=user).first()
    if not cart:
        return Decimal("0")
    return sum((Decimal(i.total_price) for i in cart.items.select_related("variant__product__category")), Decimal("0"))


@login_required
@require_POST
def apply_coupon(request):
    code = request.POST.get("code", "")
    subtotal = _cart_subtotal(request.user)
    try:
        coupon = services.validate_coupon(code, request.user, subtotal)
    except services.CouponError as exc:
        messages.error(request, str(exc))
        return redirect("checkout")

    request.session[SESSION_KEY] = coupon.id
    discount = services.compute_discount(coupon, subtotal)
    messages.success(request, f"Coupon {coupon.code} applied — you save Rs. {discount}.")
    return redirect("checkout")


@login_required
@require_POST
def remove_coupon(request):
    request.session.pop(SESSION_KEY, None)
    messages.info(request, "Coupon removed.")
    return redirect("checkout")


@login_required
@never_cache
def my_coupons(request):
    """Coupons currently available to this user (live and not yet used)."""
    now = timezone.now()
    used_ids = CouponUsage.objects.filter(user=request.user).values_list("coupon_id", flat=True)
    coupons = (Coupon.objects.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
               .exclude(id__in=used_ids)
               .order_by("-created_at"))
    return render(request, "coupons/my_coupons.html", {"coupons": coupons})
