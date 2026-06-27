from django.urls import path

from . import admin_views, views

urlpatterns = [
    path("referral/", views.referral_view, name="referral"),
    path("admin-panel/offers/",                    admin_views.admin_offer_list_view,   name="admin_offers"),
    path("admin-panel/offers/add/",                admin_views.admin_offer_add_view,    name="admin_offer_add"),
    path("admin-panel/offers/<int:offer_id>/edit/",   admin_views.admin_offer_edit_view,   name="admin_offer_edit"),
    path("admin-panel/offers/<int:offer_id>/delete/", admin_views.admin_offer_delete_view, name="admin_offer_delete"),
]
