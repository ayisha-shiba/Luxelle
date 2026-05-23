"""
decorators.py — Luxelle Ecommerce (app: core)
Custom view decorators for access control.
"""

from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def anonymous_required(redirect_url="home"):
    """
    Redirects already-authenticated users away from login/register pages.
    Usage: @anonymous_required()
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                return redirect(redirect_url)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def otp_session_required(view_func):
    """
    Ensures a pending_user_id exists in session before entering OTP views.
    Prevents direct URL access to /verify-otp/ without going through register.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("pending_user_id"):
            messages.error(request, "Session expired. Please start again.")
            return redirect("register")
        return view_func(request, *args, **kwargs)
    return wrapper


def password_reset_session_required(view_func):
    """
    Ensures the password-reset OTP has been verified before showing
    the set-new-password page.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("password_reset_verified"):
            messages.error(request, "Please verify your OTP first.")
            return redirect("forgot_password")
        return view_func(request, *args, **kwargs)
    return wrapper