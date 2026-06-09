from django.urls import path

from . import views, admin_views


urlpatterns = [

    # User
    path("", views.home_view, name="home"),
    path("home/", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),

    path("verify-otp/", views.verify_otp_view, name="verify_otp"),
    path("resend-otp/", views.resend_otp_view, name="resend_otp"),

    path("forgot-password/", views.forgot_password_view, name="forgot_password"),
    path("forgot-password/otp/", views.forgot_password_otp_view, name="forgot_password_otp"),
    path("forgot-password/resend/", views.resend_forgot_password_otp_view, name="resend_forgot_password_otp"),
    path("forgot-password/reset/", views.set_new_password_view, name="set_new_password"),

    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit_view, name="profile_edit"),
    path("profile/change-password/", views.change_password_view, name="change_password"),
    path("profile/change-email/", views.change_email_view, name="change_email"),
    path("profile/change-email/verify/", views.verify_email_otp_view, name="verify_email_otp"),
    path("profile/change-email/resend/", views.resend_email_otp_view, name="resend_email_otp"),
    path("profile/delete/", views.delete_account_view, name="delete_account"),

    path("profile/addresses/", views.addresses_view, name="addresses"),
    path("profile/addresses/<uuid:address_id>/edit/", views.address_edit_view, name="address_edit"),
    path("profile/addresses/<uuid:address_id>/delete/", views.address_delete_view, name="address_delete"),
    path("profile/addresses/<uuid:address_id>/set-default/", views.address_set_default_view, name="address_set_default"),

    path("orders/", views.orders_view, name="orders"),
    path("wishlist/", views.wishlist_view, name="wishlist"),

    # Admin
    path("admin-panel/", admin_views.admin_login_view, name="admin_login"),
    path("admin-panel/logout/", admin_views.admin_logout_view, name="admin_logout"),
    path("admin-panel/dashboard/", admin_views.admin_dashboard_view, name="admin_dashboard"),

    path("admin-panel/users/", admin_views.admin_user_management_view, name="admin_users"),
    path("admin-panel/users/profile/", admin_views.admin_user_profile_view, name="admin_user_profile"),
    path("admin-panel/users/<uuid:user_id>/toggle-status/", admin_views.admin_toggle_user_status_view, name="admin_toggle_user_status"),
    path("admin-panel/users/<uuid:user_id>/delete/", admin_views.admin_delete_user_view, name="admin_delete_user"),

    path("admin-panel/categories/", admin_views.admin_category_list_view, name="admin_categories"),
    path("admin-panel/categories/add/", admin_views.admin_category_add_view, name="admin_category_add"),
    path("admin-panel/categories/<int:category_id>/edit/", admin_views.admin_category_edit_view, name="admin_category_edit"),
    path("admin-panel/categories/<int:category_id>/delete/", admin_views.admin_category_delete_view, name="admin_category_delete"),

    path("admin-panel/forgot-password/", admin_views.admin_forgot_password_view, name="admin_forgot_password"),
    path("admin-panel/forgot-password/otp/", admin_views.admin_forgot_password_otp_view, name="admin_forgot_password_otp"),
    path("admin-panel/forgot-password/resend/", admin_views.admin_resend_forgot_password_otp_view, name="admin_resend_forgot_password_otp"),
    path("admin-panel/forgot-password/reset/", admin_views.admin_reset_password_view, name="admin_reset_password"),
]