from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import admin_required
from core.models import Category, Product

from .forms import OfferForm
from .models import Offer


def _list_context(request):
    offers = Offer.objects.select_related("product", "category")

    status = request.GET.get("status", "all")
    now = timezone.now()
    if status == "active":
        offers = offers.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
    elif status == "scheduled":
        offers = offers.filter(is_active=True, valid_from__gt=now)
    elif status == "expired":
        offers = offers.filter(Q(valid_to__lt=now) | Q(is_active=False))

    search = request.GET.get("search", "").strip()
    if search:
        offers = offers.filter(name__icontains=search)

    page = Paginator(offers, 10).get_page(request.GET.get("page"))
    return {
        "offers": page,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "status": status,
        "search_query": search,
        "products": Product.objects.filter(is_deleted=False).order_by("name"),
        "categories": Category.objects.filter(is_deleted=False).order_by("name"),
        "offer_type_product": Offer.PRODUCT,
        "offer_type_category": Offer.CATEGORY,
    }


@admin_required
def admin_offer_list_view(request):
    return render(request, "admin_panel/offers/offer_list.html", _list_context(request))


@admin_required
def admin_offer_add_view(request):
    if request.method == "POST":
        form = OfferForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Offer created successfully.")
            return redirect("admin_offers")
        context = _list_context(request)
        context["add_form"] = form
        context["open_modal"] = "add"
        return render(request, "admin_panel/offers/offer_list.html", context)
    return redirect("admin_offers")


@admin_required
def admin_offer_edit_view(request, offer_id):
    offer = Offer.objects.filter(pk=offer_id).first()
    if not offer:
        messages.error(request, "Offer not found.")
        return redirect("admin_offers")

    if request.method == "POST":
        form = OfferForm(request.POST, instance=offer)
        if form.is_valid():
            form.save()
            messages.success(request, "Offer updated successfully.")
            return redirect("admin_offers")
        context = _list_context(request)
        context["edit_form"] = form
        context["edit_offer"] = offer
        context["open_modal"] = "edit"
        return render(request, "admin_panel/offers/offer_list.html", context)
    return redirect("admin_offers")


@admin_required
def admin_offer_delete_view(request, offer_id):
    offer = Offer.objects.filter(pk=offer_id).first()
    if not offer:
        messages.error(request, "Offer not found.")
        return redirect("admin_offers")
    offer.delete()
    messages.success(request, "Offer deleted.")
    return redirect("admin_offers")
