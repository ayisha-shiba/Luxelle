import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .models import WalletTransaction


# ─── Helpers ─────────────────────────────────────────────────────────────────

FILTER_MAP = {
    "credits": Q(txn_type=WalletTransaction.CREDIT),
    "debits":  Q(txn_type=WalletTransaction.DEBIT),
    "refunds": Q(txn_type=WalletTransaction.CREDIT, sub_type=WalletTransaction.SUB_REFUND),
    "topups":  Q(txn_type=WalletTransaction.CREDIT, sub_type=WalletTransaction.SUB_TOPUP),
}


# ─── Wallet home ─────────────────────────────────────────────────────────────

@login_required
@never_cache
def wallet_view(request):
    from payments import services as pay_services

    wallet = services.get_wallet(request.user)

    # --- Filtering ---
    txn_filter = request.GET.get("filter", "all")
    q_search   = request.GET.get("q", "").strip()

    qs = wallet.transactions.select_related("order", "order_item")

    if txn_filter in FILTER_MAP:
        qs = qs.filter(FILTER_MAP[txn_filter])

    if q_search:
        qs = qs.filter(
            Q(reason__icontains=q_search) |
            Q(order__order_number__icontains=q_search)
        )

    # --- Pagination ---
    page_obj = Paginator(qs, 10).get_page(request.GET.get("page"))

    # --- Razorpay key for top-up modal ---
    razorpay_key_id = settings.RAZORPAY_KEY_ID

    return render(request, "wallet/wallet.html", {
        "wallet": wallet,
        "transactions": page_obj,
        "txn_filter": txn_filter,
        "q_search": q_search,
        "razorpay_key_id": razorpay_key_id,
    })


# ─── Top-up: initiate Razorpay order ─────────────────────────────────────────

@login_required
@require_POST
def wallet_topup_initiate(request):
    """Create a Razorpay order for a wallet top-up and return its details."""
    try:
        raw = request.POST.get("amount", "0").strip()
        amount = Decimal(raw)
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        if amount > Decimal("100000"):
            raise ValueError("Maximum top-up amount is ₹1,00,000.")
    except (InvalidOperation, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("wallet")

    from payments import services as pay_services
    import razorpay

    client = pay_services.get_client()
    try:
        rzp_order = client.order.create({
            "amount":          pay_services.to_paise(amount),
            "currency":        "INR",
            "receipt":         f"wt-{str(request.user.id)[:36]}",
            "payment_capture": 1,
        })
    except razorpay.errors.BadRequestError:
        messages.error(request, "Could not create payment order. Please try again.")
        return redirect("wallet")

    # Store in session for callback verification
    request.session["wallet_topup"] = {
        "razorpay_order_id": rzp_order["id"],
        "amount":            str(amount),
        "user_id":           str(request.user.id),
    }

    return render(request, "wallet/topup_pay.html", {
        "razorpay_key_id":  settings.RAZORPAY_KEY_ID,
        "razorpay_order_id": rzp_order["id"],
        "amount_paise":      pay_services.to_paise(amount),
        "amount_display":    amount,
        "user_name":         request.user.get_full_name() or request.user.email,
    })


# ─── Top-up: callback after payment ──────────────────────────────────────────

@login_required
@require_POST
def wallet_topup_callback(request):
    """Verify Razorpay signature and credit wallet."""
    params = {
        "razorpay_order_id":   request.POST.get("razorpay_order_id", ""),
        "razorpay_payment_id": request.POST.get("razorpay_payment_id", ""),
        "razorpay_signature":  request.POST.get("razorpay_signature", ""),
    }

    # Pull session data
    topup_data = request.session.get("wallet_topup", {})
    if topup_data.get("razorpay_order_id") != params["razorpay_order_id"]:
        messages.error(request, "Payment session mismatch. Top-up cancelled.")
        return redirect("wallet")

    from payments import services as pay_services

    if not pay_services.verify_razorpay_signature(params):
        messages.error(request, "Payment verification failed. Your wallet was NOT charged.")
        return redirect("wallet")

    amount = Decimal(topup_data["amount"])
    _, credited = services.top_up(
        request.user, amount, params["razorpay_payment_id"]
    )

    request.session.pop("wallet_topup", None)

    if credited:
        messages.success(request, f"₹{amount:,.2f} has been added to your wallet successfully!")
    else:
        messages.info(request, "This payment was already credited to your wallet.")

    return redirect("wallet")
