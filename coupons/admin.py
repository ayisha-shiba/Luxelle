from django.contrib import admin

from .models import Coupon, CouponUsage


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "discount_value", "min_order_amount", "is_active", "valid_from", "valid_to", "times_used")
    list_filter = ("discount_type", "is_active")
    search_fields = ("code",)


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ("coupon", "user", "order", "discount_amount", "used_at")
    search_fields = ("coupon__code", "user__email")
