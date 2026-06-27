from django.contrib import admin

from .models import Wallet, WalletTransaction


class WalletTransactionInline(admin.TabularInline):
    model = WalletTransaction
    extra = 0
    readonly_fields = ("txn_type", "amount", "reason", "order", "order_item", "balance_after", "created_at")
    can_delete = False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__email",)
    inlines = [WalletTransactionInline]


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "txn_type", "amount", "reason", "balance_after", "created_at")
    list_filter = ("txn_type",)
    search_fields = ("wallet__user__email", "reason", "order__order_number")
