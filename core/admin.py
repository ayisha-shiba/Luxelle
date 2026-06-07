from django.contrib import admin
from .models import Address, CustomUser, Category, Product, ProductImage

admin.site.register(Address)
admin.site.register(CustomUser)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display    = ("name", "is_listed", "is_deleted", "created_at")
    search_fields   = ("name",)
    list_filter     = ("is_listed", "is_deleted")

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 3

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display    = ("name", "category", "price", "stock", "is_listed", "is_deleted")
    search_fields   = ("name", "brand")
    list_filter     = ("category", "is_listed", "is_deleted")
    inlines         = [ProductImageInline] 