from django.urls import path

from . import views

urlpatterns = [
    path("",                views.wallet_view,            name="wallet"),
    path("topup/initiate/", views.wallet_topup_initiate,  name="wallet_topup_initiate"),
    path("topup/callback/", views.wallet_topup_callback,  name="wallet_topup_callback"),
]
