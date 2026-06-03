

import logging

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
from django.utils import timezone
from django.views.decorators.cache import never_cache
# pyrefly: ignore [missing-import]
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
from .models import Address, CustomUser, UserProfile, OTPVerification
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
        remaining = int(cooldown - elapsed)
        return max(0, remaining)
    except (ValueError, TypeError):
        return 0


# HOME

def home_view(request):

    return render(request, "home.html")


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
                # Use first part of full name for email template
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


# OTP VERIFICATION — Step 2: Verify OTP and CREATE user

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

            # Authenticate user
            user = authenticate(request, username=email, password=password)

            # Ensure any admin session flags are removed before normal user login
            if request.session.get('_is_admin'):
                request.session.pop('_is_admin', None)
                request.session.pop('_admin_user_id', None)
                request.session.pop('_admin_email', None)
                logger.debug('Cleared admin session flags before user login')

            if user is None:
                # Check if the credentials belong to an admin account
                try:
                    existing = CustomUser.objects.get(email=email)
                    if existing.is_superuser:
                        messages.error(request, "Invalid email or password.")
                        return render(request, "login.html", {"form": form})
                except CustomUser.DoesNotExist:
                    pass
                messages.error(request, "Invalid email or password.")
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
                logger.debug("SESSION BEFORE SET_PENDING (forgot_password): %s", request.session.items())
                logger.debug("SESSION BEFORE SET_PENDING (admin_forgot_password): %s", request.session.items())
                logger.debug("SESSION BEFORE SET_PENDING (admin_forgot_password): %s", request.session.items())
                logger.debug("SESSION BEFORE SET_PENDING (admin_forgot_password): %s", request.session.items())
                set_pending_user_session(request, user.id, purpose="password_reset")
                request.session.save()
                logger.debug("SESSION AFTER SET_PENDING (admin_forgot_password): %s", request.session.items())
                messages.success(request, f"An OTP has been sent to {user.email}.")
                return redirect("admin_forgot_password_otp")
            else:
                messages.error(request, "Failed to send OTP email. Please try again.")

    return render(request, "forgot_password.html", {"form": form})


# FORGOT PASSWORD 


@never_cache

def forgot_password_otp_view(request):
    """Verify the password-reset OTP (server-side expiry check)."""
    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please request a new OTP.")
        return redirect("admin_forgot_password")
    logger.debug("SESSION AFTER OTP VERIFY (admin_forgot_password_otp): %s", request.session.items())
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

# FORGOT PASSWORD — Step 3: Set New Password

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
    if request.method == "POST":
        form = ChangePasswordForm(request.user, request.POST)
        if form.is_valid():
            request.user.set_password(form.cleaned_data["new_password"])
            request.user.save(update_fields=["password"])
            update_session_auth_hash(request, request.user)  
            messages.success(request, "Your password has been updated.")
            return redirect("profile")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ChangePasswordForm(request.user)
    return render(request, "change_password.html", {"form": form})


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
            update_session_auth_hash(request, user)  # ensure session auth hash refreshed
            clear_pending_user_session(request)
            request.session.pop("pending_new_email", None)
            request.session.save()
            logger.debug("SESSION AFTER VERIFY EMAIL OTP: %s", request.session.items())
            messages.success(request, "Your email address has been updated.")
            return redirect("profile")
        else:
            messages.error(request, error_msg)

    return render(request, "verify_email_otp.html", {
        "form":      form,
        "email":     user.email,
        "new_email": new_email,
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
    error = None
    if request.method == "POST":
        # Prevent admin (superuser) deletion via normal user interface
        if request.user.is_superuser:
            messages.error(request, "Admin accounts cannot be deleted from this page.")
            return redirect("profile")
        password = request.POST.get("password", "")
        if not request.user.check_password(password):
            error = "Password incorrect. Account not deleted."
        else:
            user = request.user
            logout(request)
            user.delete()
            messages.success(request, "Your account has been permanently deleted.")
            return redirect("home")
    return render(request, "delete_account.html", {"error": error})


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
    """Edit an existing address (ownership enforced)."""
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
    """Mark an address as the default (POST only)."""
    address = get_object_or_404(Address, pk=address_id, user=request.user)
    if request.method == "POST":
        address.is_default = True
        address.save()
        messages.success(request, "Default address updated.")
    return redirect("addresses")


# PLACEHOLDERS

@login_required
@never_cache
def orders_view(request):
    return render(request, "orders.html")


@login_required
@never_cache
def wishlist_view(request):
    return render(request, "wishlist.html")