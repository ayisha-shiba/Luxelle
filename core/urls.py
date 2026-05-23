"""
urls.py — Luxelle Ecommerce (app: core)
"""

from django.urls import path
from . import views

urlpatterns = [
    # ── Home ──────────────────────────────────────────────
    path("", views.home_view, name="home"),

    # ── Authentication ─────────────────────────────────────
    path("register/",    views.register_view, name="register"),
    path("login/",       views.login_view,    name="login"),
    path("logout/",      views.logout_view,   name="logout"),

    # ── OTP (Registration) ─────────────────────────────────
    path("verify-otp/",  views.verify_otp_view, name="verify_otp"),
    path("resend-otp/",  views.resend_otp_view, name="resend_otp"),

    # ── Forgot / Reset Password ────────────────────────────
    path("forgot-password/",         views.forgot_password_view,     name="forgot_password"),
    path("forgot-password/otp/",     views.forgot_password_otp_view, name="forgot_password_otp"),
    path("set-new-password/",        views.set_new_password_view,    name="set_new_password"),

    # ── Profile ────────────────────────────────────────────
    path("profile/",       views.profile_view,      name="profile"),
    path("profile/edit/",  views.profile_edit_view, name="profile_edit"),

    # ── Addresses ──────────────────────────────────────────
    path("addresses/",                            views.addresses_view,          name="addresses"),
    path("addresses/<uuid:address_id>/edit/",     views.address_edit_view,       name="address_edit"),
    path("addresses/<uuid:address_id>/delete/",   views.address_delete_view,     name="address_delete"),
    path("addresses/<uuid:address_id>/default/",  views.address_set_default_view,name="address_set_default"),
]