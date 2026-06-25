from django.urls import path

from . import views

urlpatterns = [
    path("start/<str:order_number>/", views.payment_start, name="payment_start"),
    path("callback/", views.payment_callback, name="payment_callback"),
    path("failed/<str:order_number>/", views.payment_failure, name="payment_failure"),
]
