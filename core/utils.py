import random
import string
import logging

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES   = 10  # increased from 1 minute to allow user time
OTP_RESEND_COOLDOWN  = 60   # seconds — enforced server-side, not by frontend timer


# ─────────────────────────────────────────────
# OTP Generation
# ─────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Generate a secure numeric OTP of the given length."""
    return "".join(random.choices(string.digits, k=length))


# ─────────────────────────────────────────────
# OTP Create
# ─────────────────────────────────────────────

def create_otp_for_user(user, purpose: str):
    
    from .models import OTPVerification

    # Cleanup existing OTPs for the user to prevent accumulation and reuse
    OTPVerification.objects.filter(user=user, purpose=purpose).delete()

    # Globally clean up expired OTPs
    OTPVerification.objects.filter(expires_at__lt=timezone.now()).delete()

    otp_code   = generate_otp()
    expires_at = timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

    otp_obj = OTPVerification.objects.create(
        user=user,
        otp=otp_code,
        purpose=purpose,
        expires_at=expires_at,
    )
    return otp_obj


# ─────────────────────────────────────────────
# Resend Cooldown Check
# ─────────────────────────────────────────────

def check_resend_cooldown(user, purpose: str) -> tuple[bool, int]:
    from .models import OTPVerification

    try:
        latest_otp = OTPVerification.objects.filter(
            user=user, purpose=purpose
        ).latest("created_at")
        seconds_left = latest_otp.seconds_until_resend_allowed()
        if seconds_left > 0:
            return False, seconds_left
    except OTPVerification.DoesNotExist:
        pass   # No prior OTP — resend is allowed

    return True, 0


# ─────────────────────────────────────────────
# OTP Verify
# ─────────────────────────────────────────────

def verify_otp(user, otp_input: str, purpose: str) -> tuple[bool, str]:

    from .models import OTPVerification


    OTPVerification.objects.filter(expires_at__lt=timezone.now()).delete()

    try:
        otp_obj = OTPVerification.objects.filter(
            user=user,
            purpose=purpose,
            is_used=False,
        ).latest("created_at")
    except OTPVerification.DoesNotExist:
        return False, "No active OTP found. Please request a new one."

    if not otp_obj.is_valid():
        return False, "OTP has expired. Please request a new one."

    if otp_obj.otp != otp_input:
        return False, "Incorrect OTP. Please try again."

    OTPVerification.objects.filter(user=user, purpose=purpose).delete()

    return True, ""



# Email Sending


def send_otp_email(user, otp_code: str, purpose: str) -> bool:
    subjects = {
        "registration":   "Luxelle — Verify Your Email",
        "password_reset": "Luxelle — Password Reset OTP",
        "email_change":   "Luxelle — Confirm Email Change",
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
    elif purpose == "email_change":
        body = (
            f"Hi {user.first_name or 'there'},\n\n"
            f"We received a request to change the email address on your Luxelle account.\n\n"
            f"OTP: {otp_code}\n\n"
            f"This OTP is valid for {OTP_EXPIRY_MINUTES} minutes.\n\n"
            f"If you did not request this change, please ignore this email.\n\n"
            f"— The Luxelle Team"
        )
    else:
        body = (
            f"Hi {user.first_name or 'there'},\n\n"
            f"We received a request to reset your Luxelle password.\n\n"
            f"OTP: {otp_code}\n\n"
            f"This OTP is valid for {OTP_EXPIRY_MINUTES} minutes.\n\n"
            f"If you did not request this, please ignore this email.\n\n"
            f"— The Luxelle Team"
        )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        logger.info(f"OTP email sent to {user.email} for purpose: {purpose}")
        return True
    except Exception as exc:
        logger.error(f"Failed to send OTP email to {user.email}: {exc}")
        return False


# Session Helpers

# def set_pending_user_session(request, user_id, purpose: str):
#     from .models import OTPVerification
#     from django.utils import timezone
    
#     request.session.cycle_key()  
#     request.session["pending_user_id"] = str(user_id)
#     request.session["otp_purpose"]     = purpose
    
#     try:
#         latest_otp = OTPVerification.objects.filter(
#             user_id=user_id, purpose=purpose, is_used=False
#         ).latest("created_at")
#         request.session["otp_created_at"] = latest_otp.created_at.isoformat()
#         request.session["otp_expires_at"] = latest_otp.expires_at.isoformat()
#     except Exception:
#         now = timezone.now()
#         request.session["otp_created_at"] = now.isoformat()
#         request.session["otp_expires_at"] = (now + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()

#     request.session.set_expiry(OTP_EXPIRY_MINUTES * 60)
#     request.session.modified = True  
#     request.session.save()       

def set_pending_user_session(request, user_id, purpose: str):
    
    request.session["pending_user_id"] = str(user_id)
    request.session["otp_purpose"]     = purpose
    
    try:
        from .models import OTPVerification
        latest_otp = OTPVerification.objects.filter(
            user_id=user_id, purpose=purpose, is_used=False
        ).latest("created_at")
        request.session["otp_created_at"] = latest_otp.created_at.isoformat()
        request.session["otp_expires_at"] = latest_otp.expires_at.isoformat()
    except Exception:
        now = timezone.now()
        request.session["otp_created_at"] = now.isoformat()
        request.session["otp_expires_at"] = (now + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()

    request.session.set_expiry(OTP_EXPIRY_MINUTES * 60)
    request.session.modified = True
    request.session.save()      

def get_pending_user(request):
    from .models import CustomUser

    user_id = request.session.get("pending_user_id")
    if not user_id:
        return None
    try:
        return CustomUser.objects.get(pk=user_id)
    except CustomUser.DoesNotExist:
        return None


def clear_pending_user_session(request):
    for key in (
        # Registration flow
        "pending_registration",
        "pending_otp",
        "pending_otp_expires_at",
        "pending_otp_sent_at",
        # Shared / pending-user flow
        "pending_user_id",
        "otp_purpose",
        "otp_created_at",
        "otp_expires_at",
    ):
        request.session.pop(key, None)