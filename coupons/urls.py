from django.urls import path

from . import admin_views, views

urlpatterns = [
    path("coupon/apply/",  views.apply_coupon,  name="apply_coupon"),
    path("coupon/remove/", views.remove_coupon, name="remove_coupon"),
    path("my-coupons/",    views.my_coupons,    name="my_coupons"),

    # Admin coupon management
    path("admin-panel/coupons/",                        admin_views.admin_coupon_list_view,     name="admin_coupons"),
    path("admin-panel/coupons/add/",                    admin_views.admin_coupon_add_view,      name="admin_coupon_add"),
    path("admin-panel/coupons/generate-code/",          admin_views.admin_coupon_generate_code, name="admin_coupon_generate_code"),
    path("admin-panel/coupons/<int:coupon_id>/edit/",   admin_views.admin_coupon_edit_view,     name="admin_coupon_edit"),
    path("admin-panel/coupons/<int:coupon_id>/toggle/", admin_views.admin_coupon_toggle_view,   name="admin_coupon_toggle"),
    path("admin-panel/coupons/<int:coupon_id>/delete/", admin_views.admin_coupon_delete_view,   name="admin_coupon_delete"),
]
