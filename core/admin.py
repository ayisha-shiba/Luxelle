from django.contrib import admin
from .models import (
    Address, CustomUser, Category, Product,
    Brand, Material, ProductVariant, VariantImage,Order, OrderItem,
)

admin.site.register(Address)
admin.site.register(CustomUser)
admin.site.register(Brand)
admin.site.register(Material)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display    = ("name", "is_listed", "is_deleted", "created_at")
    search_fields   = ("name",)
    list_filter     = ("is_listed", "is_deleted")


class VariantImageInline(admin.TabularInline):
    model = VariantImage
    extra = 3


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display    = ("variant_name", "product", "sku", "sale_price", "stock", "is_default", "is_listed", "is_deleted")
    search_fields   = ("variant_name", "sku", "product__name")
    list_filter     = ("is_listed", "is_deleted", "is_offer")
    inlines         = [VariantImageInline]


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display    = ("name", "category", "brand", "gender", "is_listed", "is_featured", "is_deleted")
    search_fields   = ("name",)
    list_filter     = ("category", "brand", "gender", "is_listed", "is_deleted")
    inlines         = [ProductVariantInline]

