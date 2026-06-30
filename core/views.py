from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, Min, Avg, F


import logging
import math

from django.db import transaction
from decimal import Decimal


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
from django.http import JsonResponse, HttpResponse
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
from .models import Address, CustomUser, UserProfile, OTPVerification, Product, Category, Brand, Review, Wishlist, Cart, CartItem, ProductVariant, Order, OrderItem, OrderStatusEvent, ReferralCode, Referral
from . import pricing
from wallet import services as wallet_services
from django.conf import settings
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


# REFERRAL REWARDS

@transaction.atomic
def apply_referral_reward(new_user, code):
    """Credit both the referrer and the new user's wallets after successful sign-up.

    Uses select_for_update on the Referral row to prevent a race condition
    where two concurrent requests could both issue the reward.  The function is
    safe to call multiple times — it is a no-op if the referral is already marked
    as rewarded.
    """
    reward_amount = getattr(settings, "REFERRAL_REWARD_AMOUNT", 100)

    try:
        ref_code_obj = ReferralCode.objects.select_related("user").get(code=code)
    except ReferralCode.DoesNotExist:
        logger.warning(f"[REFERRAL] Code '{code}' not found when trying to reward {new_user.email}")
        return

    referrer = ref_code_obj.user

    # Guard: should never happen since form validates this, but be defensive
    if referrer == new_user:
        logger.warning(f"[REFERRAL] Self-referral attempt by {new_user.email} — skipping reward.")
        return

    # Guard: check if referrer is active
    if not referrer.is_active:
        logger.warning(f"[REFERRAL] Referrer {referrer.email} is inactive — skipping reward for {new_user.email}.")
        return

    # get_or_create ensures idempotency; select_for_update prevents double-credit races
    referral, created = Referral.objects.get_or_create(
        referrer=referrer,
        referee=new_user,
    )

    # Lock the row before reading the rewarded flag
    referral = Referral.objects.select_for_update().get(pk=referral.pk)

    if referral.rewarded:
        logger.info(f"[REFERRAL] Already rewarded for {referrer.email} → {new_user.email}, skipping.")
        return

    # Credit the referrer's wallet
    wallet_services.credit(
        user=referrer,
        amount=reward_amount,
        reason=f"Referral reward — {new_user.get_full_name() or new_user.email} joined using your code",
    )

    # Credit the new user's (referee) wallet
    wallet_services.credit(
        user=new_user,
        amount=reward_amount,
        reason=f"Welcome referral bonus — referred by {referrer.get_full_name() or referrer.email}",
    )

    referral.rewarded = True
    referral.save(update_fields=["rewarded"])

    logger.info(
        f"[REFERRAL] Rewarded ₹{reward_amount} to both {referrer.email} and {new_user.email}"
    )


# HOME


def about_view(request):
    return render(request, "about.html")


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

    # Referral code: from the ?ref= token URL on GET, or the form field on POST.
    referral_code = request.GET.get("ref") or request.POST.get("referral_code") or ""
    if referral_code:
        request.session["pending_referral_code"] = referral_code.strip().upper()

    if request.method == "POST":
        if form.is_valid():
            registration_data = {
                "email":         form.cleaned_data["email"],
                "password":      form.cleaned_data["password1"],
                "full_name":     form.cleaned_data["full_name"],
                "phone":         form.cleaned_data.get("phone", ""),
                # Store validated referral code (empty string if none provided)
                "referral_code": form.cleaned_data.get("referral_code", ""),
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

    return render(request, "register.html", {
        "form": form,
        "referral_code": request.session.get("pending_referral_code", ""),
    })


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

                # Apply referral reward if a valid code was submitted
                referral_code = registration_data.get("referral_code", "")
                if referral_code:
                    apply_referral_reward(user, referral_code)

            except Exception as exc:
                logger.error(f"[VERIFY_OTP] Failed to create user: {exc}")
                messages.error(request, "Account creation failed. Please try again.")
                return redirect("register")

            # Record who referred this user (reward comes on their first order).
            referral_code = request.session.get("pending_referral_code")
            if referral_code:
                from offers.services import apply_referral_code
                apply_referral_code(user, referral_code)

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
    from decimal import Decimal
    from django.urls import reverse
    from offers import services as offers_services
    from offers.models import ReferralProfile

    user      = request.user.__class__.objects.select_related("profile").get(pk=request.user.pk)
    profile   = user.profile
    addresses = user.addresses.all()

    referral = offers_services.get_or_create_profile(user)
    referral_link = request.build_absolute_uri(f"{reverse('register')}?ref={referral.code}")
    referred_qs = ReferralProfile.objects.filter(referred_by=user)
    referred_count = referred_qs.count()
    # Earnings are only released once a referred user completes their first order.
    rewards_released = referred_qs.filter(reward_granted=True).count()
    referral_earnings = rewards_released * offers_services.REFERRAL_REWARD

    return render(request, "profile.html", {
        "user":      user,
        "profile":   profile,
        "addresses": addresses,
        "referral":  referral,
        "referral_link": referral_link,
        "referred_count": referred_count,
        "rewards_released": rewards_released,
        "referral_earnings": referral_earnings,
    })


@never_cache
@login_required
def my_reviews_view(request):
    reviews = (request.user.reviews
               .select_related("product", "product__brand")
               .all())
    return render(request, "my_reviews.html", {"reviews": reviews})


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


# MY REVIEWS

@login_required
@never_cache
def my_reviews_view(request):
    reviews = Review.objects.filter(user=request.user).select_related("product").order_by("-created_at")
    return render(request, "my_reviews.html", {"reviews": reviews})


#prodt


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

    paginator = Paginator(products, 9)
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
    search_query = request.GET.get("search", "").strip()

    orders = (Order.objects.filter(user=request.user)
              .prefetch_related("items__variant__images")
              .order_by("-created_at"))

    if search_query:
        orders = orders.filter(
            Q(order_number__icontains=search_query) |
            Q(items__product_name__icontains=search_query)
        ).distinct()

    context = {"orders": orders, "search_query": search_query}
    return render(request, "orders.html", context)


@login_required
@never_cache
def order_detail_view(request, order_number):
    order = (Order.objects.filter(order_number=order_number, user=request.user)
             .prefetch_related("items__variant__images", "items__status_events").first())
    if not order:
        messages.error(request, "Order not found.")
        return redirect("orders")

    status_value, status_label = order.derived_status
    context = {
        "order": order,
        "items": order.items.all(),
        "status_value": status_value,
        "status_label": status_label,
    }
    return render(request, "order_detail.html", context)


@login_required
def order_invoice_view(request, order_number):
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa

    order = (Order.objects.filter(order_number=order_number, user=request.user)
             .prefetch_related("items").first())
    if not order:
        messages.error(request, "Order not found.")
        return redirect("orders")

    if not order.can_download_invoice:
        messages.error(request, "Invoice is available only after an item has been delivered.")
        return redirect("order_detail", order_number=order.order_number)

    # Totals are kept current on the order itself (recalculate_totals runs on
    # every cancel/return), so the invoice, the user order page and the admin
    # order page all read the same persisted figures. Bill only what the
    # customer keeps; list cancelled/returned items separately, not in totals.
    billed = [i for i in order.items.all() if i.is_billable]
    status_value, status_label = order.derived_status

    html = render_to_string("invoice.html", {
        "order": order,
        "items": billed,
        "status_label": status_label,
    })

    result = BytesIO()
    pdf_status = pisa.CreatePDF(html, dest=result, encoding="utf-8")
    if pdf_status.err:
        messages.error(request, "Could not generate the invoice. Please try again.")
        return redirect("order_detail", order_number=order.order_number)

    response = HttpResponse(result.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Luxelle-Invoice-{order.order_number}.pdf"'
    return response


@login_required
@require_POST
def cancel_order_item_view(request, item_id):
    item = (OrderItem.objects.select_related("order", "variant")
            .filter(pk=item_id, order__user=request.user).first())
    if item is None:
        messages.error(request, "Item not found.")
        return redirect("orders")

    if not item.can_cancel:
        messages.error(request, "This item can no longer be cancelled.")
        return redirect("order_detail", order_number=item.order.order_number)

    reason = request.POST.get("reason", "").strip()

    from wallet import services as wallet_services

    with transaction.atomic():
        if item.variant:
            item.variant.stock += item.quantity
            item.variant.save(update_fields=["stock"])

        item.status              = OrderItem.STATUS_CANCELLED
        item.cancellation_reason = reason
        item.save(update_fields=["status", "cancellation_reason"])

        OrderStatusEvent.objects.create(
            order_item=item, status=OrderItem.STATUS_CANCELLED,
            note="Cancelled by customer." + (f" Reason: {reason}" if reason else ""),
        )

        total_before = item.order.total
        item.order.recalculate_totals()
        refund = total_before - item.order.total

        # Direct refund to wallet — but only if the order was actually paid up
        # front. COD isn't paid until delivery (and you can't cancel post-delivery),
        # so there's nothing to refund there.
        prepaid = (item.order.payment_method == Order.PAYMENT_WALLET
                   or item.order.payments.filter(status="paid").exists())
        if prepaid and refund > 0:
            wallet_services.refund_item(
                item, refund, f"Refund for cancelled item: {item.product_name}"
            )

    refund_note = f" Rs. {refund} refunded to your wallet." if (prepaid and refund > 0) else ""
    messages.success(request, f"{item.product_name} has been cancelled successfully.{refund_note}")
    return redirect("order_detail", order_number=item.order.order_number)


@login_required
@require_POST
def return_order_item_view(request, item_id):
    item = (OrderItem.objects.select_related("order")
            .filter(pk=item_id, order__user=request.user).first())
    if item is None:
        messages.error(request, "Item not found.")
        return redirect("orders")

    if not item.can_return:
        messages.error(request, "This item is not eligible for return.")
        return redirect("order_detail", order_number=item.order.order_number)

    reason = request.POST.get("reason", "").strip()
    detail_url = item.order.order_number

    if not reason:
        messages.error(request, "Please provide a reason for the return.")
        return redirect("order_detail", order_number=detail_url)
    if len(reason) < 10:
        messages.error(request, "Please describe the reason in a little more detail (at least 10 characters).")
        return redirect("order_detail", order_number=detail_url)
    if not any(ch.isalpha() for ch in reason):
        messages.error(request, "Please enter a valid return reason in words.")
        return redirect("order_detail", order_number=detail_url)
    reason = reason[:500]

    item.status              = OrderItem.STATUS_RETURN_REQUESTED
    item.return_reason       = reason
    item.return_requested_at = timezone.now()
    item.save(update_fields=["status", "return_reason", "return_requested_at"])

    OrderStatusEvent.objects.create(
        order_item=item, status=OrderItem.STATUS_RETURN_REQUESTED,
        note=f"Return requested by customer. Reason: {reason}",
    )

    messages.success(request, f"Return requested for {item.product_name}.")
    return redirect("order_detail", order_number=item.order.order_number)


@login_required
@never_cache
def wishlist_view(request):
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
        variant = item.variant
        variant_active = (
            variant is not None and not variant.is_deleted and variant.is_listed
        )
        item.variant = variant
        item.is_blocked = product_blocked
        item.is_unavailable = product_blocked or not variant_active
        item.is_out_of_stock = variant_active and variant.stock == 0
        item.is_available = variant_active and variant.stock > 0

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
        variant = item.variant
        variant_active = variant is not None and not variant.is_deleted and variant.is_listed

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
    available_items = []
    for item in items:
        product = item.variant.product
        item.is_blocked = (
            product.is_deleted or not product.is_listed
            or product.category.is_deleted or not product.category.is_listed
            or item.variant.is_deleted or not item.variant.is_listed
        )
        stock = item.variant.stock
        item.is_out_of_stock = stock == 0

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
            available_items.append(item)

    summary = pricing.summarize_items(available_items)

    context = {
        "cart": cart,
        "items": items,
        "summary": summary,
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

    Wishlist.objects.filter(user=request.user, variant=variant).delete()

    messages.success(request, "Added to your cart.")
    return redirect("cart")


@login_required
@require_POST
def update_cart_item_view(request, item_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    item = (CartItem.objects.filter(pk=item_id, cart__user=request.user)
            .select_related("variant__product__category").first())
    if item is None:
        if is_ajax:
            return JsonResponse({"success": False, "error": "Item not found."}, status=404)
        return redirect("cart")

    action  = request.POST.get("action")
    removed = False
    warning = ""

    if action == "increment":
        max_allowed = min(item.variant.stock, CartItem.MAX_QUANTITY)
        if item.quantity < max_allowed:
            item.quantity += 1
            item.save()
        else:
            warning = "You've reached the maximum quantity for this item."
            if not is_ajax:
                messages.warning(request, warning)
    elif action == "decrement":
        if item.quantity > 1:
            item.quantity -= 1
            item.save()
        else:
            item.delete()
            removed = True

    if not is_ajax:
        return redirect("cart")

    cart = Cart.objects.get(user=request.user)
    available = []
    for ci in cart.items.select_related("variant__product__category"):
        p = ci.variant.product
        blocked = (
            p.is_deleted or not p.is_listed
            or p.category.is_deleted or not p.category.is_listed
            or ci.variant.is_deleted or not ci.variant.is_listed
        )
        if not blocked and ci.variant.stock > 0:
            available.append(ci)

    summary = pricing.summarize_items(available)

    data = {
        "success":     True,
        "removed":     removed,
        "warning":     warning,
        "subtotal":    str(summary["subtotal"]),
        "cgst":        str(summary["cgst"]),
        "sgst":        str(summary["sgst"]),
        "gst":         str(summary["gst"]),
        "grand_total": str(summary["grand_total"]),
        "cart_count":  cart.total_items,
    }
    if not removed:
        max_qty = min(item.variant.stock, CartItem.MAX_QUANTITY)
        data.update({
            "quantity":      item.quantity,
            "line_calc":     f"₹{item.variant.original_price} × {item.quantity} = ₹{item.subtotal}",
            "line_discount": f"Discount: −₹{item.discount_amount}",
            "line_total":    str(item.total_price),
            "at_max":        item.quantity >= max_qty,
        })
    return JsonResponse(data)


@login_required
@require_POST
def remove_from_cart_view(request, item_id):
    item = CartItem.objects.filter(pk=item_id, cart__user=request.user).first()
    if item is None:
        return redirect("cart")
    item.delete()
    messages.success(request, "Item removed from your cart.")
    return redirect("cart")

def payment_handler(request,order):
    method = order.payment_method

    if method == Order.PAYMENT_COD:
        return redirect("order_success", order_number=order.order_number)

    if method == Order.PAYMENT_RAZORPAY:
        # Razorpay logic lives in the payments app; core only knows the URL name.
        return redirect("payment_start", order_number=order.order_number)

    if method == Order.PAYMENT_WALLET:
        from wallet import services as wallet_services
        try:
            wallet_services.debit(
                request.user, order.total,
                f"Payment for order {order.order_number}", order=order,
            )
        except wallet_services.InsufficientBalance:
            messages.error(request, "Wallet balance was insufficient to pay for this order.")
            return redirect("order_detail", order_number=order.order_number)
        return redirect("order_success", order_number=order.order_number)

    raise ValueError(f"Unsupported payment method: {method}")

@login_required
@never_cache
def checkout_view(request):
    from offers import services as offers_services
    cart, _ = Cart.objects.get_or_create(user=request.user)
    items   = list(cart.items.select_related("variant__product", "variant__product__category"))

    for item in items:
        product = item.variant.product
        blocked = (
            product.is_deleted or not product.is_listed
            or product.category.is_deleted or not product.category.is_listed or item.variant.is_deleted or not item.variant.is_listed
        )
        if blocked or item.variant.stock == 0 or item.quantity > item.variant.stock:
            messages.error(request, "Some items in your cart are unavailable. Please reveiew your cart")
            return redirect("cart")
    
    if not items:
        messages.info(request, "Your cart is empty.")
        return redirect("cart")

    # Resolve any coupon held in the session. Re-validate it against the current
    # cart so a coupon that no longer qualifies (cart changed) is dropped.
    from decimal import Decimal
    from coupons import services as coupon_services
    from coupons.models import Coupon
    cart_subtotal = sum((Decimal(i.total_price) for i in items), Decimal("0"))
    active_coupon = None
    coupon_discount = Decimal("0")
    coupon_id = request.session.get("coupon_id")
    if coupon_id:
        coupon = Coupon.objects.filter(pk=coupon_id).first()
        try:
            if coupon:
                coupon_services.validate_coupon(coupon.code, request.user, cart_subtotal)
                active_coupon = coupon
                coupon_discount = coupon_services.compute_discount(coupon, cart_subtotal)
            else:
                raise coupon_services.CouponError("Coupon no longer available.")
        except coupon_services.CouponError:
            request.session.pop("coupon_id", None)

    addresses = request.user.addresses.all()
    address_form = AddressForm()
    open_address_modal = False

    if request.method == "POST" and request.POST.get("form_type") == "add_address":
        address_form = AddressForm(request.POST)
        if address_form.is_valid():
            new_address = address_form.save(commit=False)
            new_address.user = request.user
            new_address.is_default = True
            new_address.save()
            messages.success(request, "Address added and selected for delivery.")
            return redirect("checkout")
        open_address_modal = True

    elif request.method == "POST":
        address = Address.objects.filter(pk=request.POST.get("address_id"), user=request.user).first()
        if address is None:
            messages.error(request,"Please select a valid delivery address.")
            return redirect("checkout")

        payment_method = request.POST.get("payment_method", Order.PAYMENT_COD)
        if payment_method not in dict(Order.PAYMENT_CHOICES):
            messages.error(request, "Please select a valid payment method.")
            return redirect("checkout")

        # Pay-with-wallet: reject up front if the balance can't cover the order,
        # so we don't create an order (and decrement stock) we can't settle.
        if payment_method == Order.PAYMENT_WALLET:
            from wallet import services as wallet_services
            est_total = pricing.summarize_items(items, coupon_discount=coupon_discount)["grand_total"]
            if wallet_services.get_wallet(request.user).balance < est_total:
                messages.error(request, "Your wallet balance is insufficient for this order.")
                return redirect("checkout")

        try:
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user,
                    ship_full_name=address.full_name,
                    ship_phone=address.phone,
                    ship_address_line1=address.address_line1,
                    ship_address_line2=address.address_line2,
                    ship_city=address.city,
                    ship_state=address.state,
                    ship_postal_code=address.postal_code,
                    ship_country=address.country,
                    payment_method=payment_method,
                    coupon=active_coupon,
                )

                for item in items:
                    variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                    if variant.stock < item.quantity:
                        raise ValueError(f"{variant.variant_name} just went out of stock.")
                    # Snapshot the offer-discounted price so the order, totals and
                    # any future refund all reflect what the customer actually paid.
                    unit_price = offers_services.best_offer_for(variant)["effective_price"]
                    line_total = unit_price * item.quantity
                    order_item = OrderItem.objects.create(
                        order=order,
                        variant=variant,
                        product_name=variant.product.name,
                        variant_name=variant.variant_name,
                        sku=variant.sku,
                        unit_price=unit_price,
                        original_price=variant.original_price,
                        quantity=item.quantity,
                        line_total=line_total,
                    )

                    OrderStatusEvent.objects.create(
                        order_item=order_item, status=OrderItem.STATUS_PENDING,
                        note="Order placed successfully.",
                    )

                    variant.stock -= item.quantity
                    variant.save(update_fields=["stock"])

                order.recalculate_totals()

                # Lock in the coupon redemption (once per user) for this order.
                if active_coupon:
                    coupon_services.record_usage(active_coupon, request.user, order, order.coupon_discount)

                cart.items.all().delete()

        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("cart")

        request.session.pop("coupon_id", None)
        return payment_handler(request,order)
    
    from wallet import services as wallet_services
    summary = pricing.summarize_items(items, coupon_discount=coupon_discount)
    context = {
        "items": items,
        "addresses": addresses,
        "summary": summary,
        "address_form": address_form,
        "open_address_modal": open_address_modal,
        "wallet_balance": wallet_services.get_wallet(request.user).balance,
        "active_coupon": active_coupon,
    }
    return render(request, "checkout.html", context)

@login_required
@never_cache
def order_success_view(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user = request.user)

    # Reaching this page means an order completed — grant referral reward if due.
    from offers.services import grant_referral_reward_if_due
    grant_referral_reward_if_due(order)

    return render(request, "order_success.html", {"order":order})
    

