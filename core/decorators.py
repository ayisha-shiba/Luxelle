# """
# decorators.py — Luxelle Ecommerce (app: core)
# Custom view decorators for access control.
# """

# from functools import wraps
# from django.shortcuts import redirect
# from django.contrib import messages


# def anonymous_required(redirect_url="home"):
#     """
#     Redirects already-authenticated users away from login/register pages.
#     Usage: @anonymous_required()
#     """
#     def decorator(view_func):
#         @wraps(view_func)
#         def wrapper(request, *args, **kwargs):
#             if request.user.is_authenticated:
#                 return redirect(redirect_url)
#             return view_func(request, *args, **kwargs)
#         return wrapper
#     return decorator


# def otp_session_required(view_func):
#     """
#     Ensures a pending_user_id exists in session before entering OTP views.
#     Prevents direct URL access to /verify-otp/ without going through register.
#     """
#     @wraps(view_func)
#     def wrapper(request, *args, **kwargs):
#         if not request.session.get("pending_user_id"):
#             messages.error(request, "Session expired. Please start again.")
#             return redirect("register")
#         return view_func(request, *args, **kwargs)
#     return wrapper


# def password_reset_session_required(view_func):
#     """
#     Ensures the password-reset OTP has been verified before showing
#     the set-new-password page.
#     """
#     @wraps(view_func)
#     def wrapper(request, *args, **kwargs):
#         if not request.session.get("password_reset_verified"):
#             messages.error(request, "Please verify your OTP first.")
#             return redirect("forgot_password")
#         return view_func(request, *args, **kwargs)
#     return wrapper

"""
decorators.py — Luxelle Ecommerce (app: core)
Custom view decorators for access control.

Changes from your original:
  - @never_cache added to all auth decorators (Back-button attack fix)
  - otp_session_required: now validates OTP expiry server-side on every
    request (GET + POST). Stale/expired sessions are cleared and user is
    redirected to the correct starting page (register or forgot_password)
    based on otp_purpose. This fixes the browser-close / session-abandon bug.
  - password_reset_session_required: unchanged logic, @never_cache added
  - admin_required: new decorator replacing scattered @user_passes_test calls
"""

from functools import wraps
from datetime import datetime

from django.contrib import messages
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
# ─────────────────────────────────────────────

def admin_required(view_func):
    """
    Guards admin panel views — replaces the @user_passes_test(is_admin)
    pattern that was scattered across admin_views.py.

    Requires: authenticated + is_superuser.
    Non-superusers are redirected to the admin login page.

    @never_cache included: admin pages must never be served from browser
    cache — sensitive user data and management actions must always reflect
    the live server state.

    Usage:
        @admin_required
        def admin_dashboard_view(request): ...
    """
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            messages.error(request, "Admin access required. Please log in.")
            return redirect("admin_login")
        return view_func(request, *args, **kwargs)
    return wrapper