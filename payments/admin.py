from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("razorpay_order_id", "order", "amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("razorpay_order_id", "razorpay_payment_id", "order__order_number")
    readonly_fields = ("created_at", "updated_at")
