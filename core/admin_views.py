
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.cache import never_cache
from django.urls import reverse
from .decorators import admin_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, Min, Sum
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps
import random
import io
from .forms import SetNewPasswordForm, CategoryForm, ProductForm, ProductVariantForm
from .models import CustomUser, Category, Product, Brand, Material, ProductVariant, VariantImage
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

    context = {
        "total_users":       non_superusers.count(),
        "active_count":      non_superusers.filter(is_active=True).count(),
        "blocked_count":     non_superusers.filter(is_active=False).count(),
        "this_month_count":  non_superusers.filter(date_joined__gte=first_of_month).count(),
        "recent_users":      non_superusers.order_by("-date_joined")[:5],
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

@admin_required
def admin_category_list_view(request):
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

    context = {
        "categories":   page_obj.object_list,
        "page_obj":     page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "search_query": search_query,
        "status":       status,
        "trash_count":  Category.objects.filter(is_deleted=True).count(),
    }
    return render(request, "admin_panel/category_list.html", context)


@admin_required
def admin_category_add_view(request):
    if request.method == "POST":
        form = CategoryForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Category added successfully.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
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
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
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


def _generate_unique_sku(product):
    base = (product.slug or "prod").upper().replace("-", "")[:8]
    while True:
        sku = f"{base}-{random.randint(1000, 9999)}"
        if not ProductVariant.objects.filter(sku=sku).exists():
            return sku


def _build_variant_name(variant, product):
    parts = []
    if variant.color:
        parts.append(variant.color.strip())
    if variant.material_id:
        parts.append(variant.material.name)
    return " ".join(p for p in parts if p).strip() or product.name


PRODUCT_IMAGE_SIZE  = (800, 800)
ALLOWED_IMG_FORMATS = {"JPEG", "PNG", "WEBP"}


def process_product_image(uploaded_file):
    """Validate format, square-crop without distortion (cover), resize to a uniform
    size and re-encode as an optimized JPEG. Raises ValidationError on bad input."""
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        fmt = (img.format or "").upper()
        if fmt == "JPG":
            fmt = "JPEG"
        if fmt not in ALLOWED_IMG_FORMATS:
            raise ValidationError("Only JPG, PNG, or WEBP images are allowed.")
        img = img.convert("RGB")
        img = ImageOps.fit(img, PRODUCT_IMAGE_SIZE, Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        buffer.seek(0)
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Could not process the image. Please upload a valid JPG, PNG, or WEBP file.")
    base = (getattr(uploaded_file, "name", "") or "image").rsplit(".", 1)[0]
    return ContentFile(buffer.read(), name=f"{base}.jpg")


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
    if len(name) < 2:
        return JsonResponse({"error": "Brand name must be at least 2 characters."}, status=400)
    brand = (Brand.objects.filter(name__iexact=name, is_deleted=False).first()
             or Brand.objects.create(name=name))
    return JsonResponse({"id": brand.id, "name": brand.name})


@admin_required
def admin_material_add_ajax(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request."}, status=400)
    name = request.POST.get("name", "").strip()
    if len(name) < 2:
        return JsonResponse({"error": "Material name must be at least 2 characters."}, status=400)
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
        processed_images = []
        if not extra_errors:
            for img in images:
                try:
                    processed_images.append(process_product_image(img))
                except ValidationError as exc:
                    extra_errors.append(exc.messages[0])
                    break

        if product_form.is_valid() and variant_form.is_valid() and not extra_errors:
            with transaction.atomic():
                product = product_form.save()
                variant = variant_form.save(commit=False)
                variant.product      = product
                variant.is_default   = True
                variant.variant_name = _build_variant_name(variant, product)
                if not variant.sku:
                    variant.sku = _generate_unique_sku(product)
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
        processed_images = []
        if not extra_errors:
            for img in new_images:
                try:
                    processed_images.append(process_product_image(img))
                except ValidationError as exc:
                    extra_errors.append(exc.messages[0])
                    break

        if product_form.is_valid() and variant_form.is_valid() and not extra_errors:
            with transaction.atomic():
                product = product_form.save()
                v = variant_form.save(commit=False)
                v.product      = product
                v.is_default   = True
                v.variant_name = _build_variant_name(v, product)
                if not v.sku:
                    v.sku = _generate_unique_sku(product)
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
    
