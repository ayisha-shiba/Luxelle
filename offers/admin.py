from django.contrib import admin

from .models import Offer, ReferralProfile


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("name", "offer_type", "product", "category", "discount_percent", "is_active", "valid_from", "valid_to")
    list_filter = ("offer_type", "is_active")
    search_fields = ("name",)


@admin.register(ReferralProfile)
class ReferralProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "code", "referred_by", "reward_granted")
    search_fields = ("code", "user__email")
