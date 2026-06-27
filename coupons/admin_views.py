import random
import string

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import admin_required

from .forms import CouponForm
from .models import Coupon


def _generate_code(length=8):
    """Generate a random uppercase alphanumeric coupon code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(chars, k=length))
        if not Coupon.objects.filter(code=code).exists():
            return code


def _list_context(request):
    now = timezone.now()
    coupons = Coupon.objects.all()

    # Status filter
    status = request.GET.get("status", "all")
    if status == "active":
        coupons = coupons.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
    elif status == "scheduled":
        coupons = coupons.filter(is_active=True, valid_from__gt=now)
    elif status == "expired":
        coupons = coupons.filter(valid_to__lt=now)
    elif status == "disabled":
        coupons = coupons.filter(is_active=False)

    # Type filter
    dtype = request.GET.get("dtype", "all")
    if dtype == "percentage":
        coupons = coupons.filter(discount_type=Coupon.PERCENTAGE)
    elif dtype == "flat":
        coupons = coupons.filter(discount_type=Coupon.FLAT)

    # Search
    search = request.GET.get("search", "").strip()
    if search:
        coupons = coupons.filter(
            Q(code__icontains=search) | Q(title__icontains=search)
        )

    # Sort
    sort = request.GET.get("sort", "latest")
    if sort == "oldest":
        coupons = coupons.order_by("created_at")
    elif sort == "highest_discount":
        coupons = coupons.order_by("-discount_value")
    elif sort == "most_used":
        # annotate usage count and sort
        from django.db.models import Count
        coupons = coupons.annotate(usage_count=Count("usages")).order_by("-usage_count")
    elif sort == "expiring_soon":
        coupons = coupons.filter(valid_to__gte=now).order_by("valid_to")
    else:
        coupons = coupons.order_by("-created_at")

    # Stats
    all_coupons = Coupon.objects.all()
    total = all_coupons.count()
    active_count = all_coupons.filter(is_active=True, valid_from__lte=now, valid_to__gte=now).count()
    scheduled_count = all_coupons.filter(is_active=True, valid_from__gt=now).count()
    expired_count = all_coupons.filter(valid_to__lt=now).count()
    disabled_count = all_coupons.filter(is_active=False).count()
    from .models import CouponUsage
    total_usage = CouponUsage.objects.count()

    page = Paginator(coupons, 10).get_page(request.GET.get("page"))
    return {
        "coupons": page,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "status": status,
        "dtype": dtype,
        "sort": sort,
        "search_query": search,
        # stats
        "stat_total": total,
        "stat_active": active_count,
        "stat_scheduled": scheduled_count,
        "stat_expired": expired_count,
        "stat_disabled": disabled_count,
        "stat_usage": total_usage,
    }


@admin_required
def admin_coupon_list_view(request):
    return render(request, "admin_panel/coupons/coupon_list.html", _list_context(request))


@admin_required
def admin_coupon_generate_code(request):
    """AJAX endpoint to generate a unique coupon code."""
    code = _generate_code()
    return JsonResponse({"code": code})


@admin_required
def admin_coupon_add_view(request):
    if request.method == "POST":
        form = CouponForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Coupon created successfully.")
            return redirect("admin_coupons")
        context = _list_context(request)
        context["add_form"] = form
        context["open_modal"] = "add"
        return render(request, "admin_panel/coupons/coupon_list.html", context)
    return redirect("admin_coupons")


@admin_required
def admin_coupon_edit_view(request, coupon_id):
    coupon = Coupon.objects.filter(pk=coupon_id).first()
    if not coupon:
        messages.error(request, "Coupon not found.")
        return redirect("admin_coupons")

    if request.method == "POST":
        form = CouponForm(request.POST, instance=coupon)
        if form.is_valid():
            form.save()
            messages.success(request, "Coupon updated successfully.")
            return redirect("admin_coupons")
        context = _list_context(request)
        context["edit_form"] = form
        context["edit_coupon"] = coupon
        context["open_modal"] = "edit"
        return render(request, "admin_panel/coupons/coupon_list.html", context)
    return redirect("admin_coupons")


@admin_required
def admin_coupon_toggle_view(request, coupon_id):
    """Toggle is_active via POST (used by the toggle switch in the table)."""
    coupon = Coupon.objects.filter(pk=coupon_id).first()
    if not coupon:
        return JsonResponse({"error": "Not found"}, status=404)
    coupon.is_active = not coupon.is_active
    coupon.save(update_fields=["is_active"])
    return JsonResponse({"is_active": coupon.is_active})


@admin_required
def admin_coupon_delete_view(request, coupon_id):
    coupon = Coupon.objects.filter(pk=coupon_id).first()
    if not coupon:
        messages.error(request, "Coupon not found.")
        return redirect("admin_coupons")
    coupon.delete()
    messages.success(request, "Coupon deleted.")
    return redirect("admin_coupons")
