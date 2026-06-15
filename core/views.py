from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, Min, Avg, F


import logging
import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(console_handler)
logger.setLevel(logging.INFO)
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST


from .decorators import anonymous_required, otp_session_required, password_reset_session_required, _parse_iso
from .forms import (
    AddressForm,
    ChangePasswordForm,
    EmailChangeForm,
    ForgotPasswordForm,
    LoginForm,
    OTPVerificationForm,
    ProfileEditForm,
    RegistrationForm,
    SetNewPasswordForm,
    UserProfileForm,
)
from .models import Address, CustomUser, UserProfile, OTPVerification, Product, Category, Brand, Review, Wishlist, Cart, CartItem, ProductVariant
from .utils import (
    check_resend_cooldown,
    clear_pending_user_session,
    create_otp_for_user,
    get_pending_user,
    send_otp_email,
    set_pending_user_session,
    verify_otp,
    generate_otp,
    OTP_EXPIRY_MINUTES,
    SESSION_EXPIRY_MINUTES,
)

logger = logging.getLogger(__name__)



# Internal helpers

_REGISTRATION_SESSION_KEYS = (
    "pending_registration",
    "pending_otp",
    "pending_otp_expires_at",
    "pending_otp_sent_at",
    "otp_purpose",
)


def _clear_registration_session(request):
    
    for key in _REGISTRATION_SESSION_KEYS:
        request.session.pop(key, None)


def _get_pending_otp_resend_seconds_remaining(request):
    sent_at_str = request.session.get("pending_otp_sent_at")
    if not sent_at_str:
        return 0

    try:
        sent_at = datetime.fromisoformat(sent_at_str)
        if timezone.is_naive(sent_at):
            sent_at = timezone.make_aware(sent_at)
        elapsed = (timezone.now() - sent_at).total_seconds()
        cooldown = OTPVerification.RESEND_COOLDOWN_SECONDS
        remaining = math.ceil(cooldown - elapsed)
        return max(0, remaining)
    except (ValueError, TypeError):
        return 0


# HOME

def home_view(request):
    base_qs = Product.objects.filter(
        is_deleted=False,
        is_listed=True,
        category__is_deleted=False,
        category__is_listed=True,
    ).select_related("brand", "category").prefetch_related("variants__images")

    in_stock_qs = base_qs.filter(
        variants__is_deleted=False,
        variants__is_listed=True,
        variants__stock__gt=0,
    ).distinct()

    featured_products = in_stock_qs.filter(is_featured=True).order_by(
        F("featured_at").desc(nulls_last=True), "-created_at"
    )[:4]

    wishlist_ids = set()
    if request.user.is_authenticated:
        wishlist_ids = set(Wishlist.objects.filter(user=request.user).values_list("product_id", flat=True))

    context = {
        "featured_products": featured_products,
        "wishlist_ids": wishlist_ids,
    }
    return render(request, "home.html", context)


# DEALS

def deals_view(request):
    deal_products = Product.objects.filter(
        is_deleted=False,
        is_listed=True,
        is_deal_of_day=True,
        category__is_deleted=False,
        category__is_listed=True,
        variants__is_deleted=False,
        variants__is_listed=True,
        variants__stock__gt=0,
    ).select_related("brand", "category").prefetch_related("variants__images").distinct().order_by("-updated_at")

    wishlist_ids = set()
    if request.user.is_authenticated:
        wishlist_ids = set(Wishlist.objects.filter(user=request.user).values_list("product_id", flat=True))

    context = {
        "deal_products": deal_products,
        "wishlist_ids": wishlist_ids,
    }
    return render(request, "deals.html", context)


# REGISTRATION


@never_cache
@anonymous_required()
def register_view(request):
    if not request.session.get("pending_registration"):
        _clear_registration_session(request)

    if request.user.is_authenticated:
        logout(request)

    initial_data = request.session.get("pending_registration", {})
    form = RegistrationForm(request.POST or None, initial=initial_data)

    if request.method == "POST":
        if form.is_valid():
            registration_data = {
                "email": form.cleaned_data["email"],
                "password": form.cleaned_data["password1"],
                "full_name": form.cleaned_data["full_name"],
                "phone": form.cleaned_data.get("phone", ""),
            }
            request.session["pending_registration"] = registration_data

            otp_code = generate_otp()
            expires_at = timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

            class _TempUser:
                email = registration_data["email"]
                full_name = registration_data.get("full_name", "")
                first_name = full_name.split(maxsplit=1)[0] if full_name else ""

            if send_otp_email(_TempUser(), otp_code, purpose="registration"):
                request.session["pending_otp"] = otp_code
                request.session["pending_otp_expires_at"] = expires_at.isoformat()
                request.session["pending_otp_sent_at"] = timezone.now().isoformat()
                request.session.set_expiry(SESSION_EXPIRY_MINUTES * 60)
                request.session.save()
                return redirect('verify_otp')
            else:
                messages.error(request, "Failed to send verification email. Please try again.")
        else:
            request.session["pending_registration"] = request.POST.dict()

    return render(request, "register.html", {"form": form})


# OTP VERIFICATION

@never_cache
@otp_session_required
def verify_otp_view(request):    
    registration_data = request.session.get("pending_registration")
    if not registration_data:
        _clear_registration_session(request)
        messages.error(request, "Session expired. Please register again.")
        return redirect("register")

    pending_otp    = request.session.get("pending_otp")
    expires_at = _parse_iso(request.session.get("pending_otp_expires_at"))

    if not pending_otp or not expires_at:
        _clear_registration_session(request)
        messages.error(request, "Session data corrupted. Please register again.")
        return redirect("register")

    form = OTPVerificationForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            otp_input = form.cleaned_data["otp"]

            if timezone.now() > expires_at:
                messages.error(request, "OTP has expired. Please request a new OTP.")
                return render(request, "verify_otp.html", {
                    "form": form,
                    "email": registration_data["email"],
                    "seconds_remaining": _get_pending_otp_resend_seconds_remaining(request),
                })


            if otp_input != pending_otp:
                messages.error(request, "Invalid OTP. Please try again.")
                return render(request, "verify_otp.html", {
                    "form": form,
                    "email": registration_data["email"],
                    "seconds_remaining": _get_pending_otp_resend_seconds_remaining(request),
                })

            try:
                full_name = registration_data.get("full_name", "")
                name_parts = full_name.split(maxsplit=1)
                first_name = name_parts[0] if len(name_parts) > 0 else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""
                user = CustomUser.objects.create_user(
                    email      = registration_data["email"],
                    password   = registration_data["password"],
                    first_name = first_name,
                    last_name  = last_name,
                    full_name  = full_name,
                    phone      = registration_data.get("phone", ""),
                )
                user.is_active   = True
                user.is_verified = True
                user.save(update_fields=["is_active", "is_verified"])
            except Exception as exc:
                logger.error(f"[VERIFY_OTP] Failed to create user: {exc}")
                messages.error(request, "Account creation failed. Please try again.")
                return redirect("register")

            _clear_registration_session(request)

            request.session.flush()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, "Email verified! Welcome to Luxelle.")
            return redirect("home")

    seconds_remaining = _get_pending_otp_resend_seconds_remaining(request)

    return render(request, "verify_otp.html", {
        "form":               form,
        "email":              registration_data["email"],
        "seconds_remaining":  seconds_remaining,
    })


@never_cache
@otp_session_required
def resend_otp_view(request):
    registration_data = request.session.get("pending_registration")
    if not registration_data:
        messages.error(request, "Session expired. Please register again.")
        return redirect("register")

    otp_code = generate_otp()
    expires_at = timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

    class _TempUser:
        email = registration_data["email"]
        full_name = registration_data.get("full_name", "")
        first_name = full_name.split(maxsplit=1)[0] if full_name else ""

    email_sent = send_otp_email(_TempUser(), otp_code, purpose="registration")
    if email_sent:
        logger.debug("SESSION BEFORE RESEND (registration): %s", request.session.items())
        request.session["pending_otp"] = otp_code
        request.session["pending_otp_expires_at"] = expires_at.isoformat()
        request.session["pending_otp_sent_at"] = timezone.now().isoformat()
        request.session["pending_registration"] = registration_data
        request.session["otp_purpose"] = "registration"
        request.session.set_expiry(SESSION_EXPIRY_MINUTES * 60)
        request.session.modified = True
        request.session.save()
        messages.success(request, "OTP resent successfully.")
    else:
        messages.error(request, "Failed to resend OTP. Please try again.")
    return redirect('verify_otp')

@never_cache
@anonymous_required()
def login_view(request):

    form = LoginForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            email    = form.cleaned_data["email"]
            password = form.cleaned_data["password"]

            user = authenticate(request, username=email, password=password)

            if request.session.get('_is_admin'):
                request.session.pop('_is_admin', None)
                request.session.pop('_admin_user_id', None)
                request.session.pop('_admin_email', None)
                logger.debug('Cleared admin session flags before user login')

            if user is None:
                try:
                    existing = CustomUser.objects.get(email=email)
                    if existing.is_staff:
                        messages.error(request, "Invalid email or password.")
                    elif not existing.is_active and existing.check_password(password):
                        messages.error(request, "Your account has been suspended. Please contact support.")
                    else:
                        messages.error(request, "Invalid email or password.")
                except CustomUser.DoesNotExist:
                    messages.error(request, "Invalid email or password.")
                return render(request, "login.html", {"form": form})
            else:
                if user.is_superuser:
                    messages.error(request, "This account is for admin use only. Please login via the admin portal.")
                    return render(request, "login.html", {"form": form})
                clear_pending_user_session(request)
                request.session.cycle_key()
                login(request, user)
                request.session.pop('_is_admin', None)
                request.session.pop('_admin_user_id', None)
                messages.success(request, f"Welcome back, {user.first_name or user.email}!")
                next_url = request.GET.get("next", "home")
                return redirect(next_url)

    return render(request, "login.html", {"form": form})



# LOGOUT

@never_cache
@require_POST
def logout_view(request):
    admin_keys = {}
    if request.session.get("_is_admin"):
        admin_keys["_is_admin"] = request.session.get("_is_admin")
        admin_keys["_admin_user_id"] = request.session.get("_admin_user_id")
    logout(request)
    for key, value in admin_keys.items():
        request.session[key] = value
    request.session.modified = True
    messages.info(request, "You have been logged out.")
    return redirect("login")


@login_required
@never_cache
def logged_out_view(request):
    messages.info(request, "You have been logged out.")
    return render(request, "logged_out.html")


# FORGOT PASSWORD 

@never_cache

def forgot_password_view(request):
    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user = form.get_user()   
            otp_obj    = create_otp_for_user(user, purpose="password_reset")
            email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")

            if email_sent:
                set_pending_user_session(request, user.id, purpose="password_reset")
                request.session.save()
                messages.success(request, f"An OTP has been sent to {user.email}.")
                return redirect("forgot_password_otp")
            else:
                messages.error(request, "Failed to send OTP email. Please try again.")

    return render(request, "forgot_password.html", {"form": form})


# FORGOT PASSWORD 


@never_cache

def forgot_password_otp_view(request):
    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please request a new OTP.")
        return redirect("forgot_password")
    form = OTPVerificationForm(request.POST or None)
    seconds_remaining = _get_pending_otp_resend_seconds_remaining(request)

    if request.method == "POST":
        if form.is_valid():
            otp_input = form.cleaned_data["otp"]
            valid, error_msg = verify_otp(user, otp_input, purpose="password_reset")

            if valid:
                request.session["password_reset_verified"] = True
                request.session["password_reset_user_id"]  = str(user.pk)
                clear_pending_user_session(request)
                return redirect("set_new_password")
            else:
                messages.error(request, error_msg)

    return render(request, "forgot_password_otp.html", {"form": form, "email": user.email, "seconds_remaining": seconds_remaining})


@never_cache
@anonymous_required()
def resend_forgot_password_otp_view(request):
    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please start again.")
        return redirect("forgot_password")

    allowed, seconds_left = check_resend_cooldown(user, purpose="password_reset")
    if not allowed:
        messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new OTP.")
        return redirect("forgot_password_otp")

    logger.debug("SESSION BEFORE RESEND (forgot_password): %s", request.session.items())
    otp_obj = create_otp_for_user(user, purpose="password_reset")
    request.session["otp_purpose"] = "password_reset"
    request.session.save()
    logger.debug("SESSION AFTER RESEND (forgot_password): %s", request.session.items())
    email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")

    if email_sent:
        set_pending_user_session(request, user.pk, purpose="password_reset")
        messages.success(request, "OTP sent successfully.")
    else:
        messages.error(request, "Failed to resend OTP. Please try again.")

    from .forms import OTPVerificationForm
    form = OTPVerificationForm()
    seconds_remaining = _get_pending_otp_resend_seconds_remaining(request)
    return render(request, "forgot_password_otp.html", {"form": form, "email": user.email, "seconds_remaining": seconds_remaining})

# SET NEW PASSWORD

@never_cache
@anonymous_required()
@password_reset_session_required
def set_new_password_view(request):
    user_id = request.session.get("password_reset_user_id")
    try:
        user = CustomUser.objects.get(pk=user_id)
    except (CustomUser.DoesNotExist, Exception):
        messages.error(request, "Session expired. Please start again.")
        return redirect("forgot_password")

    form = SetNewPasswordForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user.set_password(form.cleaned_data["new_password"])
            user.save(update_fields=["password"])
            request.session.pop("password_reset_verified", None)
            request.session.pop("password_reset_user_id",  None)

            messages.success(request, "Password updated successfully. Please log in.")
            return redirect("login")

    return render(request, "set_new_password.html", {"form": form})



# PROFILE

@never_cache
@login_required

def profile_view(request):
    user      = request.user.__class__.objects.select_related("profile").get(pk=request.user.pk)
    profile   = user.profile
    addresses = user.addresses.all()
    return render(request, "profile.html", {
        "user":      user,
        "profile":   profile,
        "addresses": addresses,
    })


@never_cache
@login_required

def profile_edit_view(request):

    user    = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    user_form    = ProfileEditForm(instance=user)
    profile_form = UserProfileForm(instance=profile)

    if request.method == "POST":
        user_form    = ProfileEditForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)

        if user_form.is_valid() and profile_form.is_valid():
            updated_user = user_form.save()
            pf = profile_form.save(commit=False)

            if request.POST.get("clear_avatar") == "true":
                if profile.avatar:
                    profile.avatar.delete(save=False)
                pf.avatar = None
            elif not request.FILES.get("avatar"):
                pf.avatar = profile.avatar

            pf.save()
            update_session_auth_hash(request, updated_user)
            login(request, updated_user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, "Profile updated successfully.")
            return redirect("profile")
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "profile_edit.html", {
        "user_form":    user_form,
        "profile_form": profile_form,
        "profile":      profile,
    })


@never_cache
@login_required

def change_password_view(request):
    has_password = request.user.has_usable_password()

    if request.method == "POST":
        if has_password:
            form = ChangePasswordForm(request.user, request.POST)
        else:
            form = SetNewPasswordForm(request.POST)

        if form.is_valid():
            request.user.set_password(form.cleaned_data["new_password"])
            request.user.save(update_fields=["password"])
            update_session_auth_hash(request, request.user)
            if has_password:
                messages.success(request, "Your password has been updated.")
            else:
                messages.success(request, "Password set successfully. You can now log in with your email and password too.")
            return redirect("profile")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ChangePasswordForm(request.user) if has_password else SetNewPasswordForm()

    return render(request, "change_password.html", {"form": form, "has_password": has_password})


@never_cache
@login_required

def change_email_view(request):
    if request.method == "POST":
        form = EmailChangeForm(request.user, request.POST)
        if form.is_valid():
            new_email = form.cleaned_data["new_email"]
            from types import SimpleNamespace
            temp_user = SimpleNamespace(email=new_email, first_name=request.user.first_name)
            otp_obj = create_otp_for_user(request.user, purpose="email_change")
            send_otp_email(temp_user, otp_obj.otp, purpose="email_change")
            logger.debug("SESSION BEFORE SET_PENDING (change_email): %s", request.session.items())
            set_pending_user_session(request, request.user.pk, purpose="email_change")
            request.session["pending_new_email"] = new_email
            request.session.save()
            logger.debug("SESSION AFTER SET_PENDING (change_email): %s", request.session.items())
            messages.success(
                request,
                f"OTP sent to {new_email}. Verify to change to {new_email}.",
            )
            return redirect("verify_email_otp")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = EmailChangeForm(request.user)
    return render(request, "change_email.html", {"form": form})

@never_cache
@login_required

def verify_email_otp_view(request):
    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "email_change":
        messages.error(request, "Session expired. Please start the email change process again.")
        return redirect("profile")

    new_email = request.session.get("pending_new_email")
    form      = OTPVerificationForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        otp_input = form.cleaned_data["otp"]
        valid, error_msg = verify_otp(user, otp_input, purpose="email_change")

        logger.debug("SESSION BEFORE VERIFY EMAIL OTP: %s", request.session.items())
        if valid:
            user.email = new_email
            user.save(update_fields=["email"])
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            update_session_auth_hash(request, user)
            clear_pending_user_session(request)
            request.session.pop("pending_new_email", None)
            request.session.save()
            logger.debug("SESSION AFTER VERIFY EMAIL OTP: %s", request.session.items())
            messages.success(request, "Your email address has been updated.")
            return redirect("profile")
        else:
            messages.error(request, error_msg)

    latest = OTPVerification.objects.filter(
        user=user, purpose="email_change"
    ).order_by("-created_at").first()
    seconds_remaining = latest.seconds_until_resend_allowed() if latest else 0

    return render(request, "verify_email_otp.html", {
        "form":              form,
        "email":             user.email,
        "new_email":         new_email,
        "seconds_remaining": seconds_remaining,
    })


@login_required
@never_cache
def resend_email_otp_view(request):
    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "email_change":
        messages.error(request, "Session expired. Please start the email change process again.")
        return redirect("profile")

    allowed, seconds_left = check_resend_cooldown(user, purpose="email_change")
    if not allowed:
        messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new OTP.")
        return redirect("verify_email_otp")

    otp_obj = create_otp_for_user(user, purpose="email_change")
    send_otp_email(user, otp_obj.otp, purpose="email_change")
    messages.success(request, f"A new OTP has been sent to {user.email}.")
    return redirect("verify_email_otp")


@login_required
@never_cache
def delete_account_view(request):
    user = request.user
    has_password = user.has_usable_password()
    error = None

    if request.method == "POST":
        if user.is_superuser:
            messages.error(request, "Admin accounts cannot be deleted from this page.")
            return redirect("profile")

        if has_password:
            password = request.POST.get("password", "")
            if not user.check_password(password):
                error = "Password incorrect. Account not deleted."
            else:
                logout(request)
                user.delete()
                messages.success(request, "Your account has been permanently deleted.")
                return redirect("home")
        else:
            action = request.POST.get("action")

            if action == "send_otp":
                allowed, seconds_left = check_resend_cooldown(user, purpose="account_delete")
                if not allowed:
                    messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new code.")
                else:
                    otp_obj = create_otp_for_user(user, purpose="account_delete")
                    send_otp_email(user, otp_obj.otp, purpose="account_delete")
                    request.session["delete_otp_sent"] = True
                    messages.success(request, f"A verification code has been sent to {user.email}.")

            elif action == "confirm":
                otp_input = request.POST.get("otp", "").strip()
                valid, msg = verify_otp(user, otp_input, purpose="account_delete")
                if valid:
                    request.session.pop("delete_otp_sent", None)
                    logout(request)
                    user.delete()
                    messages.success(request, "Your account has been permanently deleted.")
                    return redirect("home")
                else:
                    error = msg

    seconds_remaining = 0
    otp_sent = request.session.get("delete_otp_sent", False)
    if not has_password and otp_sent:
        latest = OTPVerification.objects.filter(
            user=user, purpose="account_delete"
        ).order_by("-created_at").first()
        if latest:
            seconds_remaining = latest.seconds_until_resend_allowed()

    return render(request, "delete_account.html", {
        "error":             error,
        "has_password":      has_password,
        "otp_sent":          otp_sent,
        "seconds_remaining": seconds_remaining,
    })


# ADDRESSES

@login_required
@never_cache
def addresses_view(request):
    addresses = request.user.addresses.all()
    form      = AddressForm()

    if request.method == "POST":
        form = AddressForm(request.POST)
        if form.is_valid():
            try:
                address      = form.save(commit=False)
                address.user = request.user
                if request.user.addresses.count() == 0:
                    address.is_default = True
                address.save()
                messages.success(request, "Address added successfully.")
                return redirect("addresses")
            except Exception as exc:
                logger.error(f"Failed to save address for user {request.user.id}: {exc}")
                messages.error(request, "An error occurred while saving the address. Please try again.")

    return render(request, "addresses.html", {
        "addresses": request.user.addresses.all(),
        "form":      form,
        "editing":   False,
    })


@login_required
@never_cache
def address_edit_view(request, address_id):
    address = get_object_or_404(Address, pk=address_id, user=request.user)
    form    = AddressForm(instance=address)

    if request.method == "POST":
        form = AddressForm(request.POST, instance=address)
        if form.is_valid():
            form.save()
            messages.success(request, "Address updated successfully.")
            return redirect("addresses")
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "addresses.html", {
        "form":      form,
        "address":   address,
        "addresses": request.user.addresses.all(),
        "editing":   True,
    })


@login_required
@never_cache
def address_delete_view(request, address_id):
    address = get_object_or_404(Address, pk=address_id, user=request.user)
    if request.method == "POST":
        address.delete()
        messages.success(request, "Address removed.")
    return redirect("addresses")


@login_required
@never_cache
def address_set_default_view(request, address_id):
    address = get_object_or_404(Address, pk=address_id, user=request.user)
    if request.method == "POST":
        address.is_default = True
        address.save()
        messages.success(request, "Default address updated.")
    return redirect("addresses")

def product_list_view(request):
    products = Product.objects.filter(
        is_deleted=False,
        is_listed=True,
        category__is_deleted=False,
        category__is_listed=True,
        variants__is_deleted=False,
        variants__is_listed=True,
    ).select_related("brand", "category").prefetch_related("variants__images").distinct()
    
    search = request.GET.get("search", "").strip()
    sort = request.GET.get("sort", "latest")
    category = request.GET.get("category", "").strip()
    brand = request.GET.get("brand", "").strip()
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()

    if search:
        products = products.filter(
            Q(name__icontains=search) | Q(brand__name__icontains=search)
        ).distinct()
    if category.isdigit():
        products = products.filter(category_id=category)

    if brand.isdigit():
        products = products.filter(brand_id=brand)
    
    products = products.annotate(price=Min("variants__sale_price"))

    if min_price.isdigit():
        products = products.filter(price__gte=min_price)

    if max_price.isdigit():
        products = products.filter(price__lte=max_price)
    
    sort_options = {
        "latest": "-created_at",
        "price_low": "price",
        "price_high":"-price",
        "az":"name",
        "za":"-name"
    }
    products = products.order_by(sort_options.get(sort, "-created_at"))

    paginator = Paginator(products, 12)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    
    wishlist_ids = set()
    cart_product_ids = set()
    if request.user.is_authenticated:
        wishlist_ids = set(Wishlist.objects.filter(user=request.user).values_list("product_id", flat=True))
        cart_product_ids = set(
            CartItem.objects.filter(cart__user=request.user).values_list("variant__product_id", flat=True)
        )

    context = {
        "products" : page_obj.object_list,
        "page_obj" : page_obj,
        "is_paginated" : page_obj.has_other_pages(),
        "search_query": search,
        "sort": sort,
        "selected_category": category,
        "selected_brand": brand,
        "min_price": min_price,
        "max_price": max_price,
        "categories": Category.objects.filter(is_deleted=False, is_listed=True).order_by("name"),
        "brands": Brand.objects.filter(is_deleted=False).order_by("name"),
        "wishlist_ids": wishlist_ids,
        "cart_product_ids": cart_product_ids,
    }
    return render(request, "product_list.html", context)


def product_detail_view(request, slug):
    product = get_object_or_404(Product, slug=slug, is_deleted=False)
    if not product.is_listed or product.category.is_deleted or not product.category.is_listed:
        messages.error(request, "This product is no longer available.")
        return redirect("product_list")
    variants=product.variants.filter(is_deleted=False, is_listed=True)
    variant_id = request.GET.get("variant", "").strip()
    variant=None
    if variant_id.isdigit():
        variant = variants.filter(id=variant_id).first()
    if not variant:
        variant = variants.filter(is_default=True).first() or variants.first()

    color_options = []
    size_options = []
    if variant:
        seen_colors, seen_sizes = set(), set()
        for v in variants:
            if v.color and v.color.lower() not in seen_colors:
                seen_colors.add(v.color.lower())
                color_options.append(variants.filter(color__iexact=v.color, size=variant.size).first() or v)
            if v.size and v.size not in seen_sizes:
                seen_sizes.add(v.size)
                size_options.append(variants.filter(size=v.size, color__iexact=variant.color).first() or v)

    reviews = Review.objects.filter(product=product).select_related("user")
    review_count = reviews.count()
    avg_rating = reviews.aggregate(Avg("rating"))["rating__avg"]

    related_products = Product.objects.filter(
        category=product.category,
        is_deleted=False,
        is_listed=True,
    ).exclude(id=product.id).select_related("brand").prefetch_related("variants__images")[:4]

    in_wishlist = False
    in_cart = False
    wishlisted_variant_ids = []
    if request.user.is_authenticated:
        wishlisted_variant_ids = list(
            Wishlist.objects.filter(user=request.user, product=product)
            .values_list("variant_id", flat=True)
        )
        # Heart reflects the CURRENTLY selected variant, not the whole product.
        in_wishlist = bool(variant) and variant.id in wishlisted_variant_ids
        in_cart = CartItem.objects.filter(cart__user=request.user, variant__product=product).exists()

    max_qty = min(variant.stock, CartItem.MAX_QUANTITY) if variant else 0

    context = {
        "product": product,
        "variant": variant,
        "variants": variants,
        "color_options": color_options,
        "size_options": size_options,
        "avg_rating": avg_rating,
        "review_count": review_count,
        "reviews": reviews[:6],
        "related_products": related_products,
        "in_wishlist": in_wishlist,
        "in_cart": in_cart,
        "wishlisted_variant_ids": wishlisted_variant_ids,
        "max_qty": max_qty,
    }
    return render(request,"product_detail.html",context)



# PLACEHOLDERS

@login_required
@never_cache
def orders_view(request):
    return render(request, "orders.html")


@login_required
@never_cache
def wishlist_view(request):
    # Keep every wishlisted item visible — including products the admin has
    # blocked/disabled — and flag each item's availability for the template.
    items = list(
        Wishlist.objects.filter(user=request.user)
        .select_related("product", "product__brand", "product__category")
        .prefetch_related("product__variants__images")
    )

    for item in items:
        product = item.product
        product_blocked = (
            product.is_deleted or not product.is_listed
            or product.category.is_deleted or not product.category.is_listed
        )
        # Evaluate the EXACT variant the user saved — never silently swap it.
        variant = item.variant
        variant_active = (
            variant is not None and not variant.is_deleted and variant.is_listed
        )
        item.variant = variant
        item.is_blocked = product_blocked
        item.is_unavailable = product_blocked or not variant_active
        item.is_out_of_stock = variant_active and variant.stock == 0
        item.is_available = variant_active and variant.stock > 0

        # If the saved variant is unavailable but the product itself is fine,
        # offer the other active, in-stock variants so the user can switch.
        if item.is_unavailable and not product_blocked:
            item.alt_variants = [
                v for v in product.variants.all()
                if not v.is_deleted and v.is_listed and v.stock > 0
                and (variant is None or v.id != variant.id)
            ]
        else:
            item.alt_variants = []

    cart_product_ids = set(
        CartItem.objects.filter(cart__user=request.user).values_list("variant__product_id", flat=True)
    )

    return render(request, "wishlist.html", {"items": items, "cart_product_ids": cart_product_ids})


@login_required
@require_POST
def toggle_wishlist_view(request, product_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    variant_id = request.POST.get("variant", "")
    variant_id = int(variant_id) if variant_id.isdigit() else None

    if variant_id is not None:
        # Variant-level toggle (product detail page): add/remove ONLY this
        # specific (user, variant) combination — never other variants.
        entry = Wishlist.objects.filter(
            user=request.user, product_id=product_id, variant_id=variant_id
        )
        if entry.exists():
            entry.delete()
            wishlisted = False
        else:
            product = get_object_or_404(Product, pk=product_id, is_deleted=False)
            variant = ProductVariant.objects.filter(
                pk=variant_id, product=product, is_deleted=False
            ).first()
            if variant is None:
                if is_ajax:
                    return JsonResponse({"error": "Variant unavailable."}, status=400)
                messages.error(request, "This item is no longer available.")
                return redirect(request.POST.get("next") or "product_list")
            Wishlist.objects.create(user=request.user, product=product, variant=variant)
            wishlisted = True
    else:
        # Product-level toggle (listing cards, no variant chosen): a product is
        # "wishlisted" if any of its variants is, so remove all / add default.
        entry = Wishlist.objects.filter(user=request.user, product_id=product_id)
        if entry.exists():
            entry.delete()
            wishlisted = False
        else:
            product = get_object_or_404(Product, pk=product_id, is_deleted=False)
            Wishlist.objects.create(
                user=request.user, product=product, variant=product.display_variant
            )
            wishlisted = True

    if is_ajax:
        count = Wishlist.objects.filter(user=request.user).count()
        return JsonResponse({"wishlisted": wishlisted, "wishlist_count": count})

    messages.success(
        request,
        "Added to your wishlist." if wishlisted else "Removed from your wishlist.",
    )
    next_url = request.POST.get("next") or "product_list"
    return redirect(next_url)


@login_required
@require_POST
def wishlist_switch_variant_view(request, item_id):
    item = Wishlist.objects.filter(pk=item_id, user=request.user).select_related("product").first()
    if item is None:
        return redirect("wishlist")

    variant_id = request.POST.get("variant", "")
    variant = None
    if variant_id.isdigit():
        variant = ProductVariant.objects.filter(
            pk=variant_id, product=item.product, is_deleted=False, is_listed=True
        ).first()

    if variant is None or variant.stock == 0:
        messages.error(request, "That variant is no longer available.")
        return redirect("wishlist")

    item.variant = variant
    item.save(update_fields=["variant"])
    messages.success(request, f"Switched to {variant.variant_name}.")
    return redirect("wishlist")


@login_required
@require_POST
def add_all_wishlist_to_cart_view(request):
    items = list(
        Wishlist.objects.filter(user=request.user)
        .select_related("product", "product__category")
    )
    total = len(items)
    if total == 0:
        messages.info(request, "Your wishlist is empty.")
        return redirect("wishlist")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    cart_product_ids = set(
        CartItem.objects.filter(cart=cart).values_list("variant__product_id", flat=True)
    )

    added = already_in_cart = unavailable = 0

    for item in items:
        product = item.product
        product_blocked = (
            product.is_deleted or not product.is_listed
            or product.category.is_deleted or not product.category.is_listed
        )
        # Add the EXACT saved variant only — never substitute another one.
        variant = item.variant
        variant_active = variant is not None and not variant.is_deleted and variant.is_listed

        # Unavailable items are skipped and kept in the wishlist.
        if product_blocked or not variant_active or variant.stock == 0:
            unavailable += 1
            continue

        if product.id in cart_product_ids:
            already_in_cart += 1
            item.delete()
            continue

        CartItem.objects.create(cart=cart, variant=variant, quantity=1)
        cart_product_ids.add(product.id)
        item.delete()
        added += 1

    if added:
        messages.success(request, f"{added} of {total} item{'s' if total != 1 else ''} added to your cart.")
    elif already_in_cart and not unavailable:
        messages.info(request, "All items are already in your cart.")
    if already_in_cart:
        messages.info(request, f"{already_in_cart} item{'s' if already_in_cart != 1 else ''} already in your cart.")
    if unavailable:
        messages.warning(
            request,
            f"{unavailable} item{'s' if unavailable != 1 else ''} currently unavailable and "
            f"{'were' if unavailable != 1 else 'was'} skipped — kept in your wishlist."
        )

    return redirect("wishlist")


@login_required
@never_cache
def cart_view(request):
    cart, created = Cart.objects.get_or_create(user=request.user)
    items = list(cart.items.select_related(
        "variant__product", "variant__product__category", "variant__product__brand"
    ))

    can_checkout = bool(items)
    cart_total = 0
    for item in items:
        product = item.variant.product
        item.is_blocked = (
            product.is_deleted or not product.is_listed
            or product.category.is_deleted or not product.category.is_listed
            or item.variant.is_deleted or not item.variant.is_listed
        )
        stock = item.variant.stock
        item.is_out_of_stock = stock == 0

        # Re-validate the stored quantity against current stock. For available
        # items whose quantity now exceeds stock, clamp DOWN and persist it.
        if not item.is_blocked and not item.is_out_of_stock and item.quantity > stock:
            item.quantity = stock
            item.save(update_fields=["quantity"])
            messages.warning(
                request,
                f"{product.name}: quantity adjusted to {stock} — limited stock available.",
            )

        item.max_qty = min(stock, CartItem.MAX_QUANTITY)

        if item.is_blocked or item.is_out_of_stock:
            can_checkout = False
        else:
            # Only available items count toward the payable total.
            cart_total += item.total_price

    context = {
        "cart": cart,
        "items": items,
        "cart_total": cart_total,
        "can_checkout": can_checkout,
    }
    return render(request, "cart.html", context)


@login_required
@require_POST
def add_to_cart_view(request, variant_id):
    variant = ProductVariant.objects.filter(pk=variant_id, is_deleted=False).select_related("product", "product__category").first()
    if not variant:
        messages.error(request, "This item is no longer available.")
        return redirect("product_list")

    product = variant.product

    if (not variant.is_listed or product.is_deleted or not product.is_listed
            or product.category.is_deleted or not product.category.is_listed):
        messages.error(request, "This product is no longer available.")
        return redirect("product_list")

    if variant.stock == 0:
        messages.error(request, "This item is out of stock.")
        return redirect("product_detail", slug=product.slug)

    qty = request.POST.get("quantity", "1")
    qty = int(qty) if qty.isdigit() and int(qty) > 0 else 1

    cart, created = Cart.objects.get_or_create(user=request.user)
    item, item_created = CartItem.objects.get_or_create(cart=cart, variant=variant, defaults={"quantity": qty})
    if not item_created:
        item.quantity += qty

    max_allowed = min(variant.stock, CartItem.MAX_QUANTITY)
    if item.quantity > max_allowed:
        item.quantity = max_allowed
        messages.warning(request, f"Only {max_allowed} of this item can be added to your cart.")

    item.save()

    Wishlist.objects.filter(user=request.user, product=product).delete()

    messages.success(request, "Added to your cart.")
    return redirect("cart")


@login_required
@require_POST
def update_cart_item_view(request, item_id):
    item = CartItem.objects.filter(pk=item_id, cart__user=request.user).first()
    if item is None:
        return redirect("cart")
    action = request.POST.get("action")

    if action == "increment":
        max_allowed = min(item.variant.stock, CartItem.MAX_QUANTITY)
        if item.quantity < max_allowed:
            item.quantity += 1
            item.save()
        else:
            messages.warning(request, "You've reached the maximum quantity for this item.")
    elif action == "decrement":
        if item.quantity > 1:
            item.quantity -= 1
            item.save()
        else:
            item.delete()

    return redirect("cart")


@login_required
@require_POST
def remove_from_cart_view(request, item_id):
    item = CartItem.objects.filter(pk=item_id, cart__user=request.user).first()
    if item is None:
        return redirect("cart")
    item.delete()
    messages.success(request, "Item removed from your cart.")
    return redirect("cart")

