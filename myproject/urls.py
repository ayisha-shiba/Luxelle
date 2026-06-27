from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.templatetags.static import static as static_url
from django.views.generic.base import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("favicon.ico", RedirectView.as_view(url=static_url("image/favicon.ico"), permanent=True)),
    path("payments/", include("payments.urls")),
    path("wallet/", include("wallet.urls")),
    path("", include("core.urls")),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
