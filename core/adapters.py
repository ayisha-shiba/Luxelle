import logging

from django.contrib import messages
from django.shortcuts import redirect
from django.contrib.auth import get_user_model
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse

logger = logging.getLogger(__name__)
User = get_user_model()


SUSPENDED_MESSAGE = "Your account has been suspended. Please contact support."


class CustomAccountAdapter(DefaultAccountAdapter):
    def respond_user_inactive(self, request, user):
        messages.error(request, SUSPENDED_MESSAGE)
        return redirect("login")


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            if not sociallogin.user.is_active:
                messages.error(request, SUSPENDED_MESSAGE)
                raise ImmediateHttpResponse(redirect("login"))
            return

        email = sociallogin.account.extra_data.get("email")
        if not email:
            return

        try:
            existing_user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return

        if not existing_user.is_active:
            messages.error(request, SUSPENDED_MESSAGE)
            raise ImmediateHttpResponse(redirect("login"))

        sociallogin.connect(request, existing_user)

    def save_user(self, request, sociallogin, form=None):
        user = sociallogin.user
        extra = sociallogin.account.extra_data or {}

        user.email = user.email or extra.get("email", "")
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
        logger.error(
            "[Google OAuth] Authentication error provider=%s error=%s exception=%s",
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
        try:
            super().send_notification_mail(template_prefix, user, context, email)
        except Exception as exc:
            logger.warning(
                "[Google OAuth] Notification email failed (non-fatal): %s", exc
            )
