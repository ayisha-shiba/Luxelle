from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('home/', views.home_view, name='home'),
    path('profile/', views.profile_view, name='profile'),
    path('profile/edit/', views.profile_edit_view, name='profile_edit'),
    path('profile/addresses/', views.addresses_view, name='addresses'),
    path('profile/verify-email-otp/', views.verify_otp_view, name='verify_otp'),
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),
    path('forgot-password/otp/', views.forgot_password_otp_view, name='forgot_password_otp'),
    path('forgot-password/reset/', views.set_new_password_view, name='set_new_password'),
]

