from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.templatetags.static import static as static_url
from django.views.generic.base import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    # Serve the favicon site-wide: browsers auto-request /favicon.ico on every
    # page, so this covers the whole site without editing each template.
    path("favicon.ico", RedirectView.as_view(url=static_url("image/favicon.ico"), permanent=True)),
    path("", include("core.urls")),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
