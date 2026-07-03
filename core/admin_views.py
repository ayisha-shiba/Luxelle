
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from .decorators import admin_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, Min, Sum
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps
import io
import re
import hashlib

NAME_ALPHA_RE = re.compile(r"^[A-Za-z ]+$")


def _validate_name_field(name, label):
    if len(name) < 2:
        return f"{label} name must be at least 2 characters."
    if len(name) > 50:
        return f"{label} name cannot exceed 50 characters."
    if not NAME_ALPHA_RE.match(name):
        return f"{label} name can only contain letters and spaces."
    return None
from .forms import SetNewPasswordForm, CategoryForm, ProductForm, ProductVariantForm
from .models import CustomUser, Category, Product, Brand, Material, ProductVariant, VariantImage, Order, OrderItem, OrderStatusEvent
from .utils import (
    OTP_EXPIRY_MINUTES,
    check_resend_cooldown,
    clear_pending_user_session,
    create_otp_for_user,
    get_pending_user,
    send_otp_email,
    set_pending_user_session,
    verify_otp,
)

logger = logging.getLogger(__name__)


# ADMIN LOGIN / LOGOUT

@never_cache
def admin_login_view(request):

    if request.session.get("_is_admin") and request.session.get("_admin_user_id"):
        return redirect("admin_dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")

        user = authenticate(request, email=email, password=password)

        if user is not None and user.is_staff:
            request.session.cycle_key()
            request.session["_is_admin"] = True
            request.session["_admin_user_id"] = str(user.id)
            request.session["_admin_pw_hash"] = user.password
            request.session.modified = True
            logger.info(f"[ADMIN LOGIN] Staff user logged in: {user.email}")
            messages.success(request, "Welcome to the Luxelle Admin Panel.")
            return redirect("admin_dashboard")
        else:
            messages.error(request, "Invalid admin credentials.")

    return render(request, "admin_panel/login.html")


@admin_required
@never_cache
def admin_logout_view(request):

    request.session.pop('_is_admin', None)
    request.session.pop('_admin_user_id', None)
    request.session.modified = True
    messages.success(request, "You have been logged out of the Admin Panel.")
    return redirect("admin_login")



# ADMIN DASHBOARD

# ADMIN DASHBOARD

@admin_required
def admin_dashboard_view(request):
    import datetime as dt
    import json
    from decimal import Decimal
    from django.utils import timezone
    from django.db.models import (
        Sum, Count, Avg, Q, F, ExpressionWrapper, DecimalField
    )
    from django.db.models.functions import TruncDate, Coalesce

    _D0 = Decimal("0")
    now  = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Customers ─────────────────────────────────────────────────────────────
    non_staff = CustomUser.objects.filter(is_staff=False)
    total_customers   = non_staff.count()
    active_customers  = non_staff.filter(is_active=True).count()
    blocked_customers = non_staff.filter(is_active=False).count()
    new_this_month    = non_staff.filter(date_joined__gte=first_of_month).count()
    recent_users      = non_staff.order_by("-date_joined")[:5]

    # ── Catalogue counts ──────────────────────────────────────────────────────
    total_products   = Product.objects.filter(is_deleted=False).count()
    total_categories = Category.objects.filter(is_deleted=False).count()
    total_brands     = Brand.objects.count()

    # ── Orders — aggregate by item status (each item moves independently) ─────
    all_orders = Order.objects.all()
    total_orders = all_orders.count()

    # Statuses that make an order "revenue-generating"
    billable_statuses = [
        OrderItem.STATUS_CONFIRMED, OrderItem.STATUS_PACKED,
        OrderItem.STATUS_SHIPPED,   OrderItem.STATUS_OUT_FOR_DELIVERY,
        OrderItem.STATUS_DELIVERED,
    ]

    # Summary card counts (by item, which is the real unit of fulfilment)
    item_qs = OrderItem.objects.all()
    pending_orders    = item_qs.filter(status=OrderItem.STATUS_PENDING).values("order").distinct().count()
    processing_orders = item_qs.filter(status__in=[OrderItem.STATUS_CONFIRMED, OrderItem.STATUS_PACKED]).values("order").distinct().count()
    shipped_orders    = item_qs.filter(status__in=[OrderItem.STATUS_SHIPPED, OrderItem.STATUS_OUT_FOR_DELIVERY]).values("order").distinct().count()
    delivered_orders  = item_qs.filter(status=OrderItem.STATUS_DELIVERED).values("order").distinct().count()
    cancelled_orders  = item_qs.filter(status=OrderItem.STATUS_CANCELLED).values("order").distinct().count()
    returned_orders   = item_qs.filter(status=OrderItem.STATUS_RETURNED).values("order").distinct().count()

    # ── Revenue statistics ────────────────────────────────────────────────────
    # Only include orders whose status is NOT cancelled / returned at order level
    revenue_qs = all_orders.exclude(
        status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED]
    )
    rev_agg = revenue_qs.aggregate(
        gross_revenue   = Coalesce(Sum("subtotal"),       _D0),
        net_revenue     = Coalesce(Sum("total"),          _D0),
        total_discounts = Coalesce(Sum("discount"),       _D0),
        coupon_discounts= Coalesce(Sum("coupon_discount"),_D0),
        total_tax       = Coalesce(Sum("tax"),            _D0),
        total_shipping  = Coalesce(Sum("shipping"),       _D0),
        order_count_rev = Count("id"),
    )
    gross_revenue    = rev_agg["gross_revenue"]
    net_revenue      = rev_agg["net_revenue"]
    total_discounts  = rev_agg["total_discounts"]
    coupon_discounts = rev_agg["coupon_discounts"]
    total_tax        = rev_agg["total_tax"]
    total_shipping   = rev_agg["total_shipping"]

    # Referral wallet credits — treat as a marketing discount
    try:
        from wallet.models import WalletTransaction
        referral_credits = (
            WalletTransaction.objects
            .filter(txn_type="credit", reason__icontains="referral")
            .aggregate(s=Coalesce(Sum("amount"), _D0))["s"]
        )
    except Exception:
        referral_credits = _D0

    aov = revenue_qs.aggregate(a=Avg("total"))["a"] or _D0

    # ── Today's snapshot ──────────────────────────────────────────────────────
    today_orders  = all_orders.filter(created_at__gte=today_start)
    today_revenue = today_orders.exclude(
        status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED]
    ).aggregate(s=Coalesce(Sum("total"), _D0))["s"]

    # ── Best-selling products (top 10 by units sold — excludes cancelled/returned items) ──
    top_products = list(
        OrderItem.objects
        .filter(status__in=billable_statuses)
        .filter(variant__isnull=False)
        .values(
            "variant__product__name",
            "variant__product__category__name",
            "variant__product__brand__name",
        )
        .annotate(
            units_sold=Sum("quantity"),
            revenue=Sum("line_total"),
        )
        .order_by("-units_sold")[:10]
    )
    # Append current stock from the variant with the most units sold
    from core.models import ProductVariant as _PV
    for p in top_products:
        name = p["variant__product__name"]
        stock = (
            _PV.objects
            .filter(product__name=name, is_deleted=False)
            .aggregate(s=Coalesce(Sum("stock"), 0))["s"]
        )
        p["stock"] = stock

    # ── Best-selling categories (top 10) ──────────────────────────────────────
    top_categories = list(
        OrderItem.objects
        .filter(status__in=billable_statuses)
        .filter(variant__product__category__isnull=False)
        .values("variant__product__category__name")
        .annotate(units_sold=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-units_sold")[:10]
    )

    # ── Best-selling brands (top 10) ──────────────────────────────────────────
    top_brands = list(
        OrderItem.objects
        .filter(status__in=billable_statuses)
        .filter(variant__product__brand__isnull=False)
        .values("variant__product__brand__name")
        .annotate(units_sold=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-units_sold")[:10]
    )

    # ── Recent orders (last 10) ───────────────────────────────────────────────
    recent_orders = (
        all_orders
        .select_related("user")
        .prefetch_related("items")
        .order_by("-created_at")[:10]
    )

    # ── Daily Revenue Trend (Last 15 Days) ────────────────────────────────────
    trend_start = now - dt.timedelta(days=14)
    daily_revenue_qs = (
        all_orders
        .exclude(status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED])
        .filter(created_at__date__gte=trend_start.date())
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(revenue=Coalesce(Sum("total"), _D0))
        .order_by("day")
    )
    trend_map = {d["day"]: d["revenue"] for d in daily_revenue_qs}
    trend_labels = []
    trend_revenue = []
    for i in range(14, -1, -1):
        day_date = (now - dt.timedelta(days=i)).date()
        trend_labels.append(day_date.strftime("%Y-%m-%d"))
        trend_revenue.append(float(trend_map.get(day_date, _D0)))

    # ── Order Status Counts for Doughnut Chart (all statuses) ─────────────────
    status_counter = {
        "pending":    0,
        "processing": 0,
        "shipped":    0,
        "delivered":  0,
        "cancelled":  0,
        "returned":   0,
    }
    for o in all_orders:
        ds_val, _ds_label = o.derived_status
        if ds_val in status_counter:
            status_counter[ds_val] += 1
        else:
            status_counter["processing"] += 1   # fallback for confirmed/packed/out-for-delivery

    delivered_count = status_counter["delivered"]
    cancelled_count = status_counter["cancelled"]
    returned_count  = status_counter["returned"]
    pending_count   = status_counter["pending"]

    status_labels = ["Pending", "Processing", "Shipped", "Delivered", "Cancelled", "Returned"]
    status_counts = [
        status_counter["pending"],
        status_counter["processing"],
        status_counter["shipped"],
        status_counter["delivered"],
        status_counter["cancelled"],
        status_counter["returned"],
    ]

    context = {
        "active": "dashboard",
        # Customers
        "total_customers":   total_customers,
        "active_customers":  active_customers,
        "blocked_customers": blocked_customers,
        "this_month_count":  new_this_month,
        "recent_users":      recent_users,
        # Catalogue
        "total_products":    total_products,
        "total_categories":  total_categories,
        "total_brands":      total_brands,
        # Orders
        "total_orders":      total_orders,
        "pending_orders":    pending_orders,
        "processing_orders": processing_orders,
        "shipped_orders":    shipped_orders,
        "delivered_orders":  delivered_orders,
        "cancelled_orders":  cancelled_orders,
        "returned_orders":   returned_orders,
        "returned_count":    returned_count,
        "delivered_count":   delivered_count,
        "cancelled_count":   cancelled_count,
        "pending_count":     pending_count,
        # Revenue
        "gross_revenue":     gross_revenue,
        "net_revenue":       net_revenue,
        "total_discounts":   total_discounts,
        "coupon_discounts":  coupon_discounts,
        "referral_credits":  referral_credits,
        "total_tax":         total_tax,
        "total_shipping":    total_shipping,
        "aov":               aov,
        "today_orders":      today_orders.count(),
        "today_revenue":     today_revenue,
        # Tables
        "top_products":   top_products,
        "top_categories": top_categories,
        "top_brands":     top_brands,
        "recent_orders":  recent_orders,
        # Chart Data
        "trend_labels_json":  json.dumps(trend_labels),
        "trend_revenue_json": json.dumps(trend_revenue),
        "status_labels_json": json.dumps(status_labels),
        "status_counts_json": json.dumps(status_counts),
    }
    return render(request, "admin_panel/dashboard.html", context)


def _build_dashboard_sales_chart(request):
    import datetime as dt
    from decimal import Decimal
    from django.utils import timezone
    from django.db.models import Sum, Count, DecimalField
    from django.db.models.functions import TruncDate, TruncHour, TruncMonth, Coalesce

    _D0 = Decimal("0")
    now = timezone.now()
    period = request.GET.get("period", "month")

    if period == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        granularity = "hour"
        period_label = "Today"

    elif period == "week":
        start_dt = (now - dt.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        granularity = "day"
        period_label = "Last 7 Days"

    elif period == "year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        granularity = "month"
        period_label = "This Year"

    elif period == "custom":
        try:
            s = request.GET.get("start_date", "")
            e = request.GET.get("end_date", "")
            import datetime
            sd = datetime.date.fromisoformat(s)
            ed = datetime.date.fromisoformat(e)
            if sd > ed:
                sd, ed = ed, sd
            start_dt = timezone.make_aware(datetime.datetime.combine(sd, datetime.time.min))
            end_dt = timezone.make_aware(datetime.datetime.combine(ed, datetime.time.max))
            delta_days = (ed - sd).days
            if delta_days == 0:
                granularity = "hour"
            elif delta_days <= 90:
                granularity = "day"
            else:
                granularity = "month"
            period_label = f"{sd.strftime('%d %b %Y')} – {ed.strftime('%d %b %Y')}"
        except Exception:
            period = "month"
            start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_dt = now
            granularity = "day"
            period_label = "This Month"

    else:
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        granularity = "day"
        period_label = "This Month"

    base_qs = (
        Order.objects
        .filter(created_at__gte=start_dt, created_at__lte=end_dt)
        .exclude(status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED])
    )

    labels = []
    revenues = []
    order_counts = []

    if granularity == "hour":
        grouped = (
            base_qs
            .annotate(bucket=TruncHour("created_at"))
            .values("bucket")
            .annotate(
                rev=Coalesce(Sum("total"), _D0, output_field=DecimalField()),
                cnt=Count("id"),
            )
            .order_by("bucket")
        )
        rev_map = {r["bucket"].hour: (float(r["rev"]), r["cnt"]) for r in grouped}
        for h in range(24):
            suffix = "AM" if h < 12 else "PM"
            display_h = h if h <= 12 else h - 12
            display_h = 12 if display_h == 0 else display_h
            labels.append(f"{display_h}{suffix}")
            rev, cnt = rev_map.get(h, (0.0, 0))
            revenues.append(rev)
            order_counts.append(cnt)

    elif granularity == "day":
        grouped = (
            base_qs
            .annotate(bucket=TruncDate("created_at"))
            .values("bucket")
            .annotate(
                rev=Coalesce(Sum("total"), _D0, output_field=DecimalField()),
                cnt=Count("id"),
            )
            .order_by("bucket")
        )
        rev_map = {r["bucket"]: (float(r["rev"]), r["cnt"]) for r in grouped}
        current = start_dt.date()
        end_date = end_dt.date()
        while current <= end_date:
            labels.append(current.strftime("%d %b"))
            rev, cnt = rev_map.get(current, (0.0, 0))
            revenues.append(rev)
            order_counts.append(cnt)
            current += dt.timedelta(days=1)

    else:
        grouped = (
            base_qs
            .annotate(bucket=TruncMonth("created_at"))
            .values("bucket")
            .annotate(
                rev=Coalesce(Sum("total"), _D0, output_field=DecimalField()),
                cnt=Count("id"),
            )
            .order_by("bucket")
        )
        rev_map = {(r["bucket"].year, r["bucket"].month): (float(r["rev"]), r["cnt"]) for r in grouped}
        MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        year = start_dt.year
        end_month = end_dt.month
        for m in range(1, end_month + 1):
            labels.append(MONTHS[m - 1])
            rev, cnt = rev_map.get((year, m), (0.0, 0))
            revenues.append(rev)
            order_counts.append(cnt)

    return labels, revenues, order_counts, period_label, granularity


# ── Dashboard Bar Chart AJAX Data ─────────────────────────────────────────────

@admin_required
def admin_dashboard_chart_data(request):
    labels, revenues, order_counts, period_label, granularity = _build_dashboard_sales_chart(request)
    return JsonResponse({
        "labels": labels,
        "revenue": revenues,
        "orders": order_counts,
        "period_label": period_label,
        "granularity": granularity,
    })


# USER MANAGEMENT

@admin_required
def admin_user_management_view(request):
        from django.utils import timezone
        search_query = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '')  

        from django.db.models import Q
        
        users_qs = CustomUser.objects.filter(is_staff=False).order_by('-date_joined')
        if search_query:
            users_qs = users_qs.filter(
                Q(email__icontains=search_query) |
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query)
            )
        if status_filter == 'active':
            users_qs = users_qs.filter(is_active=True)
        elif status_filter == 'blocked':
            users_qs = users_qs.filter(is_active=False)

        paginator = Paginator(users_qs,5)
        page_number = request.GET.get('page')
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        first_of_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        context = {
            'users': page_obj.object_list,
            'page_obj': page_obj,
            'is_paginated': page_obj.has_other_pages(),
            'search_query': search_query,
            'status_filter': status_filter,
            'active_count': users_qs.filter(is_active=True).count(),
            'blocked_count': users_qs.filter(is_active=False).count(),
            'this_month_count': users_qs.filter(date_joined__gte=first_of_month).count(),
        }
        return render(request, 'admin_panel/user_management.html', context)


@admin_required
def admin_user_profile_view(request):
    
    return render(request, "admin_panel/user_profile.html")


@admin_required
def admin_toggle_user_status_view(request, user_id):

    try:
        user        = CustomUser.objects.get(id=user_id, is_staff=False)
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        action = "unblocked" if user.is_active else "blocked"
        messages.success(request, f"User {user.get_full_name()} has been {action}.")
        admin_email = request.session.get('_admin_email', 'unknown')
        logger.info(f"[ADMIN] User {user.email} {action} by {admin_email}")
    except CustomUser.DoesNotExist:
        messages.error(request, "User not found.")

    return redirect("admin_users")




@admin_required
@never_cache
def admin_delete_user_view(request, user_id):
    if request.method not in ("POST", "GET"):
        return redirect('admin_users')
    from django.db.models import ProtectedError
    try:
        user = CustomUser.objects.get(id=user_id, is_staff=False)
        user_name = user.get_full_name() or user.email
        user.delete()
        messages.success(request, f"User {user_name} has been permanently deleted.")
        admin_email = request.session.get('_admin_email', 'unknown')
        logger.info(f"[ADMIN] User {user_name} deleted by {admin_email}")
    except CustomUser.DoesNotExist:
        messages.error(request, "User not found.")
    except ProtectedError:
        messages.error(
            request,
            "This user cannot be deleted because they have existing orders. "
            "Please handle the user's orders before deleting the account."
        )
    return redirect('admin_users')


# ADMIN FORGOT PASSWORD - REQUEST OTP


def admin_forgot_password_view(request):
    logger.debug("admin_forgot_password_view called with method=%s", request.method)
    if request.method != "POST":
        return render(request, "admin_panel/forgot_password.html")
    email = request.POST.get("email", "").strip()
    if not email:
        messages.error(request, "Please provide an email address.")
        return render(request, "admin_panel/forgot_password.html")
    try:
        user = CustomUser.objects.get(email=email, is_staff=True)
    except CustomUser.DoesNotExist:
        messages.success(request, "If an admin account with that email exists, an OTP has been sent.")
        return render(request, "admin_panel/forgot_password.html")
    allowed, seconds_left = check_resend_cooldown(user, purpose="password_reset")
    if not allowed:
        messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new OTP.")
        return render(request, "admin_panel/forgot_password.html")
    otp_obj = create_otp_for_user(user, purpose="password_reset")
    email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")
    if not email_sent:
        messages.warning(request, "OTP generated, but email could not be sent. Proceeding with verification.")

    request.session.cycle_key()
    set_pending_user_session(request, user.id, purpose="password_reset")
    request.session["pending_otp"] = otp_obj.otp
    request.session["pending_otp_expires_at"] = (otp_obj.created_at + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()
    request.session["pending_otp_sent_at"] = otp_obj.created_at.isoformat()
    request.session.set_expiry(OTP_EXPIRY_MINUTES * 60)
    request.session.save()
    logger.debug("Redirecting to OTP page for user %s", user.email)
    return redirect('admin_forgot_password_otp')


# ADMIN FORGOT PASSWORD - VERIFY OTP

@never_cache
def admin_forgot_password_otp_view(request):

    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please request a new OTP.")
        return redirect("admin_forgot_password")

    if request.method == "POST":
        otp_input = request.POST.get("hiddenOtp", "").strip()
        if not otp_input:
            otp_input = request.POST.get("otp", "").strip()
        is_valid, msg = verify_otp(user, otp_input, purpose="password_reset")

        if is_valid:
            request.session["password_reset_verified"] = True
            request.session["password_reset_user_id"]  = str(user.id)
            clear_pending_user_session(request)
            messages.success(request, "OTP verified. Please enter your new password.")
            return redirect("admin_reset_password")
        else:
            messages.error(request, msg)

    return render(request, "admin_panel/otp.html", {"email": user.email})


# ADMIN FORGOT PASSWORD - RESEND OTP

@never_cache
def admin_resend_forgot_password_otp_view(request):

    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please request a new OTP.")
        return redirect("admin_forgot_password")

    allowed, seconds_left = check_resend_cooldown(user, purpose="password_reset")
    if not allowed:
        messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new OTP.")
        return redirect("admin_forgot_password_otp")

    otp_obj    = create_otp_for_user(user, purpose="password_reset")
    email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")

    if email_sent:
        messages.success(request, f"A new OTP has been sent to {user.email}.")
    else:
        messages.error(request, "Failed to resend OTP. Please try again.")

    return redirect("admin_forgot_password_otp")


# ADMIN FORGOT PASSWORD - RESET PASSWORD

@never_cache
def admin_reset_password_view(request):

    if not request.session.get("password_reset_verified"):
        messages.error(request, "Please verify OTP first.")
        return redirect("admin_forgot_password")

    user_id = request.session.get("password_reset_user_id")
    try:
        user = CustomUser.objects.get(pk=user_id, is_staff=True)
    except CustomUser.DoesNotExist:
        messages.error(request, "Session expired. Please start again.")
        return redirect("admin_forgot_password")

    form = SetNewPasswordForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user.set_password(form.cleaned_data["new_password"])
            user.save(update_fields=["password"])

            for key in (
                "password_reset_verified",
                "password_reset_user_id",
                "_is_admin",
                "_admin_user_id",
                "_admin_pw_hash",
            ):
                request.session.pop(key, None)
            request.session.cycle_key()

            messages.success(request, "Password updated successfully. Please log in with your new password.")
            return redirect("admin_login")

    return render(request, "admin_panel/reset_password.html", {"form": form})


#ADMIN CATEGORY MANAGEMENT

def _category_list_context(request):
    search_query = request.GET.get("search", "").strip()
    status       = request.GET.get("status", "all")

    categories = Category.objects.all()
    if status == "visible":
        categories = categories.filter(is_deleted=False, is_listed=True)
    elif status == "hidden":
        categories = categories.filter(is_deleted=False, is_listed=False)
    elif status == "trash":
        categories = categories.filter(is_deleted=True)
    else:
        status = "all"
        categories = categories.filter(is_deleted=False)

    if search_query:
        categories = categories.filter(Q(name__icontains=search_query))
    categories = categories.order_by("-created_at")

    paginator   = Paginator(categories, 5)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return {
        "categories":   page_obj.object_list,
        "page_obj":     page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "search_query": search_query,
        "status":       status,
        "trash_count":  Category.objects.filter(is_deleted=True).count(),
    }


@admin_required
def admin_category_list_view(request):
    return render(request, "admin_panel/category_list.html", _category_list_context(request))


@admin_required
def admin_category_add_view(request):
    if request.method == "POST":
        form = CategoryForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Category added successfully.")
            return redirect("admin_categories")

        context = _category_list_context(request)
        context["add_form"] = form
        context["open_modal"] = "add"
        return render(request, "admin_panel/category_list.html", context)
    return redirect("admin_categories")


@admin_required
def admin_category_edit_view(request, category_id):
    category = Category.objects.filter(id=category_id, is_deleted=False).first()
    if not category:
        messages.error(request, "Category not found.")
        return redirect("admin_categories")

    if request.method == "POST":
        form = CategoryForm(request.POST, request.FILES, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated successfully.")
            return redirect("admin_categories")

        context = _category_list_context(request)
        context["edit_form"] = form
        context["edit_category"] = category
        context["open_modal"] = "edit"
        return render(request, "admin_panel/category_list.html", context)
    return redirect("admin_categories")


@admin_required
def admin_category_delete_view(request, category_id):
    category = Category.objects.filter(id=category_id, is_deleted=False).first()
    if not category:
        messages.error(request, "Category not found.")
        return redirect("admin_categories")

    category.is_deleted = True
    category.save(update_fields=["is_deleted"])
    messages.success(request, f"Category '{category.name}' has been moved to Trash.")
    return redirect("admin_categories")


@admin_required
def admin_category_restore_view(request, category_id):
    category = Category.objects.filter(id=category_id, is_deleted=True).first()
    if not category:
        messages.error(request, "Category not found in Trash.")
        return redirect(f"{reverse('admin_categories')}?status=trash")

    if Category.objects.filter(name__iexact=category.name, is_deleted=False).exists():
        messages.error(
            request,
            f"Cannot restore '{category.name}' — an active category with this name already exists. Rename or remove it first.",
        )
        return redirect(f"{reverse('admin_categories')}?status=trash")

    category.is_deleted = False
    category.save(update_fields=["is_deleted"])
    messages.success(request, f"Category '{category.name}' has been restored.")
    return redirect(f"{reverse('admin_categories')}?status=trash")


@admin_required
def admin_category_toggle_visibility_view(request, category_id):
    category = Category.objects.filter(id=category_id, is_deleted=False).first()
    if not category:
        messages.error(request, "Category not found.")
        return redirect("admin_categories")

    category.is_listed = not category.is_listed
    category.save(update_fields=["is_listed"])
    state = "visible" if category.is_listed else "hidden"
    messages.success(request, f"Category '{category.name}' is now {state}.")
    return redirect(request.META.get("HTTP_REFERER") or "admin_categories")


# ADMIN PRODUCT MANAGEMENT

SORT_OPTIONS = {
    "latest":     "-created_at",
    "oldest":     "created_at",
    "price_low":  "min_price",
    "price_high": "-min_price",
    "name_az":    "name",
    "name_za":    "-name",
}


@admin_required
def admin_product_list_view(request):
    search_query = request.GET.get("search", "").strip()
    status       = request.GET.get("status", "all")
    sort         = request.GET.get("sort", "latest")
    category_id  = request.GET.get("category", "").strip()
    brand_id     = request.GET.get("brand", "").strip()

    products = Product.objects.select_related("category", "brand").prefetch_related("variants__images")

    if status == "active":
        products = products.filter(is_deleted=False, is_listed=True)
    elif status == "inactive":
        products = products.filter(is_deleted=False, is_listed=False)
    elif status == "trash":
        products = products.filter(is_deleted=True)
    else:
        status = "all"
        products = products.filter(is_deleted=False)

    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(brand__name__icontains=search_query) |
            Q(variants__sku__icontains=search_query)
        ).distinct()

    if category_id.isdigit():
        products = products.filter(category_id=category_id)
    if brand_id.isdigit():
        products = products.filter(brand_id=brand_id)

    products = products.annotate(min_price=Min("variants__sale_price"))
    products = products.order_by(SORT_OPTIONS.get(sort, "-created_at"))

    paginator   = Paginator(products, 10)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    active_qs    = Product.objects.filter(is_deleted=False)
    out_of_stock = (active_qs
                    .annotate(total_stock=Sum("variants__stock", filter=Q(variants__is_deleted=False)))
                    .filter(Q(total_stock=0) | Q(total_stock__isnull=True))
                    .count())

    context = {
        "products":           page_obj.object_list,
        "page_obj":           page_obj,
        "is_paginated":       page_obj.has_other_pages(),
        "search_query":       search_query,
        "status":             status,
        "sort":               sort,
        "category_id":        category_id,
        "brand_id":           brand_id,
        "categories":         Category.objects.filter(is_deleted=False).order_by("name"),
        "brands":             Brand.objects.filter(is_deleted=False).order_by("name"),
        "total_count":        active_qs.count(),
        "active_count":       active_qs.filter(is_listed=True).count(),
        "inactive_count":     active_qs.filter(is_listed=False).count(),
        "out_of_stock_count": out_of_stock,
        "trash_count":        Product.objects.filter(is_deleted=True).count(),
    }
    return render(request, "admin_panel/product_list.html", context)


@admin_required
def admin_product_delete_view(request, product_id):
    product = Product.objects.filter(id=product_id, is_deleted=False).first()
    if not product:
        messages.error(request, "Product not found.")
        return redirect("admin_products")
    product.is_deleted = True
    product.save(update_fields=["is_deleted"])
    messages.success(request, f"Product '{product.name}' has been moved to Trash.")
    return redirect("admin_products")


@admin_required
def admin_product_restore_view(request, product_id):
    product = Product.objects.filter(id=product_id, is_deleted=True).first()
    if not product:
        messages.error(request, "Product not found in Trash.")
        return redirect(f"{reverse('admin_products')}?status=trash")
    product.is_deleted = False
    product.save(update_fields=["is_deleted"])
    messages.success(request, f"Product '{product.name}' has been restored.")
    return redirect(f"{reverse('admin_products')}?status=trash")


@admin_required
@require_POST
def admin_product_toggle_status_view(request, product_id):
    product = Product.objects.filter(id=product_id, is_deleted=False).first()
    if not product:
        messages.error(request, "Product not found.")
        return redirect("admin_products")
    product.is_listed = not product.is_listed
    product.save(update_fields=["is_listed"])
    state = "active" if product.is_listed else "inactive"
    messages.success(request, f"Product '{product.name}' is now {state}.")
    return redirect(request.META.get("HTTP_REFERER") or "admin_products")


def _sku_segment(value):
    value = re.sub(r"[^A-Za-z0-9\s-]", "", value or "")
    value = re.sub(r"[\s-]+", "-", value.strip())
    return value.upper().strip("-")


def _generate_unique_sku(product, variant):
    parts = []
    if product.category_id:
        parts.append(_sku_segment(product.category.name))
    if product.brand_id:
        parts.append(_sku_segment(product.brand.name))
    if variant.color:
        parts.append(_sku_segment(variant.color))
    if variant.size:
        parts.append(_sku_segment(variant.get_size_display()[:1]))

    base = "-".join(p for p in parts if p) or "SKU"

    sku = base
    suffix = 0
    while ProductVariant.objects.filter(sku__iexact=sku).exists():
        suffix += 1
        sku = f"{base}-{suffix}"
    return sku


def _build_variant_name(variant, product):
    parts = []
    if variant.color:
        parts.append(variant.color.strip())
    if variant.material_id:
        parts.append(variant.material.name)
    attrs = " ".join(p for p in parts if p).strip()
    return f"{product.name} - {attrs}" if attrs else product.name


PRODUCT_IMAGE_SIZE  = (800, 800)
ALLOWED_IMG_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_BYTES     = 5 * 1024 * 1024
MIN_IMAGE_DIM       = 200


def process_product_image(uploaded_file):
    """Validate format/size/dimensions, square-crop without distortion (cover), resize to a
    uniform size and re-encode as an optimized JPEG. Raises ValidationError on bad input."""
    name = getattr(uploaded_file, "name", "image")
    if getattr(uploaded_file, "size", 0) > MAX_IMAGE_BYTES:
        raise ValidationError(f"\"{name}\" is larger than 5 MB.")
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        fmt = (img.format or "").upper()
        if fmt == "JPG":
            fmt = "JPEG"
        if fmt not in ALLOWED_IMG_FORMATS:
            raise ValidationError("Only JPG, JPEG, PNG, or WEBP images are allowed.")
        if img.width < MIN_IMAGE_DIM or img.height < MIN_IMAGE_DIM:
            raise ValidationError(f"Images must be at least {MIN_IMAGE_DIM}x{MIN_IMAGE_DIM}px.")
        img = img.convert("RGB")
        img = ImageOps.fit(img, PRODUCT_IMAGE_SIZE, Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        buffer.seek(0)
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Could not process the image. Please upload a valid JPG, PNG, or WEBP file.")
    base = (name or "image").rsplit(".", 1)[0]
    return ContentFile(buffer.read(), name=f"{base}.jpg")


def _process_images(uploaded_files):
    """Process a batch of uploads with dedupe. Returns (processed_list, error_or_None)."""
    processed, hashes = [], set()
    for img in uploaded_files:
        try:
            cf = process_product_image(img)
        except ValidationError as exc:
            return processed, exc.messages[0]
        digest = hashlib.md5(cf.read()).hexdigest()
        cf.seek(0)
        if digest in hashes:
            return processed, "Duplicate images detected — please upload distinct images."
        hashes.add(digest)
        processed.append(cf)
    return processed, None


def _apply_inline_new(data):
    """A newly-added brand arrives as a NON-numeric select value (the typed name).
    Create it (case-insensitive get-or-create) and replace the value with its id."""
    brand_val = data.get("p-brand", "").strip()
    if brand_val and not brand_val.isdigit():
        brand = (Brand.objects.filter(name__iexact=brand_val, is_deleted=False).first()
                 or Brand.objects.create(name=brand_val))
        data["p-brand"] = str(brand.id)
    return data


@admin_required
def admin_brand_add_ajax(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request."}, status=400)
    name = request.POST.get("name", "").strip()
    err = _validate_name_field(name, "Brand")
    if err:
        return JsonResponse({"error": err}, status=400)
    brand = (Brand.objects.filter(name__iexact=name, is_deleted=False).first()
             or Brand.objects.create(name=name))
    return JsonResponse({"id": brand.id, "name": brand.name})


@admin_required
def admin_material_add_ajax(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request."}, status=400)
    name = request.POST.get("name", "").strip()
    err = _validate_name_field(name, "Material")
    if err:
        return JsonResponse({"error": err}, status=400)
    material = (Material.objects.filter(name__iexact=name).first()
                or Material.objects.create(name=name))
    return JsonResponse({"id": material.id, "name": material.name})


@admin_required
def admin_product_add_view(request):
    product_form = ProductForm(prefix="p")
    variant_form = ProductVariantForm(prefix="v")

    if request.method == "POST":
        data = _apply_inline_new(request.POST.copy())
        product_form = ProductForm(data, prefix="p")
        variant_form = ProductVariantForm(data, prefix="v")
        images       = request.FILES.getlist("images")

        extra_errors = []
        if len(images) < 3:
            extra_errors.append("Please upload at least 3 product images.")
        elif len(images) > 5:
            extra_errors.append("You can upload a maximum of 5 images.")
        processed_images = []
        if not extra_errors:
            processed_images, img_err = _process_images(images)
            if img_err:
                extra_errors.append(img_err)

        if product_form.is_valid() and variant_form.is_valid() and not extra_errors:
            with transaction.atomic():
                product = product_form.save(commit=False)
                if product.is_featured:
                    product.featured_at = timezone.now()
                product.save()
                variant = variant_form.save(commit=False)
                variant.product      = product
                variant.is_default   = True
                variant.is_listed    = True
                variant.variant_name = _build_variant_name(variant, product)
                if not variant.sku:
                    variant.sku = _generate_unique_sku(product, variant)
                variant.save()
                for index, cf in enumerate(processed_images):
                    VariantImage.objects.create(variant=variant, image=cf, is_primary=(index == 0))
            messages.success(request, f"Product '{product.name}' created successfully.")
            return redirect("admin_products")

        for err in extra_errors:
            messages.error(request, err)

    context = {
        "mode":          "add",
        "product_form":  product_form,
        "variant_form":  variant_form,
        "categories":    Category.objects.filter(is_deleted=False).order_by("name"),
        "brands":        Brand.objects.filter(is_deleted=False).order_by("name"),
        "materials":     Material.objects.all().order_by("name"),
    }
    return render(request, "admin_panel/product_form.html", context)


@admin_required
def admin_product_edit_view(request, product_id):
    product = Product.objects.filter(id=product_id, is_deleted=False).first()
    if not product:
        messages.error(request, "Product not found.")
        return redirect("admin_products")
    variant = product.default_variant
    was_featured = product.is_featured
    was_variant_listed = variant.is_listed if variant else True

    if request.method == "POST":
        data         = _apply_inline_new(request.POST.copy())
        product_form = ProductForm(data, prefix="p", instance=product)
        variant_form = ProductVariantForm(data, prefix="v", instance=variant)
        new_images   = request.FILES.getlist("images")
        delete_ids   = request.POST.getlist("delete_images")

        existing_after = variant.images.exclude(id__in=delete_ids).count() if variant else 0
        total_after    = existing_after + len(new_images)

        extra_errors = []
        if total_after < 3:
            extra_errors.append("A product must keep at least 3 images.")
        elif total_after > 5:
            extra_errors.append("A product can have a maximum of 5 images.")
        processed_images = []
        if not extra_errors:
            processed_images, img_err = _process_images(new_images)
            if img_err:
                extra_errors.append(img_err)

        if product_form.is_valid() and variant_form.is_valid() and not extra_errors:
            with transaction.atomic():
                product = product_form.save(commit=False)
                if product.is_featured and not was_featured:
                    product.featured_at = timezone.now()
                elif not product.is_featured:
                    product.featured_at = None
                product.save()
                v = variant_form.save(commit=False)
                v.product      = product
                v.is_default   = True
                v.is_listed    = was_variant_listed
                v.variant_name = _build_variant_name(v, product)
                if not v.sku:
                    v.sku = _generate_unique_sku(product, v)
                v.save()
                if delete_ids:
                    VariantImage.objects.filter(variant=v, id__in=delete_ids).delete()
                for cf in processed_images:
                    VariantImage.objects.create(variant=v, image=cf)
                if not v.images.filter(is_primary=True).exists():
                    first = v.images.first()
                    if first:
                        first.is_primary = True
                        first.save(update_fields=["is_primary"])
            messages.success(request, f"Product '{product.name}' updated successfully.")
            return redirect("admin_products")

        for err in extra_errors:
            messages.error(request, err)
    else:
        product_form = ProductForm(prefix="p", instance=product)
        variant_form = ProductVariantForm(prefix="v", instance=variant)

    context = {
        "mode":            "edit",
        "product":         product,
        "variant":         variant,
        "existing_images": variant.images.all() if variant else [],
        "product_form":    product_form,
        "variant_form":    variant_form,
        "categories":      Category.objects.filter(is_deleted=False).order_by("name"),
        "brands":          Brand.objects.filter(is_deleted=False).order_by("name"),
        "materials":       Material.objects.all().order_by("name"),
    }
    return render(request, "admin_panel/product_form.html", context)


@admin_required
def admin_product_detail_view(request, product_id):
    product = (Product.objects
               .select_related("category", "brand")
               .filter(id=product_id, is_deleted=False)
               .first())
    if not product:
        messages.error(request, "Product not found.")
        return redirect("admin_products")

    active_variants = product.variants.filter(is_deleted=False)
    stock_total = active_variants.aggregate(total=Sum("stock"))["total"] or 0

    show = request.GET.get("show")
    if show == "trash":
        variants = (product.variants
                    .filter(is_deleted=True)
                    .select_related("material")
                    .prefetch_related("images"))
    else:
        show = "active"
        variants = (active_variants
                    .select_related("material")
                    .prefetch_related("images"))

    context = {
        "product":              product,
        "variants":             variants,
        "show":                 show,
        "total_variants":       active_variants.count(),
        "total_stock":          stock_total,
        "active_variant_count": active_variants.filter(is_listed=True).count(),
        "out_of_stock_count":   active_variants.filter(stock=0).count(),
        "trash_count":          product.variants.filter(is_deleted=True).count(),
    }
    return render(request, "admin_panel/product_detail.html", context)


@admin_required
def admin_variant_add_view(request, product_id):
    product = Product.objects.filter(id=product_id, is_deleted=False).first()
    if not product:
        messages.error(request, "Product not found.")
        return redirect("admin_products")

    variant_form = ProductVariantForm(product=product)

    if request.method == "POST":
        variant_form = ProductVariantForm(request.POST, product=product)
        images = request.FILES.getlist("images")

        extra_errors = []
        if len(images) < 3:
            extra_errors.append("Please upload at least 3 variant images.")
        elif len(images) > 5:
            extra_errors.append("You can upload a maximum of 5 images.")
        processed_images = []
        if not extra_errors:
            processed_images, img_err = _process_images(images)
            if img_err:
                extra_errors.append(img_err)

        if variant_form.is_valid() and not extra_errors:
            with transaction.atomic():
                variant = variant_form.save(commit=False)
                variant.product      = product
                variant.is_default   = not product.variants.filter(is_deleted=False, is_default=True).exists()
                variant.variant_name = _build_variant_name(variant, product)
                if not variant.sku:
                    variant.sku = _generate_unique_sku(product, variant)
                variant.save()
                for index, cf in enumerate(processed_images):
                    VariantImage.objects.create(variant=variant, image=cf, is_primary=(index == 0))
            messages.success(request, f"Variant '{variant.variant_name}' added.")
            return redirect("admin_product_detail", product_id=product.id)

        for err in extra_errors:
            messages.error(request, err)

    context = {
        "mode":         "add",
        "product":      product,
        "variant_form": variant_form,
        "materials":    Material.objects.all().order_by("name"),
    }
    return render(request, "admin_panel/variant_form.html", context)


@admin_required
def admin_variant_edit_view(request, variant_id):
    variant = (ProductVariant.objects
               .filter(id=variant_id, is_deleted=False)
               .select_related("product")
               .first())
    if not variant:
        messages.error(request, "Variant not found.")
        return redirect("admin_products")
    product = variant.product

    if request.method == "POST":
        variant_form = ProductVariantForm(request.POST, instance=variant)
        new_images   = request.FILES.getlist("images")
        delete_ids   = request.POST.getlist("delete_images")

        existing_after = variant.images.exclude(id__in=delete_ids).count()
        total_after    = existing_after + len(new_images)

        extra_errors = []
        if total_after < 3:
            extra_errors.append("A variant must keep at least 3 images.")
        elif total_after > 5:
            extra_errors.append("A variant can have a maximum of 5 images.")
        processed_images = []
        if not extra_errors:
            processed_images, img_err = _process_images(new_images)
            if img_err:
                extra_errors.append(img_err)

        if variant_form.is_valid() and not extra_errors:
            with transaction.atomic():
                v = variant_form.save(commit=False)
                v.variant_name = _build_variant_name(v, product)
                if not v.sku:
                    v.sku = _generate_unique_sku(product, v)
                v.save()
                if delete_ids:
                    VariantImage.objects.filter(variant=v, id__in=delete_ids).delete()
                for cf in processed_images:
                    VariantImage.objects.create(variant=v, image=cf)
                if not v.images.filter(is_primary=True).exists():
                    first = v.images.first()
                    if first:
                        first.is_primary = True
                        first.save(update_fields=["is_primary"])
            messages.success(request, f"Variant '{v.variant_name}' updated.")
            return redirect("admin_product_detail", product_id=product.id)

        for err in extra_errors:
            messages.error(request, err)
    else:
        variant_form = ProductVariantForm(instance=variant)

    context = {
        "mode":            "edit",
        "product":         product,
        "variant":         variant,
        "existing_images": variant.images.all(),
        "variant_form":    variant_form,
        "materials":       Material.objects.all().order_by("name"),
    }
    return render(request, "admin_panel/variant_form.html", context)


@admin_required
@require_POST
def admin_variant_set_default_view(request, variant_id):
    variant = ProductVariant.objects.filter(id=variant_id, is_deleted=False).first()
    if not variant:
        messages.error(request, "Variant not found.")
        return redirect("admin_products")
    variant.is_default = True
    variant.save()
    messages.success(request, f"'{variant.variant_name}' is now the default variant.")
    return redirect("admin_product_detail", product_id=variant.product_id)


@admin_required
@require_POST
def admin_variant_toggle_status_view(request, variant_id):
    variant = ProductVariant.objects.filter(id=variant_id, is_deleted=False).first()
    if not variant:
        messages.error(request, "Variant not found.")
        return redirect("admin_products")
    variant.is_listed = not variant.is_listed
    variant.save(update_fields=["is_listed"])
    state = "active" if variant.is_listed else "inactive"
    messages.success(request, f"Variant '{variant.variant_name}' is now {state}.")
    return redirect(request.META.get("HTTP_REFERER") or reverse("admin_product_detail", args=[variant.product_id]))


@admin_required
def admin_variant_delete_view(request, variant_id):
    variant = (ProductVariant.objects
               .filter(id=variant_id, is_deleted=False)
               .select_related("product")
               .first())
    if not variant:
        messages.error(request, "Variant not found.")
        return redirect("admin_products")
    product = variant.product

    remaining = product.variants.filter(is_deleted=False).exclude(pk=variant.pk)
    if not remaining.exists():
        messages.error(request, "A product must keep at least one variant.")
        return redirect("admin_product_detail", product_id=product.id)

    with transaction.atomic():
        was_default = variant.is_default
        variant.is_deleted = True
        variant.is_default = False
        variant.save()
        if was_default:
            new_default = remaining.order_by("created_at").first()
            new_default.is_default = True
            new_default.save()

    messages.success(request, f"Variant '{variant.variant_name}' moved to Trash.")
    return redirect("admin_product_detail", product_id=product.id)


@admin_required
def admin_variant_restore_view(request, variant_id):
    variant = (ProductVariant.objects
               .filter(id=variant_id, is_deleted=True)
               .select_related("product")
               .first())
    if not variant:
        messages.error(request, "Variant not found.")
        return redirect("admin_products")
    variant.is_deleted = False
    variant.save()
    messages.success(request, f"Variant '{variant.variant_name}' restored.")
    return redirect(f"{reverse('admin_product_detail', args=[variant.product_id])}?show=trash")


# ORDER MANAGEMENT

ORDER_SORT_OPTIONS = {
    "latest":      "-created_at",
    "oldest":      "created_at",
    "amount_high": "-total",
    "amount_low":  "total",
}


@admin_required
def admin_order_list_view(request):
    search_query = request.GET.get("search", "").strip()
    status       = request.GET.get("status", "all")
    sort         = request.GET.get("sort", "latest")

    orders = Order.objects.select_related("user").prefetch_related("items")

    # Filter by item status: orders that contain at least one item in the chosen status.
    valid_statuses = dict(OrderItem.STATUS_CHOICES)
    if status in valid_statuses:
        orders = orders.filter(items__status=status).distinct()
    else:
        status = "all"

    if search_query:
        orders = orders.filter(
            Q(order_number__icontains=search_query) |
            Q(user__email__icontains=search_query) |
            Q(user__full_name__icontains=search_query)
        ).distinct()

    orders = orders.order_by(ORDER_SORT_OPTIONS.get(sort, "-created_at"))

    paginator   = Paginator(orders, 10)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    def orders_with_item_status(s):
        return Order.objects.filter(items__status=s).distinct().count()

    context = {
        "orders":          page_obj.object_list,
        "page_obj":        page_obj,
        "is_paginated":    page_obj.has_other_pages(),
        "search_query":    search_query,
        "status":          status,
        "sort":            sort,
        "status_choices":  OrderItem.STATUS_CHOICES,
        "total_count":     Order.objects.count(),
        "pending_count":   orders_with_item_status(OrderItem.STATUS_PENDING),
        "delivered_count": orders_with_item_status(OrderItem.STATUS_DELIVERED),
        "cancelled_count": orders_with_item_status(OrderItem.STATUS_CANCELLED),
    }
    return render(request, "admin_panel/order_list.html", context)


@admin_required
def admin_order_detail_view(request, order_id):
    order = (Order.objects.select_related("user")
             .prefetch_related("items__variant__product", "items__status_events")
             .filter(id=order_id).first())
    if not order:
        messages.error(request, "Order not found.")
        return redirect("admin_orders")

    context = {
        "order": order,
        "items": order.items.all(),
    }
    return render(request, "admin_panel/order_detail.html", context)


@admin_required
@require_POST
def admin_order_update_status_view(request, order_id):
    order = Order.objects.filter(id=order_id).first()
    if not order:
        messages.error(request, "Order not found.")
        return redirect("admin_orders")

    new_status = request.POST.get("status")
    valid_statuses = dict(Order.STATUS_CHOICES)

    allowed = dict(order.allowed_next_statuses())
    if new_status not in allowed:
        messages.error(
            request,
            f"Cannot change a {order.get_status_display()} order to "
            f"{valid_statuses.get(new_status, new_status)}.",
        )
        return redirect("admin_order_detail", order_id=order.id)

    if new_status == Order.STATUS_CANCELLED and order.status != Order.STATUS_CANCELLED:
        with transaction.atomic():
            for item in order.items.select_related("variant"):
                if item.variant and item.status != OrderItem.STATUS_CANCELLED:
                    item.variant.stock += item.quantity
                    item.variant.save(update_fields=["stock"])
                    item.status = OrderItem.STATUS_CANCELLED
                    item.save(update_fields=["status"])
            order.status = new_status
            order.save(update_fields=["status"])
            order.recalculate_totals()
    else:
        order.status = new_status
        order.save(update_fields=["status"])

    OrderStatusEvent.objects.create(
        order=order, status=new_status,
        note=f"Status updated to {valid_statuses[new_status]} by admin.",
    )

    messages.success(request, f"Order {order.order_number} marked as {valid_statuses[new_status]}.")
    return redirect("admin_order_detail", order_id=order.id)


@admin_required
@require_POST
def admin_order_item_update_status_view(request, item_id):
    item = (OrderItem.objects.select_related("order", "variant").filter(pk=item_id).first())
    if item is None:
        messages.error(request, "Order item not found.")
        return redirect("admin_orders")

    order      = item.order
    new_status = request.POST.get("status")
    note       = request.POST.get("note", "").strip()

    allowed = dict(item.admin_next_statuses())
    if new_status not in allowed:
        messages.error(request, "That status change isn't allowed for this item.")
        return redirect("admin_order_detail", order_id=order.id)

    with transaction.atomic():
        terminal_restock = (OrderItem.STATUS_CANCELLED, OrderItem.STATUS_RETURNED)
        if new_status in terminal_restock and item.status not in terminal_restock and item.variant:
            item.variant.stock += item.quantity
            item.variant.save(update_fields=["stock"])

        item.status = new_status
        item.save(update_fields=["status"])

        OrderStatusEvent.objects.create(
            order_item=item, status=new_status,
            note=note or f"Status updated to {allowed[new_status]} by admin.",
        )

        # Cancelling/restoring an item changes what's payable — keep totals fresh.
        order.recalculate_totals()

    messages.success(request, f"{item.product_name} marked as {allowed[new_status]}.")
    url = reverse("admin_order_detail", args=[order.id])
    return redirect(f"{url}#item-{item.id}")


# RETURN MANAGEMENT

RETURN_FILTERS = [
    (OrderItem.STATUS_RETURN_REQUESTED, "Return Requested"),
    (OrderItem.STATUS_RETURN_APPROVED,  "Return Approved"),
    (OrderItem.STATUS_PICKUP_SCHEDULED, "Pickup Scheduled"),
    (OrderItem.STATUS_RETURN_PICKED,    "Return Picked"),
    (OrderItem.STATUS_RETURNED,         "Returned to Stock"),
    (OrderItem.STATUS_RETURN_REPAIR,    "Sent for Repair"),
    (OrderItem.STATUS_RETURN_REJECTED,  "Return Rejected"),
]


@admin_required
def admin_return_requests_view(request):
    status = request.GET.get("status", "all")

    items = (OrderItem.objects
             .filter(status__in=OrderItem.RETURN_STATUSES)
             .select_related("order", "order__user", "variant")
             .order_by("-return_requested_at", "-id"))

    valid = dict(RETURN_FILTERS)
    if status in valid:
        items = items.filter(status=status)
    else:
        status = "all"

    paginator   = Paginator(items, 12)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    def count(s):
        return OrderItem.objects.filter(status=s).count()

    context = {
        "items":           page_obj.object_list,
        "page_obj":        page_obj,
        "is_paginated":    page_obj.has_other_pages(),
        "status":          status,
        "filters":         RETURN_FILTERS,
        "count_requested": count(OrderItem.STATUS_RETURN_REQUESTED),
        "count_approved":  count(OrderItem.STATUS_RETURN_APPROVED),
        "count_pickup":    count(OrderItem.STATUS_PICKUP_SCHEDULED),
        "count_picked":    count(OrderItem.STATUS_RETURN_PICKED),
        "count_returned":  count(OrderItem.STATUS_RETURNED),
        "count_repair":    count(OrderItem.STATUS_RETURN_REPAIR),
        "count_rejected":  count(OrderItem.STATUS_RETURN_REJECTED),
    }
    return render(request, "admin_panel/return_requests.html", context)


@admin_required
@require_POST
def admin_return_approve_view(request, item_id):
    item = OrderItem.objects.filter(pk=item_id).first()
    if not item or not item.can_approve_return:
        messages.error(request, "This return cannot be approved.")
        return redirect("admin_returns")

    with transaction.atomic():
        item.status = OrderItem.STATUS_RETURN_APPROVED
        item.save(update_fields=["status"])
        OrderStatusEvent.objects.create(
            order_item=item, status=OrderItem.STATUS_RETURN_APPROVED,
            note="Return approved by admin. Awaiting product pickup and inspection.",
        )
        # NOTE: No refund here. The wallet credit is issued only after the admin
        # confirms the inventory action (Returned to Stock or Sent for Repair)
        # in admin_return_update_status_view below.

    messages.success(request, f"Return approved for {item.product_name}. Proceed to schedule pickup.")
    return redirect(request.POST.get("next") or "admin_returns")


@admin_required
@require_POST
def admin_return_decline_view(request, item_id):
    item = OrderItem.objects.filter(pk=item_id).first()
    if not item or not item.can_decline_return:
        messages.error(request, "This return cannot be declined.")
        return redirect("admin_returns")

    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A rejection reason is required to decline a return.")
        return redirect("admin_returns")

    item.status                  = OrderItem.STATUS_RETURN_REJECTED
    item.return_rejection_reason = reason
    item.save(update_fields=["status", "return_rejection_reason"])
    OrderStatusEvent.objects.create(
        order_item=item, status=OrderItem.STATUS_RETURN_REJECTED,
        note=f"Return rejected by admin. Reason: {reason}",
    )
    # Rejected return → customer keeps & pays for the item → restore it to the total.
    item.order.recalculate_totals()
    messages.success(request, f"Return rejected for {item.product_name}.")
    return redirect(request.POST.get("next") or "admin_returns")


@admin_required
@require_POST
def admin_return_update_status_view(request, item_id):
    item = OrderItem.objects.select_related("order", "variant").filter(pk=item_id).first()
    if not item:
        messages.error(request, "Item not found.")
        return redirect("admin_returns")

    new_status = request.POST.get("status")
    allowed = dict(item.return_next_statuses())
    if new_status not in allowed:
        messages.error(request, "That return step isn't allowed.")
        return redirect(request.POST.get("next") or "admin_returns")

    from wallet import services as wallet_services

    with transaction.atomic():
        # ── Restock: only when item is physically returned to inventory ──────────
        if new_status == OrderItem.STATUS_RETURNED and item.variant:
            item.variant.stock += item.quantity
            item.variant.save(update_fields=["stock"])

        item.status = new_status
        item.save(update_fields=["status"])

        # ── Build the audit note ─────────────────────────────────────────────────
        if new_status == OrderItem.STATUS_RETURNED:
            note = "Item inspected — returned to stock."
        elif new_status == OrderItem.STATUS_RETURN_REPAIR:
            note = "Item inspected — sent for repair (not restocked)."
        else:
            note = f"Return step updated to {allowed[new_status]} by admin."
        OrderStatusEvent.objects.create(order_item=item, status=new_status, note=note)

        # ── Issue refund at the terminal inventory action ────────────────────────
        # Refund is due when the product is either restocked OR sent for repair.
        # In both cases the customer no longer holds the item, so the refund is
        # always warranted. The already_refunded() guard prevents double-credits.
        refund_statuses = {OrderItem.STATUS_RETURNED, OrderItem.STATUS_RETURN_REPAIR}
        if new_status in refund_statuses:
            # Drop the item from the payable total first so `refund` is exact.
            total_before = item.order.total
            item.order.recalculate_totals()
            refund = total_before - item.order.total

            prepaid = (
                item.order.payment_method == item.order.PAYMENT_WALLET
                or item.order.payments.filter(status="paid").exists()
            )

            if prepaid and refund > 0 and not wallet_services.already_refunded(item):
                wallet_services.refund_item(
                    item, refund,
                    f"Refund for returned item: {item.product_name}"
                )
                refund_note = f" ₹{refund} refunded to customer wallet."
                OrderStatusEvent.objects.create(
                    order_item=item, status=new_status,
                    note=f"Wallet refund of ₹{refund} processed for {item.product_name}.",
                )
                messages.success(request, f"{item.product_name}: {allowed[new_status]}.{refund_note}")
                return redirect(request.POST.get("next") or "admin_returns")
            elif wallet_services.already_refunded(item):
                messages.warning(request, f"{item.product_name}: status updated but refund was already processed.")
                return redirect(request.POST.get("next") or "admin_returns")
        else:
            # Non-terminal step (e.g. pickup_scheduled, return_picked) — no totals change yet.
            pass

    messages.success(request, f"{item.product_name}: {allowed[new_status]}.")
    return redirect(request.POST.get("next") or "admin_returns")


@admin_required
@require_POST
def admin_return_reallow_view(request, item_id):
    item = OrderItem.objects.filter(pk=item_id, status=OrderItem.STATUS_RETURN_REJECTED).first()
    if not item:
        messages.error(request, "Cannot re-allow this return.")
        return redirect("admin_returns")

    item.status                  = OrderItem.STATUS_DELIVERED
    item.return_rejection_reason = ""
    item.return_reason           = ""
    item.return_requested_at     = None
    item.save(update_fields=["status", "return_rejection_reason", "return_reason", "return_requested_at"])
    OrderStatusEvent.objects.create(
        order_item=item, status=OrderItem.STATUS_DELIVERED,
        note="Return re-allowed by admin; customer may request again.",
    )
    item.order.recalculate_totals()
    messages.success(request, f"{item.product_name}: the customer may request a return again.")
    return redirect("admin_returns")


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS & REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _get_analytics_period(request):
    """Parse GET params and return (period, period_label, start, end, prev_start, prev_end,
    start_date_str, end_date_str).

    Variable contract: every variable is guaranteed to be assigned before return,
    regardless of the period value or URL parameters.  Invalid / missing custom
    dates fall back to "This Month" without raising any exception.
    """
    import datetime as dt
    from django.utils import timezone

    today  = timezone.now().date()
    now    = timezone.now()
    start_date_str = request.GET.get("start_date", "")
    end_date_str   = request.GET.get("end_date",   "")
    requested      = request.GET.get("period", "month")

    def make_aware(d):
        return timezone.make_aware(dt.datetime.combine(d, dt.time.min))

    # ── Safe defaults (This Month) ─────────────────────────────────────────
    prev_m     = today.month - 1 or 12
    prev_y     = today.year if today.month > 1 else today.year - 1
    start      = make_aware(dt.date(today.year, today.month, 1))
    end        = now
    prev_start = make_aware(dt.date(prev_y, prev_m, 1))
    prev_end   = start
    label      = "This Month"
    period     = "month"

    # ── Map recognised periods ─────────────────────────────────────────────
    if requested == "today":
        start      = make_aware(today)
        end        = now
        prev_start = make_aware(today - dt.timedelta(days=1))
        prev_end   = start
        label      = "Today"
        period     = "today"

    elif requested == "week":
        week_start = today - dt.timedelta(days=today.weekday())
        start      = make_aware(week_start)
        end        = now
        prev_start = make_aware(week_start - dt.timedelta(weeks=1))
        prev_end   = start
        label      = "This Week"
        period     = "week"

    elif requested == "7days":
        start      = make_aware(today - dt.timedelta(days=7))
        end        = now
        prev_start = make_aware(today - dt.timedelta(days=14))
        prev_end   = start
        label      = "Last 7 Days"

    elif requested == "30days":
        start      = make_aware(today - dt.timedelta(days=30))
        end        = now
        prev_start = make_aware(today - dt.timedelta(days=60))
        prev_end   = start
        label      = "Last 30 Days"
        period     = "30days"

    elif requested == "month":
        pass   # already set to defaults above

    elif requested == "year":
        start      = make_aware(dt.date(today.year, 1, 1))
        end        = now
        prev_start = make_aware(dt.date(today.year - 1, 1, 1))
        prev_end   = start
        label      = "This Year"
        period     = "year"

    elif requested == "custom":
        # Both dates must be present and parseable; otherwise fall back quietly.
        if start_date_str and end_date_str:
            try:
                _s = dt.datetime.strptime(start_date_str, "%Y-%m-%d").date()
                _e = dt.datetime.strptime(end_date_str,   "%Y-%m-%d").date()
                # If inverted, swap so the range is always valid
                if _s > _e:
                    _s, _e = _e, _s
                    start_date_str = str(_s)
                    end_date_str   = str(_e)
                start      = make_aware(_s)
                end        = make_aware(_e + dt.timedelta(days=1))
                delta      = end - start
                prev_start = start - delta
                prev_end   = start
                label      = f"{_s.strftime('%d %b %Y')} – {_e.strftime('%d %b %Y')}"
                period     = "custom"
            except (ValueError, OverflowError):
                # Bad date format → fall back to This Month (defaults already set)
                start_date_str = ""
                end_date_str   = ""
        else:
            # One or both dates empty → fall back to This Month
            start_date_str = ""
            end_date_str   = ""
    # Any other unknown period value falls back to This Month (defaults already set)

    return period, label, start, end, prev_start, prev_end, start_date_str, end_date_str


@admin_required
def admin_analytics_view(request):
    import json
    import datetime as dt
    from decimal import Decimal
    from django.db.models import Sum, Count, F
    from django.db.models.functions import TruncDate, TruncMonth, TruncHour
    from .models import Order, OrderItem

    period, period_label, start, end, prev_start, prev_end, start_date_str, end_date_str = (
        _get_analytics_period(request)
    )

    # Base querysets
    all_orders = Order.objects.all()
    period_orders = all_orders.filter(created_at__gte=start, created_at__lte=end)

    # Only include completed/delivered (successful) orders in revenue/sales calculations.
    # Use item-level delivery status because order.status may not track individual item fulfilment.
    delivered_orders = period_orders.filter(items__status=OrderItem.STATUS_DELIVERED).distinct()

    # Metrics
    order_count = delivered_orders.count()
    net_revenue = delivered_orders.aggregate(s=Sum("total"))["s"] or Decimal("0.00")
    product_discounts = delivered_orders.aggregate(s=Sum("discount"))["s"] or Decimal("0.00")
    coupon_discounts = delivered_orders.aggregate(s=Sum("coupon_discount"))["s"] or Decimal("0.00")
    referral_discounts = Decimal("0.00")
    total_discount = product_discounts + coupon_discounts + referral_discounts
    
    # Products Sold
    delivered_items = OrderItem.objects.filter(order__in=delivered_orders)
    products_sold = delivered_items.aggregate(s=Sum("quantity"))["s"] or 0
    
    # Gross Sales Amount: sum of original_price * quantity for all delivered items
    gross_sales = delivered_items.aggregate(s=Sum(F("original_price") * F("quantity")))["s"] or Decimal("0.00")
    
    # Average Order Value (AOV)
    average_order_value = net_revenue / order_count if order_count > 0 else Decimal("0.00")

    # ── Sales Analytics Chart ────────────────────────────────────────────────
    chart_labels, chart_revenue, chart_items, _, _ = _build_dashboard_sales_chart(request)

    # Sales Report Table — paginated (20 rows per page)
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    report_orders_qs = (
        period_orders
        .select_related("user")
        .order_by("-created_at")
    )

    paginator    = Paginator(report_orders_qs, 20)
    page_number  = request.GET.get("page", 1)
    try:
        report_orders = paginator.page(page_number)
    except (PageNotAnInteger, EmptyPage):
        report_orders = paginator.page(1)

    # Build a query-string fragment that keeps period/date params when paginating
    qs_parts = [f"period={period}"]
    if start_date_str:
        qs_parts.append(f"start_date={start_date_str}")
    if end_date_str:
        qs_parts.append(f"end_date={end_date_str}")
    base_qs = "&".join(qs_parts)

    context = {
        "active": "analytics",
        "period": period,
        "period_label": period_label,
        "start_date": start_date_str,
        "end_date": end_date_str,

        # KPI Metrics
        "gross_sales": gross_sales,
        "order_count": order_count,
        "products_sold": products_sold,
        "total_discount": total_discount,
        "net_revenue": net_revenue,
        "average_order_value": average_order_value,

        # Details
        "product_discounts": product_discounts,
        "coupon_discounts": coupon_discounts,
        "referral_discounts": referral_discounts,

        # Chart
        "chart_labels": chart_labels,
        "chart_revenue": chart_revenue,
        "chart_items": chart_items,

        # Paginated report
        "report_orders": report_orders,
        "paginator": paginator,
        "base_qs": base_qs,
    }
    return render(request, "admin_panel/analytics.html", context)


@admin_required
def admin_analytics_pdf_view(request):
    """Generate and stream a PDF analytics report using xhtml2pdf."""
    import json
    from decimal import Decimal
    from django.db.models import Sum, Count, Avg, Q
    from django.db.models.functions import TruncDate
    from django.template.loader import render_to_string
    from django.http import HttpResponse
    from io import BytesIO
    from xhtml2pdf import pisa

    period, period_label, start, end, prev_start, prev_end, start_date_str, end_date_str = (
        _get_analytics_period(request)
    )

    _D0 = Decimal("0")
    all_orders    = Order.objects.all()
    period_orders = all_orders.filter(created_at__gte=start, created_at__lte=end)
    period_items  = OrderItem.objects.select_related(
        "order", "variant__product__category", "variant__product__brand"
    ).filter(order__created_at__gte=start, order__created_at__lte=end)

    agg = period_orders.aggregate(
        gross_sales  = Sum("subtotal"),
        net_revenue  = Sum("total"),
        discounts    = Sum("discount"),
        coupon_disc  = Sum("coupon_discount"),
        taxes        = Sum("tax"),
        delivery     = Sum("shipping"),
        order_count  = Count("id"),
    )
    top_products = list(
        period_items
        .filter(variant__isnull=False)
        .values("variant__product__name", "sku")
        .annotate(units_sold=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-units_sold")[:15]
    )
    top_categories = list(
        period_items
        .filter(variant__product__category__isnull=False)
        .values("variant__product__category__name")
        .annotate(revenue=Sum("line_total"))
        .order_by("-revenue")[:10]
    )
    pay_qs = list(
        period_orders.values("payment_method").annotate(count=Count("id")).order_by("-count")
    )
    pm_map = dict(Order.PAYMENT_CHOICES)

    order_status_summary = []
    for st_val, st_label in [
        ("pending","Pending"),("confirmed","Confirmed"),("shipped","Shipped"),
        ("delivered","Delivered"),("cancelled","Cancelled"),("returned","Returned"),
    ]:
        cnt = OrderItem.objects.filter(order__created_at__gte=start, order__created_at__lte=end, status=st_val).count()
        order_status_summary.append({"label": st_label, "count": cnt})

    ctx = {
        "period_label":   period_label,
        "start":          start,
        "end":            end,
        "gross_sales":    agg["gross_sales"]  or _D0,
        "net_revenue":    agg["net_revenue"]  or _D0,
        "discounts":      agg["discounts"]    or _D0,
        "coupon_disc":    agg["coupon_disc"]  or _D0,
        "taxes":          agg["taxes"]        or _D0,
        "delivery":       agg["delivery"]     or _D0,
        "order_count":    agg["order_count"]  or 0,
        "top_products":   top_products,
        "top_categories": top_categories,
        "pay_summary":    [{"label": pm_map.get(d["payment_method"], d["payment_method"]), "count": d["count"]} for d in pay_qs],
        "order_status_summary": order_status_summary,
    }

    html_str  = render_to_string("admin_panel/analytics_pdf.html", ctx)
    buffer    = BytesIO()
    pisa_status = pisa.CreatePDF(html_str, dest=buffer)
    if pisa_status.err:
        return HttpResponse("PDF generation failed.", status=500)
    buffer.seek(0)
    filename = f"luxelle_analytics_{period_label.replace(' ', '_').lower()}.pdf"
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
# LEDGER BOOK EXPORTS  (PDF / Excel / CSV)
# ─────────────────────────────────────────────────────────────────────────────

def _build_ledger_rows(start, end):
    """Return a list of ledger dicts sorted by created_at, with running balance."""
    from decimal import Decimal
    try:
        from wallet.models import WalletTransaction
        txns = (
            WalletTransaction.objects
            .select_related("wallet__user", "order")
            .filter(wallet__isnull=False)
        )
        if start:
            txns = txns.filter(created_at__gte=start)
        if end:
            txns = txns.filter(created_at__lte=end)
        txns = txns.order_by("created_at")
    except Exception:
        return []

    rows = []
    running = Decimal("0")
    pm_map = dict(Order.PAYMENT_CHOICES)
    for t in txns:
        credit = t.amount if t.txn_type == "credit" else Decimal("0")
        debit  = t.amount if t.txn_type == "debit"  else Decimal("0")
        running += credit - debit
        order = t.order
        rows.append({
            "date":     t.created_at,
            "order_id": order.order_number if order else "—",
            "customer": t.wallet.user.get_full_name() or t.wallet.user.email,
            "debit":    debit,
            "credit":   credit,
            "payment":  pm_map.get(order.payment_method, order.payment_method) if order else "—",
            "status":   order.get_status_display() if order else "—",
            "balance":  running,
        })
    return rows


def _ledger_period(request):
    """Parse start_date/end_date from GET; default to current month."""
    import datetime as dt
    from django.utils import timezone
    today = timezone.now().date()
    sd = request.GET.get("start_date", "")
    ed = request.GET.get("end_date", "")
    try:
        start = timezone.make_aware(dt.datetime.strptime(sd, "%Y-%m-%d"))
    except (ValueError, TypeError):
        start = timezone.make_aware(dt.datetime(today.year, today.month, 1))
    try:
        end = timezone.make_aware(dt.datetime.strptime(ed, "%Y-%m-%d") + dt.timedelta(days=1))
    except (ValueError, TypeError):
        end = timezone.now()
    return start, end, sd, ed


@admin_required
def admin_ledger_pdf_view(request):
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    from io import BytesIO
    from xhtml2pdf import pisa

    start, end, sd, ed = _ledger_period(request)
    rows = _build_ledger_rows(start, end)
    html = render_to_string("admin_panel/ledger_pdf.html", {
        "rows": rows,
        "start_date": sd or start.strftime("%Y-%m-%d"),
        "end_date":   ed or end.strftime("%Y-%m-%d"),
    })
    buf = BytesIO()
    pisa.CreatePDF(html, dest=buf)
    buf.seek(0)
    filename = f"luxelle_ledger_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.pdf"
    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin_required
def admin_ledger_excel_view(request):
    from django.http import HttpResponse
    from io import BytesIO
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    start, end, sd, ed = _ledger_period(request)
    rows = _build_ledger_rows(start, end)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ledger"
    GOLD_FILL   = PatternFill("solid", fgColor="C5A059")
    HEADER_FONT = Font(bold=True, color="0B0B0B", size=11)
    thin = Side(style="thin", color="DDDDDD")
    thin_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = ["Date", "Order ID", "Customer", "Debit (INR)", "Credit (INR)",
               "Payment Method", "Order Status", "Running Balance (INR)"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = GOLD_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    for r in rows:
        ws.append([
            r["date"].strftime("%d %b %Y %H:%M"),
            r["order_id"], r["customer"],
            float(r["debit"]), float(r["credit"]),
            r["payment"], r["status"], float(r["balance"]),
        ])

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 40)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"luxelle_ledger_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin_required
def admin_ledger_csv_view(request):
    import csv
    from django.http import HttpResponse

    start, end, sd, ed = _ledger_period(request)
    rows = _build_ledger_rows(start, end)
    filename = f"luxelle_ledger_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(["Date", "Order ID", "Customer", "Debit (INR)", "Credit (INR)",
                     "Payment Method", "Order Status", "Running Balance (INR)"])
    for r in rows:
        writer.writerow([
            r["date"].strftime("%d %b %Y %H:%M"),
            r["order_id"], r["customer"],
            float(r["debit"]), float(r["credit"]),
            r["payment"], r["status"], float(r["balance"]),
        ])
    return response
@admin_required
def admin_analytics_excel_view(request):
    """Generate and stream a multi-sheet Excel analytics report using openpyxl."""
    from decimal import Decimal
    from django.db.models import Sum, Count
    from django.http import HttpResponse
    from io import BytesIO
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    period, period_label, start, end, prev_start, prev_end, start_date_str, end_date_str = (
        _get_analytics_period(request)
    )

    _D0 = Decimal("0")
    all_orders    = Order.objects.all()
    period_orders = all_orders.filter(created_at__gte=start, created_at__lte=end)
    period_items  = OrderItem.objects.select_related(
        "order", "variant__product__category", "variant__product__brand"
    ).filter(order__created_at__gte=start, order__created_at__lte=end)

    wb = openpyxl.Workbook()

    # ── Styling helpers ───────────────────────────────────────────────────────
    GOLD_FILL   = PatternFill("solid", fgColor="C5A059")
    DARK_FILL   = PatternFill("solid", fgColor="1A1A1A")
    HEADER_FONT = Font(bold=True, color="0B0B0B", size=11)
    TITLE_FONT  = Font(bold=True, color="C5A059", size=13)
    NORMAL_FONT = Font(color="000000", size=10)
    thin        = Side(style="thin", color="DDDDDD")
    thin_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def style_header_row(ws, row, cols):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.fill = GOLD_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

    def auto_width(ws):
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                try:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                except Exception:
                    pass
            ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

    # ── Sheet 1: Sales Summary ────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Sales Summary"
    agg = period_orders.aggregate(
        gross=Sum("subtotal"), net=Sum("total"), disc=Sum("discount"),
        coupon=Sum("coupon_discount"), tax=Sum("tax"), ship=Sum("shipping"), cnt=Count("id"),
    )
    ws1["A1"] = f"Luxelle Analytics Report — {period_label}"
    ws1["A1"].font = TITLE_FONT
    ws1.merge_cells("A1:B1")

    headers = ["Metric", "Value"]
    for ci, h in enumerate(headers, 1):
        ws1.cell(row=2, column=ci, value=h)
    style_header_row(ws1, 2, 2)

    rows = [
        ("Gross Sales (₹)",       float(agg["gross"]  or 0)),
        ("Net Revenue (₹)",       float(agg["net"]    or 0)),
        ("Product Discounts (₹)", float(agg["disc"]   or 0)),
        ("Coupon Discounts (₹)",  float(agg["coupon"] or 0)),
        ("Taxes / GST (₹)",       float(agg["tax"]    or 0)),
        ("Delivery Charges (₹)",  float(agg["ship"]   or 0)),
        ("Total Orders",          agg["cnt"] or 0),
    ]
    for ri, (label, val) in enumerate(rows, 3):
        ws1.cell(row=ri, column=1, value=label)
        ws1.cell(row=ri, column=2, value=val)
        ws1.cell(row=ri, column=2).number_format = "#,##0.00"
    auto_width(ws1)

    # ── Sheet 2: Orders ───────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Orders")
    ws2.append(["Order Number", "Date", "Customer", "Payment", "Status", "Total (₹)"])
    style_header_row(ws2, 1, 6)
    pm_map = dict(Order.PAYMENT_CHOICES)
    for o in period_orders.select_related("user").order_by("-created_at")[:500]:
        ws2.append([
            o.order_number,
            o.created_at.strftime("%d %b %Y"),
            o.user.get_full_name() or o.user.email,
            pm_map.get(o.payment_method, o.payment_method),
            o.get_status_display(),
            float(o.total),
        ])
    auto_width(ws2)

    # ── Sheet 3: Top Products ─────────────────────────────────────────────────
    ws3 = wb.create_sheet("Top Products")
    ws3.append(["Product Name", "SKU", "Units Sold", "Revenue (₹)"])
    style_header_row(ws3, 1, 4)
    top_prods = (
        period_items
        .filter(variant__isnull=False)
        .values("variant__product__name", "sku")
        .annotate(units=Sum("quantity"), rev=Sum("line_total"))
        .order_by("-units")[:50]
    )
    for p in top_prods:
        ws3.append([p["variant__product__name"], p["sku"], p["units"], float(p["rev"] or 0)])
    auto_width(ws3)

    # ── Sheet 4: Category Revenue ─────────────────────────────────────────────
    ws4 = wb.create_sheet("Category Revenue")
    ws4.append(["Category", "Units Sold", "Revenue (₹)"])
    style_header_row(ws4, 1, 3)
    for c in (
        period_items
        .filter(variant__product__category__isnull=False)
        .values("variant__product__category__name")
        .annotate(units=Sum("quantity"), rev=Sum("line_total"))
        .order_by("-rev")
    ):
        ws4.append([c["variant__product__category__name"], c["units"], float(c["rev"] or 0)])
    auto_width(ws4)

    # ── Sheet 5: Brand Revenue ────────────────────────────────────────────────
    ws5 = wb.create_sheet("Brand Revenue")
    ws5.append(["Brand", "Units Sold", "Revenue (₹)"])
    style_header_row(ws5, 1, 3)
    for b in (
        period_items
        .filter(variant__product__brand__isnull=False)
        .values("variant__product__brand__name")
        .annotate(units=Sum("quantity"), rev=Sum("line_total"))
        .order_by("-rev")
    ):
        ws5.append([b["variant__product__brand__name"], b["units"], float(b["rev"] or 0)])
    auto_width(ws5)

    # ── Sheet 6: Payment Methods ──────────────────────────────────────────────
    ws6 = wb.create_sheet("Payment Methods")
    ws6.append(["Payment Method", "Order Count"])
    style_header_row(ws6, 1, 2)
    for p in period_orders.values("payment_method").annotate(cnt=Count("id")).order_by("-cnt"):
        ws6.append([pm_map.get(p["payment_method"], p["payment_method"]), p["cnt"]])
    auto_width(ws6)

    # ── Sheet 7: Order Status ─────────────────────────────────────────────────
    ws7 = wb.create_sheet("Order Status")
    ws7.append(["Status", "Item Count"])
    style_header_row(ws7, 1, 2)
    st_map = dict(OrderItem.STATUS_CHOICES)
    for s in (
        period_items.values("status").annotate(cnt=Count("id")).order_by("-cnt")
    ):
        ws7.append([st_map.get(s["status"], s["status"]), s["cnt"]])
    auto_width(ws7)

    # ── Stream ────────────────────────────────────────────────────────────────
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"luxelle_analytics_{period_label.replace(' ', '_').lower()}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ── Admin Reviews ────────────────────────────────────────────────────────────

@admin_required
@never_cache
def admin_reviews_view(request):
    from .models import Review
    from django.db.models import Avg

    qs = Review.objects.select_related("product", "user").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(product__name__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__email__icontains=q)
        )

    rating_filter = request.GET.get("rating", "").strip()
    if rating_filter.isdigit() and 1 <= int(rating_filter) <= 5:
        qs = qs.filter(rating=int(rating_filter))

    visibility = request.GET.get("visibility", "").strip()
    if visibility == "hidden":
        qs = qs.filter(is_hidden=True)
    elif visibility == "visible":
        qs = qs.filter(is_hidden=False)

    total_reviews = qs.count()
    avg = qs.aggregate(avg=Avg("rating"))["avg"]

    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, "admin_panel/reviews.html", {
        "active": "reviews",
        "reviews": page_obj,
        "paginator": paginator,
        "q": q,
        "rating_filter": rating_filter,
        "visibility": visibility,
        "total_reviews": total_reviews,
        "avg_rating": avg,
    })


@admin_required
@require_POST
def admin_review_toggle_view(request, review_id):
    from .models import Review
    review = Review.objects.filter(pk=review_id).first()
    if not review:
        messages.error(request, "Review not found.")
    else:
        review.is_hidden = not review.is_hidden
        review.save(update_fields=["is_hidden"])
        action = "hidden" if review.is_hidden else "restored"
        messages.success(request, f"Review has been {action} successfully.")
    # preserve filters in redirect
    next_url = request.POST.get("next", "")
    if next_url:
        return HttpResponseRedirect(next_url)
    return redirect("admin_reviews")
