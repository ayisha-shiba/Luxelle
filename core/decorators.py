from functools import wraps
from datetime import datetime
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _parse_iso(dt_str):
    """Parse an ISO datetime string and make it timezone-aware. Returns None on failure."""
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
    """Remove OTP-related keys from registration flow while preserving pending_registration data."""
    for key in (
        "pending_otp",
        "pending_otp_expires_at",
        "pending_otp_sent_at",
        "otp_purpose",
    ):
        request.session.pop(key, None)



def _clear_pending_user_otp_session(request):
    """Remove all pending-user OTP keys from session (password reset / email change)."""
    for key in (
        "pending_user_id",
        "otp_purpose",
        "otp_created_at",
        "otp_expires_at",
    ):
        request.session.pop(key, None)


# ─────────────────────────────────────────────
# Anonymous Required
# ─────────────────────────────────────────────

def anonymous_required(redirect_url="home"):
    """
    Redirects already-authenticated users away from login/register pages.

    @never_cache added: prevents the browser from caching the login or
    register page — without this, hitting Back after logout can show the
    cached login page in a partially-authenticated state.

    Usage: @anonymous_required()
    """
    def decorator(view_func):
        @wraps(view_func)
        @never_cache
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                return redirect(redirect_url)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# OTP Session Required  (security-hardened)
# ─────────────────────────────────────────────

def otp_session_required(view_func):
    """
    Gate for all OTP verification views.

    Security guarantees (backend-only — frontend cannot bypass these):
    1. Session must contain either pending_registration (registration flow)
       or pending_user_id (password-reset / email-change flow).
       Missing → redirect to register.
    2. OTP expiry is validated server-side on EVERY request (GET and POST).
       Expired → session is fully cleared + user is redirected to the
       correct starting page based on otp_purpose:
         - "password_reset" → forgot_password
         - anything else   → register
    3. @never_cache prevents any browser/proxy from serving a cached copy
       of the OTP page after the session has been cleared.

    This fixes:
    - User closes browser → reopens site → lands back on OTP page (stale session)
    - OTP expires → page still usable if user refreshes
    - Manual URL access with expired/missing session
    """
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        has_registration = bool(request.session.get("pending_registration"))
        has_pending_user = bool(request.session.get("pending_user_id"))

        # ── 1. Session must exist ──
        if not has_registration and not has_pending_user:
            messages.error(request, "Session expired. Please start again.")
            return redirect("register")

        now = timezone.now()

        # ── 2a. Registration flow: check pending_otp_expires_at ──
        if has_registration:
            expires_at = _parse_iso(request.session.get("pending_otp_expires_at"))
            if expires_at and now > expires_at:
                _clear_registration_otp_session(request)
                messages.error(
                    request,
                    "Your OTP has expired. Please register again.",
                )
                return redirect("register")

        # ── 2b. Password-reset / email-change flow: check otp_expires_at ──
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


# ─────────────────────────────────────────────
# Password Reset Session Required
# ─────────────────────────────────────────────

def password_reset_session_required(view_func):
    """
    Ensures the password-reset OTP has been verified before showing
    the set-new-password page.

    @never_cache added: prevents the password reset form from being
    served from cache after the session has been cleared.
    """
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.session.get("password_reset_verified"):
            messages.error(request, "Please verify your OTP first.")
            return redirect("forgot_password")
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
# Admin Required
def admin_required(view_func):
    """Guard admin panel views using custom admin session.
    Checks for session flag `_is_admin` and validates the stored admin user ID.
    If missing or user is not a superuser, redirects to admin login.
    """
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        admin_flag = request.session.get('_is_admin')
        admin_user_id = request.session.get('_admin_user_id')
        if not admin_flag or not admin_user_id:
            messages.error(request, "Admin access required. Please log in.")
            return redirect('admin_login')
        try:
            from .models import CustomUser
            admin_user = CustomUser.objects.get(id=admin_user_id)
            if not admin_user.is_superuser:
                raise CustomUser.DoesNotExist
        except CustomUser.DoesNotExist:
            messages.error(request, "Admin access required. Please log in.")
            return redirect('admin_login')
        request.admin_user = admin_user
        return view_func(request, *args, **kwargs)
    return wrapper


from django.shortcuts import redirect
from .models import CustomUser


# Helpers

def _parse_iso(dt_str):
    """Parse an ISO datetime string and make it timezone-aware. Returns None on failure."""
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
    """Remove all pending-user OTP keys from session (password reset / email change)."""
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


# OTP Session Required  (security-hardened)

def otp_session_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        has_registration = bool(request.session.get("pending_registration"))
        has_pending_user = bool(request.session.get("pending_user_id"))

        # ── 1. Session must exist ──
        if not has_registration and not has_pending_user:
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


# Admin Required

def admin_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        # Check admin session flags
        if not request.session.get("_is_admin") or not request.session.get("_admin_user_id"):
            messages.error(request, "Admin access required. Please log in.")
            return redirect("admin_login")
        try:
            admin_user = CustomUser.objects.get(id=request.session.get("_admin_user_id"), is_superuser=True)
        except CustomUser.DoesNotExist:
            messages.error(request, "Admin account not found. Please log in again.")
            # Clear possibly stale admin flags
            request.session.pop("_is_admin", None)
            request.session.pop("_admin_user_id", None)
            return redirect("admin_login")
        # Attach admin user to request for view usage and logging
        request.user = admin_user
        return view_func(request, *args, **kwargs)
    return wrapper

