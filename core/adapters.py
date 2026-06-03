import logging

from django.contrib import messages
from django.shortcuts import redirect
from django.contrib.auth import get_user_model
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger(__name__)
User = get_user_model()


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom adapter for Google social login.

    - Connects Google login to an existing user with the same email.
    - Activates the user on first social signup.
    - Stores first / last name when available.
    - Redirects gracefully on OAuth errors instead of showing a raw error page.
    - Suppresses the 'account connected' notification email to avoid SMTP
      errors blocking the login flow.
    """

    def pre_social_login(self, request, sociallogin):
        """Connect an incoming Google login to an existing local account."""
        if sociallogin.is_existing:
            return

        email = sociallogin.account.extra_data.get("email")
        if not email:
            return

        try:
            existing_user = User.objects.get(email__iexact=email)
            sociallogin.connect(request, existing_user)
        except User.DoesNotExist:
            pass

    def save_user(self, request, sociallogin, form=None):
        """Persist the Google user, ensuring is_active / is_verified are set."""
        user = sociallogin.user
        extra = sociallogin.account.extra_data or {}

        user.email = user.email or extra.get("email", "")
        # Guard against Google returning None for name fields
        user.first_name = user.first_name or (extra.get("given_name") or "")
        user.last_name = user.last_name or (extra.get("family_name") or "")
        user.is_active = True
        user.is_verified = True

        if not user.pk:
            user.set_unusable_password()

        user = super().save_user(request, sociallogin, form)
        return user

    def authentication_error(
        self,
        request,
        provider_id,
        error=None,
        exception=None,
        extra_context=None,
    ):
        """Instead of showing a raw 'Social Network Login Failure' page,
        redirect the user back to login with a friendly error message."""
        logger.error(
            "[Google OAuth] Authentication error — provider=%s error=%s exception=%s",
            provider_id,
            error,
            exception,
        )
        messages.error(
            request,
            "Google sign-in failed. Please try again or use your email and password.",
        )
        return redirect("login")

    def send_notification_mail(self, template_prefix, user, context=None, email=None):
        """Suppress the 'account connected' email that allauth sends when an
        existing account is linked to a social login.  Sending it can trigger
        an SMTP error that bubbles up as a 500 during the OAuth callback."""
        try:
            super().send_notification_mail(template_prefix, user, context, email)
        except Exception as exc:
            logger.warning(
                "[Google OAuth] Notification email failed (non-fatal): %s", exc
            )
