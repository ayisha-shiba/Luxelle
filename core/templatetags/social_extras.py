from django import template
from django.core.exceptions import ImproperlyConfigured

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialApp

register = template.Library()


@register.simple_tag(takes_context=True)
def safe_provider_login_url(context, provider, **params) -> str:
    request = context.get("request")
    if request is None:
        return ""

    try:
        if isinstance(provider, str):
            adapter = get_adapter()
            provider = adapter.get_provider(request, provider)
        return provider.get_login_url(request, **params)
    except (SocialApp.DoesNotExist, ImproperlyConfigured, AttributeError):
        return ""
    except Exception:
        return ""
