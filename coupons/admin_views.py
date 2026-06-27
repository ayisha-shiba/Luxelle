from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import admin_required

from .forms import CouponForm
from .models import Coupon


def _list_context(request):
    coupons = Coupon.objects.all()

    status = request.GET.get("status", "all")
    now = timezone.now()
    if status == "active":
        coupons = coupons.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
    elif status == "expired":
        coupons = coupons.filter(Q(valid_to__lt=now) | Q(is_active=False))

    search = request.GET.get("search", "").strip()
    if search:
        coupons = coupons.filter(code__icontains=search)

    page = Paginator(coupons, 10).get_page(request.GET.get("page"))
    return {
        "coupons": page,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "status": status,
        "search_query": search,
    }


@admin_required
def admin_coupon_list_view(request):
    return render(request, "admin_panel/coupons/coupon_list.html", _list_context(request))


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
def admin_coupon_delete_view(request, coupon_id):
    coupon = Coupon.objects.filter(pk=coupon_id).first()
    if not coupon:
        messages.error(request, "Coupon not found.")
        return redirect("admin_coupons")
    coupon.delete()
    messages.success(request, "Coupon deleted.")
    return redirect("admin_coupons")
