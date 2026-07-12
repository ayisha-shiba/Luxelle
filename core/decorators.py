from functools import wraps
from datetime import datetime
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.contrib import messages


# Helpers

def _parse_iso(dt_str):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except (ValueError, TypeError):
        return None


def _clear_registration_otp_session(request):
    for key in (
        "pending_otp",
        "pending_otp_expires_at",
        "pending_otp_sent_at",
        "otp_purpose",
    ):
        request.session.pop(key, None)



def _clear_pending_user_otp_session(request):
    for key in (
        "pending_user_id",
        "otp_purpose",
        "otp_created_at",
        "otp_expires_at",
    ):
        request.session.pop(key, None)


# Anonymous Required

def anonymous_required(redirect_url="home"):
    def decorator(view_func):
        @wraps(view_func)
        @never_cache
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                return redirect(redirect_url)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# OTP Session Required# 

def otp_session_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        has_registration = bool(request.session.get("pending_registration"))
        has_pending_user = bool(request.session.get("pending_user_id"))

        if not has_registration and not has_pending_user:
            messages.error(request, "Session expired. Please start again.")
            return redirect("register")

        now = timezone.now()

        if has_registration:
            expires_at = _parse_iso(request.session.get("pending_otp_expires_at"))
            if expires_at and now > expires_at:
                _clear_registration_otp_session(request)
                messages.error(
                    request,
                    "Your OTP has expired. Please register again.",
                )
                return redirect("register")

        if has_pending_user:
            expires_at = _parse_iso(request.session.get("otp_expires_at"))
            if expires_at and now > expires_at:
                purpose = request.session.get("otp_purpose", "")
                _clear_pending_user_otp_session(request)
                if purpose == "password_reset":
                    messages.error(
                        request,
                        "Your OTP has expired. Please restart the password reset.",
                    )
                    return redirect("forgot_password")
                else:
                    messages.error(request, "Session expired. Please start again.")
                    return redirect("register")

        return view_func(request, *args, **kwargs)
    return wrapper


# Password Reset Session Required

def password_reset_session_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.session.get("password_reset_verified"):
            messages.error(request, "Please verify your OTP first.")
            return redirect("forgot_password")
        return view_func(request, *args, **kwargs)
    return wrapper



from .models import CustomUser


# Admin Required

def admin_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.session.get("_is_admin") or not request.session.get("_admin_user_id"):
            messages.error(request, "Admin access required. Please log in.")
            return redirect("admin_login")
        try:
            admin_user = CustomUser.objects.get(id=request.session.get("_admin_user_id"), is_staff=True)
        except CustomUser.DoesNotExist:
            messages.error(request, "Admin account not found. Please log in again.")
            request.session.pop("_is_admin", None)
            request.session.pop("_admin_user_id", None)
            request.session.pop("_admin_pw_hash", None)
            return redirect("admin_login")

        stored_hash = request.session.get("_admin_pw_hash")
        if stored_hash and admin_user.password != stored_hash:
            request.session.flush()
            messages.error(request, "Your password was changed. Please log in again.")
            return redirect("admin_login")

        request.user = admin_user
        return view_func(request, *args, **kwargs)
    return wrapper

