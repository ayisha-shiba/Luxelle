import os

from django.apps import AppConfig
from django.conf import settings



class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.contrib.sessions.models import Session
        for s in Session.objects.all():
            data = s.get_decoded()
            if not data.get('_is_admin'):
                s.delete()
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._ensure_google_social_app, sender=self, weak=False)

    def _ensure_google_social_app(self, sender, **kwargs):
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            return

        try:
            from django.contrib.sites.models import Site
            from django.db.utils import OperationalError, ProgrammingError
            from allauth.socialaccount.models import SocialApp
        except Exception:
            return

        site_domain = os.environ.get("SITE_DOMAIN", "127.0.0.1:8000")
        site_name = os.environ.get("SITE_NAME", "Local")

        try:
            site, created = Site.objects.get_or_create(
                pk=settings.SITE_ID,
                defaults={"domain": site_domain, "name": site_name},
            )
            if not created and (site.domain != site_domain or site.name != site_name):
                site.domain = site_domain
                site.name = site_name
                site.save()

            social_app, _ = SocialApp.objects.get_or_create(
                provider="google",
                name="Google",
                defaults={"client_id": client_id, "secret": client_secret},
            )
            if social_app.client_id != client_id or social_app.secret != client_secret:
                social_app.client_id = client_id
                social_app.secret = client_secret
                social_app.save()
            if not social_app.sites.filter(pk=site.pk).exists():
                social_app.sites.add(site)
        except (OperationalError, ProgrammingError):
            return
