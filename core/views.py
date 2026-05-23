"""
views.py — Luxelle Ecommerce (app: core)
Complete authentication, profile, and address management views.
All template names match the existing frontend templates exactly.
"""

import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import (
    anonymous_required,
    otp_session_required,
    password_reset_session_required,
)
from .forms import (
    AddressForm,
    ForgotPasswordForm,
    LoginForm,
    OTPVerificationForm,
    ProfileEditForm,
    RegistrationForm,
    SetNewPasswordForm,
    UserProfileForm,
)
from .models import Address, CustomUser
from .utils import (
    clear_pending_user_session,
    create_otp_for_user,
    get_pending_user,
    send_otp_email,
    set_pending_user_session,
    verify_otp,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# HOME
# ══════════════════════════════════════════════════════════════

def home_view(request):
    """Public home / landing page."""
    return render(request, "home.html")


# ══════════════════════════════════════════════════════════════
# REGISTRATION
# ══════════════════════════════════════════════════════════════

@anonymous_required()
def register_view(request):
    form = RegistrationForm(request.POST or None)

    if request.method == "POST":
        print("DEBUG: Form submitted")
        if form.is_valid():
            print("DEBUG: Form is valid")
            user = form.save()
            print(f"DEBUG: User created → {user.email}")

            otp_obj = create_otp_for_user(user, purpose="registration")
            print(f"DEBUG: OTP created → {otp_obj.otp}")
            email_sent = send_otp_email(user, otp_obj.otp, purpose="registration")

            print("EMAIL STATUS:", email_sent)
            print("USER EMAIL:", user.email)
            print("OTP:", otp_obj.otp)

            if not email_sent:
                print("EMAIL FAILED BUT USER SAVED")
                messages.warning(
                    request,
                    "Email failed temporarily, but account was created."
                )

            set_pending_user_session(request, user.pk, purpose="registration")

            messages.success(
                request,
                f"OTP sent to {user.email}. Please verify your email."
            )

            return redirect("verify_otp")
            set_pending_user_session    (request, user.pk, purpose="registration")
            messages.success(request, f"OTP sent to {user.email}. Please verify your email.")
            return redirect("verify_otp")
        else:
            print(f"DEBUG: Form errors → {form.errors}")

    return render(request, "register.html", {"form": form})
# ══════════════════════════════════════════════════════════════
# OTP VERIFICATION (Registration)
# ══════════════════════════════════════════════════════════════

@otp_session_required
def verify_otp_view(request):
    """
    Step 2 of registration.
    Verifies the OTP and activates the user account.
    """
    user = get_pending_user(request)
    if not user:
        messages.error(request, "Session expired. Please register again.")
        return redirect("register")

    form = OTPVerificationForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            otp_input = form.cleaned_data["otp"]
            valid, error_msg = verify_otp(user, otp_input, purpose="registration")

            if valid:
                user.is_active   = True
                user.is_verified = True
                user.save(update_fields=["is_active", "is_verified"])

                clear_pending_user_session(request)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")

                messages.success(request, "Email verified! Welcome to Luxelle.")
                return redirect("home")
            else:
                messages.error(request, error_msg)

    return render(request, "verify_otp.html", {"form": form, "email": user.email})


@otp_session_required
def resend_otp_view(request):
    """Resend OTP for registration verification."""
    user = get_pending_user(request)
    if not user:
        messages.error(request, "Session expired. Please register again.")
        return redirect("register")

    otp_obj    = create_otp_for_user(user, purpose="registration")
    email_sent = send_otp_email(user, otp_obj.otp, purpose="registration")

    if email_sent:
        messages.success(request, f"A new OTP has been sent to {user.email}.")
    else:
        messages.error(request, "Failed to resend OTP. Please try again.")

    return redirect("verify_otp")


# ══════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════

@anonymous_required()
def login_view(request):
    """
    Email + password login.
    Handles unverified accounts by re-sending OTP.
    """
    form = LoginForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email    = form.cleaned_data["email"]
            password = form.cleaned_data["password"]

            user = authenticate(request, username=email, password=password)

            if user is None:
                # Check if user exists but is unverified
                try:
                    existing = CustomUser.objects.get(email=email)
                    if not existing.is_active:
                        otp_obj = create_otp_for_user(existing, purpose="registration")
                        send_otp_email(existing, otp_obj.otp, purpose="registration")
                        set_pending_user_session(request, existing.pk, purpose="registration")
                        messages.warning(
                            request,
                            "Your email is not verified. A new OTP has been sent.",
                        )
                        return redirect("verify_otp")
                except CustomUser.DoesNotExist:
                    pass

                messages.error(request, "Invalid email or password.")
            else:
                login(request, user)
                logger.info(f"User logged in: {user.email}")

                next_url = request.GET.get("next", "home")
                messages.success(request, f"Welcome back, {user.first_name or user.email}!")
                return redirect(next_url)

    return render(request, "login.html", {"form": form})


# ══════════════════════════════════════════════════════════════
# LOGOUT
# ══════════════════════════════════════════════════════════════

@login_required
def logout_view(request):
    """Log out and redirect to login."""
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("login")


# ══════════════════════════════════════════════════════════════
# FORGOT PASSWORD — Step 1: Email Entry
# ══════════════════════════════════════════════════════════════

@anonymous_required()
def forgot_password_view(request):
    """Accepts the user's email and sends a password-reset OTP."""
    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email = form.cleaned_data["email"]
            user  = CustomUser.objects.get(email=email)

            otp_obj    = create_otp_for_user(user, purpose="password_reset")
            email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")

            if email_sent:
                set_pending_user_session(request, user.pk, purpose="password_reset")
                messages.success(request, f"OTP sent to {email}.")
                return redirect("forgot_password_otp")
            else:
                messages.error(request, "Failed to send OTP email. Please try again.")

    return render(request, "forgot_password.html", {"form": form})


# ══════════════════════════════════════════════════════════════
# FORGOT PASSWORD — Step 2: OTP Verification
# ══════════════════════════════════════════════════════════════

def forgot_password_otp_view(request):
    """Verifies the password-reset OTP."""
    user = get_pending_user(request)
    if not user:
        messages.error(request, "Session expired. Please start again.")
        return redirect("forgot_password")

    form = OTPVerificationForm(request.POST or None)

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

    return render(request, "forgot_password_otp.html", {"form": form, "email": user.email})


# ══════════════════════════════════════════════════════════════
# FORGOT PASSWORD — Step 3: Set New Password
# ══════════════════════════════════════════════════════════════

@password_reset_session_required
def set_new_password_view(request):
    """Final step: set a new password after OTP is verified."""
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
            request.session.pop("password_reset_user_id", None)

            messages.success(request, "Password updated successfully. Please log in.")
            return redirect("login")

    return render(request, "set_new_password.html", {"form": form})


# ══════════════════════════════════════════════════════════════
# PROFILE — View
# ══════════════════════════════════════════════════════════════

@login_required
def profile_view(request):
    """Display the logged-in user's profile and addresses."""
    profile   = request.user.profile
    addresses = request.user.addresses.all()

    context = {
        "user":      request.user,
        "profile":   profile,
        "addresses": addresses,
    }
    return render(request, "profile.html", context)


# ══════════════════════════════════════════════════════════════
# PROFILE — Edit
# ══════════════════════════════════════════════════════════════

@login_required
def profile_edit_view(request):
    """
    Edit profile: name, phone (CustomUser) and
    bio, gender, DOB, avatar (UserProfile).
    """
    user         = request.user
    profile      = user.profile
    user_form    = ProfileEditForm(instance=user)
    profile_form = UserProfileForm(instance=profile)

    if request.method == "POST":
        user_form    = ProfileEditForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)

        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("profile")
        else:
            messages.error(request, "Please correct the errors below.")

    context = {
        "user_form":    user_form,
        "profile_form": profile_form,
    }
    return render(request, "profile_edit.html", context)


# ══════════════════════════════════════════════════════════════
# ADDRESSES — List + Add
# ══════════════════════════════════════════════════════════════

@login_required
def addresses_view(request):
    """List all addresses and handle Add Address form."""
    addresses = request.user.addresses.all()
    form      = AddressForm()

    if request.method == "POST":
        form = AddressForm(request.POST)
        if form.is_valid():
            address      = form.save(commit=False)
            address.user = request.user
            address.save()
            messages.success(request, "Address added successfully.")
            return redirect("addresses")

    context = {
        "addresses": addresses,
        "form":      form,
    }
    return render(request, "addresses.html", context)


# ══════════════════════════════════════════════════════════════
# ADDRESSES — Edit
# ══════════════════════════════════════════════════════════════

@login_required
def address_edit_view(request, address_id):
    """Edit an existing address (ownership enforced)."""
    address = get_object_or_404(Address, pk=address_id, user=request.user)
    form    = AddressForm(instance=address)

    if request.method == "POST":
        form = AddressForm(request.POST, instance=address)
        if form.is_valid():
            form.save()
            messages.success(request, "Address updated.")
            return redirect("addresses")

    context = {
        "form":    form,
        "address": address,
    }
    return render(request, "addresses.html", context)


# ══════════════════════════════════════════════════════════════
# ADDRESSES — Delete
# ══════════════════════════════════════════════════════════════

@login_required
def address_delete_view(request, address_id):
    """Delete an address (POST only, ownership enforced)."""
    address = get_object_or_404(Address, pk=address_id, user=request.user)

    if request.method == "POST":
        address.delete()
        messages.success(request, "Address removed.")

    return redirect("addresses")


# ══════════════════════════════════════════════════════════════
# ADDRESSES — Set Default
# ══════════════════════════════════════════════════════════════

@login_required
def address_set_default_view(request, address_id):
    """Mark an address as the default (POST only)."""
    address = get_object_or_404(Address, pk=address_id, user=request.user)

    if request.method == "POST":
        address.is_default = True
        address.save()
        messages.success(request, "Default address updated.")

    return redirect("addresses")