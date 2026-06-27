from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from . import services


@login_required
@never_cache
def wallet_view(request):
    wallet = services.get_wallet(request.user)

    transactions = wallet.transactions.select_related("order")
    page = Paginator(transactions, 10).get_page(request.GET.get("page"))

    return render(request, "wallet/wallet.html", {
        "wallet": wallet,
        "transactions": page,
    })
