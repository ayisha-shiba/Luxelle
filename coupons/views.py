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

from django.db.models import Q

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
    """Coupons available to the user, with custom filters and status computes."""
    now = timezone.now()
    used_ids = list(CouponUsage.objects.filter(user=request.user).values_list("coupon_id", flat=True))

    coupons = Coupon.objects.all()

    # Search
    search = request.GET.get("search", "").strip()
    if search:
        coupons = coupons.filter(Q(code__icontains=search) | Q(title__icontains=search))

    # Status filter
    status = request.GET.get("status", "all")
    if status == "available":
        coupons = coupons.filter(is_active=True, valid_from__lte=now, valid_to__gte=now).exclude(id__in=used_ids)
    elif status == "used":
        coupons = coupons.filter(id__in=used_ids)
    elif status == "expired":
        coupons = coupons.filter(Q(valid_to__lt=now) | Q(is_active=False)).exclude(id__in=used_ids)

    coupons = coupons.order_by("-created_at")

    # Annotate computed user status
    for coupon in coupons:
        if coupon.id in used_ids:
            coupon.user_status = "used"
            coupon.user_status_label = "USED"
        elif not coupon.is_active or coupon.valid_to < now:
            coupon.user_status = "expired"
            coupon.user_status_label = "EXPIRED"
        else:
            coupon.user_status = "available"
            coupon.user_status_label = "AVAILABLE"

    return render(request, "coupons/my_coupons.html", {
        "coupons": coupons,
        "search_query": search,
        "status_filter": status,
    })
