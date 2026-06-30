
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

@admin_required
def admin_dashboard_view(request):
    from django.utils import timezone

    non_superusers = CustomUser.objects.filter(is_staff=False)
    first_of_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    orders        = Order.objects.all()
    total_revenue = (orders.exclude(status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED])
                     .aggregate(s=Sum("total"))["s"] or 0)

    context = {
        "total_users":       non_superusers.count(),
        "active_count":      non_superusers.filter(is_active=True).count(),
        "blocked_count":     non_superusers.filter(is_active=False).count(),
        "this_month_count":  non_superusers.filter(date_joined__gte=first_of_month).count(),
        "recent_users":      non_superusers.order_by("-date_joined")[:5],
        "total_orders":      orders.count(),
        "total_revenue":     total_revenue,
    }
    return render(request, "admin_panel/dashboard.html", context)


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
    try:
        user = CustomUser.objects.get(id=user_id, is_staff=False)
        user_name = user.get_full_name()
        user.delete()
        messages.success(request, f"User {user_name} has been permanently deleted.")
        admin_email = request.session.get('_admin_email', 'unknown')
        logger.info(f"[ADMIN] User {user_name} deleted by {admin_email}")
    except CustomUser.DoesNotExist:
        messages.error(request, "User not found.")
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
    (OrderItem.STATUS_RETURN_REJECTED,  "Return Rejected"),
    (OrderItem.STATUS_RETURNED,         "Returned"),
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
        "count_rejected":  count(OrderItem.STATUS_RETURN_REJECTED),
        "count_returned":  count(OrderItem.STATUS_RETURNED),
    }
    return render(request, "admin_panel/return_requests.html", context)


@admin_required
@require_POST
def admin_return_approve_view(request, item_id):
    item = OrderItem.objects.filter(pk=item_id).first()
    if not item or not item.can_approve_return:
        messages.error(request, "This return cannot be approved.")
        return redirect("admin_returns")

    from wallet import services as wallet_services

    with transaction.atomic():
        item.status = OrderItem.STATUS_RETURN_APPROVED
        item.save(update_fields=["status"])
        OrderStatusEvent.objects.create(
            order_item=item, status=OrderItem.STATUS_RETURN_APPROVED,
            note="Return approved by admin.",
        )
        # Approved return → item is being refunded → drop it from the payable total.
        total_before = item.order.total
        item.order.recalculate_totals()
        refund = total_before - item.order.total

        # Returns are only possible on delivered items, which were always paid
        # for (cash or online), so an approved return always refunds to wallet.
        if refund > 0:
            wallet_services.refund_item(
                item, refund, f"Refund for returned item: {item.product_name}"
            )

    note = f" Rs. {refund} refunded to the customer's wallet." if refund > 0 else ""
    messages.success(request, f"Return approved for {item.product_name}.{note}")
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
    item = OrderItem.objects.select_related("variant").filter(pk=item_id).first()
    if not item:
        messages.error(request, "Item not found.")
        return redirect("admin_returns")

    new_status = request.POST.get("status")
    allowed = dict(item.return_next_statuses())
    if new_status not in allowed:
        messages.error(request, "That return step isn't allowed.")
        return redirect(request.POST.get("next") or "admin_returns")

    with transaction.atomic():
        if new_status == OrderItem.STATUS_RETURNED and item.variant:
            item.variant.stock += item.quantity
            item.variant.save(update_fields=["stock"])

        item.status = new_status
        item.save(update_fields=["status"])

        if new_status == OrderItem.STATUS_RETURNED:
            note = "Item inspected — returned to stock. Refund to be processed."
        elif new_status == OrderItem.STATUS_RETURN_REPAIR:
            note = "Item inspected — sent for repair (not restocked)."
        else:
            note = f"Return step updated to {allowed[new_status]} by admin."
        OrderStatusEvent.objects.create(order_item=item, status=new_status, note=note)

        item.order.recalculate_totals()

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
    start_date_str, end_date_str)."""
    import datetime as dt
    from django.utils import timezone

    today  = timezone.now().date()
    now    = timezone.now()
    period = request.GET.get("period", "month")
    start_date_str = request.GET.get("start_date", "")
    end_date_str   = request.GET.get("end_date", "")

    def make_aware(d):
        return timezone.make_aware(dt.datetime.combine(d, dt.time.min))

    if period == "today":
        start      = make_aware(today)
        end        = now
        prev_start = make_aware(today - dt.timedelta(days=1))
        prev_end   = start
        label      = "Today"

    elif period == "week":
        week_start = today - dt.timedelta(days=today.weekday())
        start      = make_aware(week_start)
        end        = now
        prev_start = make_aware(week_start - dt.timedelta(weeks=1))
        prev_end   = start
        label      = "This Week"

    elif period == "year":
        start      = make_aware(dt.date(today.year, 1, 1))
        end        = now
        prev_start = make_aware(dt.date(today.year - 1, 1, 1))
        prev_end   = start
        label      = "This Year"

    elif period == "custom" and start_date_str and end_date_str:
        try:
            _s = dt.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            _e = dt.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            start      = make_aware(_s)
            end        = make_aware(_e + dt.timedelta(days=1))
            delta      = end - start
            prev_start = start - delta
            prev_end   = start
            label      = f"{start_date_str} – {end_date_str}"
        except ValueError:
            period = "month"

    if period == "month":
        start = make_aware(dt.date(today.year, today.month, 1))
        end   = now
        prev_m = today.month - 1 or 12
        prev_y = today.year if today.month > 1 else today.year - 1
        prev_start = make_aware(dt.date(prev_y, prev_m, 1))
        prev_end   = start
        label      = "This Month"

    return period, label, start, end, prev_start, prev_end, start_date_str, end_date_str


@admin_required
def admin_analytics_view(request):
    import json
    import datetime as dt
    from decimal import Decimal
    from django.db.models import Sum, Count, Avg, Q
    from django.db.models.functions import TruncDate, TruncMonth
    from .models import ProductVariant

    period, period_label, start, end, prev_start, prev_end, start_date_str, end_date_str = (
        _get_analytics_period(request)
    )

    # ── Base querysets ────────────────────────────────────────────────────────
    all_orders    = Order.objects.all()
    period_orders = all_orders.filter(created_at__gte=start, created_at__lte=end)
    prev_orders   = all_orders.filter(created_at__gte=prev_start, created_at__lte=prev_end)
    all_items     = OrderItem.objects.select_related("order", "variant__product__category", "variant__product__brand")
    period_items  = all_items.filter(order__created_at__gte=start, order__created_at__lte=end)

    _D0 = Decimal("0")

    # ── Overview KPIs ─────────────────────────────────────────────────────────
    total_revenue = (
        all_orders
        .exclude(status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED])
        .aggregate(s=Sum("total"))["s"] or _D0
    )
    total_orders    = all_orders.count()
    total_customers = CustomUser.objects.filter(is_staff=False).count()
    total_products  = Product.objects.filter(is_deleted=False).count()

    delivered_count = OrderItem.objects.filter(status=OrderItem.STATUS_DELIVERED).values("order").distinct().count()
    pending_count   = OrderItem.objects.filter(status=OrderItem.STATUS_PENDING).values("order").distinct().count()
    cancelled_count = OrderItem.objects.filter(status=OrderItem.STATUS_CANCELLED).values("order").distinct().count()
    returned_count  = OrderItem.objects.filter(status=OrderItem.STATUS_RETURNED).values("order").distinct().count()

    try:
        from wallet.models import Wallet
        total_wallet_balance = Wallet.objects.aggregate(s=Sum("balance"))["s"] or _D0
    except Exception:
        total_wallet_balance = _D0

    aov = (
        all_orders
        .exclude(status__in=[Order.STATUS_CANCELLED, Order.STATUS_RETURNED])
        .aggregate(a=Avg("total"))["a"] or _D0
    )

    # ── Sales Analytics (period) ──────────────────────────────────────────────
    agg = period_orders.aggregate(
        gross_sales  = Sum("subtotal"),
        net_revenue  = Sum("total"),
        discounts    = Sum("discount"),
        coupon_disc  = Sum("coupon_discount"),
        taxes        = Sum("tax"),
        delivery     = Sum("shipping"),
        order_count  = Count("id"),
    )
    gross_sales  = agg["gross_sales"]  or _D0
    net_revenue  = agg["net_revenue"]  or _D0
    discounts    = agg["discounts"]    or _D0
    coupon_disc  = agg["coupon_disc"]  or _D0
    taxes        = agg["taxes"]        or _D0
    delivery     = agg["delivery"]     or _D0
    order_count  = agg["order_count"]  or 0

    prev_net = prev_orders.aggregate(s=Sum("total"))["s"] or _D0
    if prev_net > 0:
        growth_pct = round(float((net_revenue - prev_net) / prev_net * 100), 1)
    else:
        growth_pct = 100.0 if net_revenue > 0 else 0.0

    # ── Chart Data ────────────────────────────────────────────────────────────
    daily_qs = (
        period_orders
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(revenue=Sum("total"), orders=Count("id"))
        .order_by("day")
    )
    chart_labels  = [str(d["day"]) for d in daily_qs]
    chart_revenue = [float(d["revenue"] or 0) for d in daily_qs]
    chart_orders  = [int(d["orders"] or 0) for d in daily_qs]

    today_dt = dt.date.today()
    twelve_ago = dt.date(
        today_dt.year - 1 if today_dt.month > 1 else today_dt.year - 2,
        today_dt.month - 1 if today_dt.month > 1 else 12,
        1,
    )
    from django.utils import timezone as tz
    monthly_qs = (
        all_orders
        .filter(created_at__gte=tz.make_aware(dt.datetime(twelve_ago.year, twelve_ago.month, 1)))
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(revenue=Sum("total"))
        .order_by("month")
    )
    monthly_labels  = [d["month"].strftime("%b %Y") for d in monthly_qs]
    monthly_revenue = [float(d["revenue"] or 0) for d in monthly_qs]

    cat_qs = (
        period_items
        .filter(variant__product__category__isnull=False)
        .values("variant__product__category__name")
        .annotate(revenue=Sum("line_total"))
        .order_by("-revenue")[:8]
    )
    cat_labels  = [d["variant__product__category__name"] for d in cat_qs]
    cat_revenue = [float(d["revenue"] or 0) for d in cat_qs]

    brand_qs = (
        period_items
        .filter(variant__product__brand__isnull=False)
        .values("variant__product__brand__name")
        .annotate(revenue=Sum("line_total"))
        .order_by("-revenue")[:8]
    )
    brand_labels  = [d["variant__product__brand__name"] for d in brand_qs]
    brand_revenue = [float(d["revenue"] or 0) for d in brand_qs]

    pay_qs = (
        period_orders
        .values("payment_method")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    pm_map          = dict(Order.PAYMENT_CHOICES)
    payment_labels  = [pm_map.get(d["payment_method"], d["payment_method"]) for d in pay_qs]
    payment_counts  = [d["count"] for d in pay_qs]

    stat_qs = (
        period_items
        .values("status")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )
    st_map        = dict(OrderItem.STATUS_CHOICES)
    status_labels = [st_map.get(d["status"], d["status"]) for d in stat_qs]
    status_counts = [d["count"] for d in stat_qs]

    # ── Product Analytics ─────────────────────────────────────────────────────
    raw_top = (
        period_items
        .filter(variant__isnull=False)
        .values("variant__id", "variant__product__name", "sku")
        .annotate(units_sold=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-units_sold")[:10]
    )
    top_products_list = []
    for p in raw_top:
        try:
            v     = ProductVariant.objects.prefetch_related("images").get(id=p["variant__id"])
            img   = v.images.filter(is_primary=True).first() or v.images.first()
            stock = v.stock
            img_url = img.image.url if img else None
        except ProductVariant.DoesNotExist:
            stock = 0; img_url = None
        top_products_list.append({
            "name":       p["variant__product__name"],
            "sku":        p["sku"],
            "units_sold": p["units_sold"],
            "revenue":    p["revenue"] or _D0,
            "stock":      stock,
            "image":      img_url,
        })

    # ── Category & Brand tables ───────────────────────────────────────────────
    top_categories = list(
        period_items
        .filter(variant__product__category__isnull=False)
        .values("variant__product__category__name")
        .annotate(units=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:10]
    )
    top_brands = list(
        period_items
        .filter(variant__product__brand__isnull=False)
        .values("variant__product__brand__name")
        .annotate(units=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:10]
    )

    # ── Customer Analytics ────────────────────────────────────────────────────
    total_cust  = CustomUser.objects.filter(is_staff=False).count()
    new_cust    = CustomUser.objects.filter(is_staff=False, date_joined__gte=start, date_joined__lte=end).count()
    active_cust = period_orders.values("user").distinct().count()
    repeat_cust = (
        Order.objects
        .values("user")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
        .count()
    )
    top_spenders = list(
        period_orders
        .values("user__email", "user__full_name")
        .annotate(total_spent=Sum("total"), order_count=Count("id"))
        .order_by("-total_spent")[:10]
    )

    # ── Order Analytics ───────────────────────────────────────────────────────
    order_statuses = [
        ("pending",          "Pending"),
        ("confirmed",        "Confirmed"),
        ("packed",           "Packed"),
        ("shipped",          "Shipped"),
        ("out_for_delivery", "Out for Delivery"),
        ("delivered",        "Delivered"),
        ("cancelled",        "Cancelled"),
        ("return_requested", "Return Requested"),
        ("returned",         "Returned"),
    ]
    order_status_counts = []
    for st_val, st_label in order_statuses:
        cnt = OrderItem.objects.filter(
            order__created_at__gte=start,
            order__created_at__lte=end,
            status=st_val,
        ).count()
        order_status_counts.append({"label": st_label, "count": cnt})

    try:
        from payments.models import Payment
        failed_payments     = Payment.objects.filter(status=Payment.STATUS_FAILED).count()
        successful_payments = Payment.objects.filter(status=Payment.STATUS_PAID).count()
    except Exception:
        failed_payments = successful_payments = 0

    # ── Inventory Analytics ───────────────────────────────────────────────────
    active_products_count   = Product.objects.filter(is_deleted=False, is_listed=True).count()
    inactive_products_count = Product.objects.filter(is_deleted=False, is_listed=False).count()
    low_stock_count         = ProductVariant.objects.filter(is_deleted=False, stock__gt=0, stock__lte=10).count()
    out_of_stock_count      = ProductVariant.objects.filter(is_deleted=False, stock=0).count()
    recent_products         = (
        Product.objects
        .filter(is_deleted=False)
        .select_related("category", "brand")
        .prefetch_related("variants__images")
        .order_by("-created_at")[:8]
    )

    # ── Coupon Analytics ──────────────────────────────────────────────────────
    try:
        from coupons.models import Coupon, CouponUsage
        coupons_created       = Coupon.objects.count()
        coupons_used          = CouponUsage.objects.filter(used_at__gte=start, used_at__lte=end).values("coupon").distinct().count()
        total_coupon_discount = period_orders.aggregate(s=Sum("coupon_discount"))["s"] or _D0
        most_used             = (
            CouponUsage.objects
            .filter(used_at__gte=start, used_at__lte=end)
            .values("coupon__code")
            .annotate(uses=Count("id"))
            .order_by("-uses")
            .first()
        )
        most_used_coupon = most_used["coupon__code"] if most_used else "—"
    except Exception:
        coupons_created = coupons_used = 0
        total_coupon_discount = _D0
        most_used_coupon = "—"

    # ── Payment Analytics ─────────────────────────────────────────────────────
    razorpay_orders = period_orders.filter(payment_method=Order.PAYMENT_RAZORPAY).count()
    cod_orders      = period_orders.filter(payment_method=Order.PAYMENT_COD).count()
    wallet_orders   = period_orders.filter(payment_method=Order.PAYMENT_WALLET).count()

    context = {
        "active":        "analytics",
        "period":        period,
        "period_label":  period_label,
        "start_date":    start_date_str,
        "end_date":      end_date_str,
        # Overview
        "total_revenue":       total_revenue,
        "total_orders":        total_orders,
        "total_customers":     total_customers,
        "total_products":      total_products,
        "delivered_count":     delivered_count,
        "pending_count":       pending_count,
        "cancelled_count":     cancelled_count,
        "returned_count":      returned_count,
        "total_wallet_balance":total_wallet_balance,
        "aov":                 aov,
        # Sales
        "gross_sales":    gross_sales,
        "net_revenue":    net_revenue,
        "discounts":      discounts,
        "coupon_disc":    coupon_disc,
        "taxes":          taxes,
        "delivery":       delivery,
        "order_count":    order_count,
        "growth_pct":     growth_pct,
        # Charts (JSON-safe)
        "chart_labels":    json.dumps(chart_labels),
        "chart_revenue":   json.dumps(chart_revenue),
        "chart_orders":    json.dumps(chart_orders),
        "monthly_labels":  json.dumps(monthly_labels),
        "monthly_revenue": json.dumps(monthly_revenue),
        "cat_labels":      json.dumps(cat_labels),
        "cat_revenue":     json.dumps(cat_revenue),
        "brand_labels":    json.dumps(brand_labels),
        "brand_revenue":   json.dumps(brand_revenue),
        "payment_labels":  json.dumps(payment_labels),
        "payment_counts":  json.dumps(payment_counts),
        "status_labels":   json.dumps(status_labels),
        "status_counts":   json.dumps(status_counts),
        # Products
        "top_products_list": top_products_list,
        "top_products_labels": json.dumps([p["name"] for p in top_products_list[:8]]),
        "top_products_units":  json.dumps([float(p["units_sold"]) for p in top_products_list[:8]]),
        "top_products_rev":    json.dumps([float(p["revenue"]) for p in top_products_list[:8]]),
        # Category & Brand
        "top_categories": top_categories,
        "top_brands":     top_brands,
        # Customers
        "total_cust":    total_cust,
        "new_cust":      new_cust,
        "active_cust":   active_cust,
        "repeat_cust":   repeat_cust,
        "top_spenders":  top_spenders,
        # Orders
        "order_status_counts":  order_status_counts,
        "failed_payments":      failed_payments,
        "successful_payments":  successful_payments,
        # Inventory
        "active_products_count":   active_products_count,
        "inactive_products_count": inactive_products_count,
        "low_stock_count":         low_stock_count,
        "out_of_stock_count":      out_of_stock_count,
        "recent_products":         recent_products,
        # Coupons
        "coupons_created":       coupons_created,
        "coupons_used":          coupons_used,
        "total_coupon_discount": total_coupon_discount,
        "most_used_coupon":      most_used_coupon,
        # Payments
        "razorpay_orders": razorpay_orders,
        "cod_orders":      cod_orders,
        "wallet_orders":   wallet_orders,
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

