"""
utils.py — Luxelle Ecommerce (app: core)
Utility functions: OTP generation, email sending, session helpers.
"""

import random
import string
import logging

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 10


# ─────────────────────────────────────────────
# OTP Utilities
# ─────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Generate a secure numeric OTP."""
    return "".join(random.choices(string.digits, k=length))


def create_otp_for_user(user, purpose: str):
    """
    Create (or replace) an OTP record for a user.
    Args:
        user: CustomUser instance
        purpose: 'registration' or 'password_reset'
    Returns:
        OTPVerification instance
    """
    from .models import OTPVerification

    # Invalidate any existing unused OTPs for same user + purpose
    OTPVerification.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)

    otp_code   = generate_otp()
    expires_at = timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

    otp_obj = OTPVerification.objects.create(
        user=user,
        otp=otp_code,
        purpose=purpose,
        expires_at=expires_at,
    )
    return otp_obj


def verify_otp(user, otp_input: str, purpose: str) -> tuple[bool, str]:
    """
    Verify an OTP for a given user and purpose.
    Returns:
        (True, "")         — if valid
        (False, "reason")  — if invalid
    """
    from .models import OTPVerification

    try:
        otp_obj = OTPVerification.objects.filter(
            user=user,
            purpose=purpose,
            is_used=False,
        ).latest("created_at")
    except OTPVerification.DoesNotExist:
        return False, "No OTP found. Please request a new one."

    if otp_obj.otp != otp_input:
        return False, "Incorrect OTP. Please try again."

    if not otp_obj.is_valid():
        return False, "OTP has expired. Please request a new one."

    otp_obj.is_used = True
    otp_obj.save(update_fields=["is_used"])

    return True, ""


# ─────────────────────────────────────────────
# Email Utilities
# ─────────────────────────────────────────────

def send_otp_email(user, otp_code: str, purpose: str) -> bool:
    """
    Send an OTP email to the user.
    Returns True if sent successfully, False on error.
    """
    subjects = {
        "registration":   "Luxelle — Verify Your Email",
        "password_reset": "Luxelle — Password Reset OTP",
    }
    subject = subjects.get(purpose, "Luxelle — OTP Verification")

    if purpose == "registration":
        body = (
            f"Hi {user.first_name or 'there'},\n\n"
            f"Welcome to Luxelle! Use the OTP below to verify your email address.\n\n"
            f"OTP: {otp_code}\n\n"
            f"This OTP is valid for {OTP_EXPIRY_MINUTES} minutes.\n\n"
            f"If you did not create an account, please ignore this email.\n\n"
            f"— The Luxelle Team"
        )
    else:
        body = (
            f"Hi {user.first_name or 'there'},\n\n"
            f"We received a request to reset your Luxelle password. Use the OTP below.\n\n"
            f"OTP: {otp_code}\n\n"
            f"This OTP is valid for {OTP_EXPIRY_MINUTES} minutes.\n\n"
            f"If you did not request this, please ignore this email.\n\n"
            f"— The Luxelle Team"
        )

    try:
        print(f"DEBUG: Sending OTP to → {user.email}")
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        logger.info(f"OTP email sent to {user.email} for purpose: {purpose}")
        return True
    # except Exception as exc:
    #     logger.error(f"Failed to send OTP email to {user.email}: {exc}")
    #     return False
    except Exception as exc:
        print("\n\nSMTP ERROR:\n", exc, "\n\n")
        logger.error(f"Failed to send OTP email to {user.email}: {exc}")
    return False


# ─────────────────────────────────────────────
# Session Helpers
# ─────────────────────────────────────────────

def set_pending_user_session(request, user_id: str, purpose: str):
    """Store a pending (unverified) user ID in the session."""
    request.session["pending_user_id"] = str(user_id)
    request.session["otp_purpose"]     = purpose
    request.session.set_expiry(600)  # 10 minutes


def get_pending_user(request):
    """Retrieve the pending CustomUser from session. Returns None if not found."""
    from .models import CustomUser

    user_id = request.session.get("pending_user_id")
    if not user_id:
        return None
    try:
        return CustomUser.objects.get(pk=user_id)
    except CustomUser.DoesNotExist:
        return None


def clear_pending_user_session(request):
    """Clear OTP-related session keys after verification."""
    request.session.pop("pending_user_id", None)
    request.session.pop("otp_purpose", None)