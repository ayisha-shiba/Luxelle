from django.contrib.auth import get_user_model
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

User = get_user_model()


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom adapter for Google social login.

    - Connects Google login to an existing user with the same email.
    - Activates the user on first social signup.
    - Stores first / last name when available.
    """

    def pre_social_login(self, request, sociallogin):
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
        user = sociallogin.user
        user.email = user.email or sociallogin.account.extra_data.get("email")
        user.first_name = user.first_name or sociallogin.account.extra_data.get("given_name", "")
        user.last_name = user.last_name or sociallogin.account.extra_data.get("family_name", "")
        user.is_active = True
        user.is_verified = True

        if not user.pk:
            user.set_unusable_password()

        user = super().save_user(request, sociallogin, form)
        return user
