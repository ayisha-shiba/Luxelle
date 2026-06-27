"""Offer pricing + referral rewards.

`best_offer_for` is the single source of truth for the effective price, called
from product listing, detail, cart and checkout so the number is consistent
everywhere.
"""
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.pricing import money
from .models import Offer, ReferralProfile


def _live_filter():
    now = timezone.now()
    return Q(is_active=True, valid_from__lte=now, valid_to__gte=now)


def best_offer_percent(variant):
    """Largest live discount % applicable to this variant — the bigger of its
    product offer and its category offer."""
    product = variant.product
    offers = Offer.objects.filter(_live_filter()).filter(
        Q(offer_type=Offer.PRODUCT,  product=product)
        | Q(offer_type=Offer.CATEGORY, category=product.category_id)
    )
    percents = [o.discount_percent for o in offers]
    return max(percents) if percents else 0


def best_offer_for(variant):
    """Return pricing for a variant after the best offer.

    {percent, discount_amount, effective_price}. Both offers are percentages on
    the same base (sale_price), so the largest percent is also the largest rupee
    saving — no separate comparison needed.
    """
    percent = best_offer_percent(variant)
    sale_price = Decimal(variant.sale_price)
    if percent <= 0:
        return {"percent": 0, "discount_amount": Decimal("0.00"), "effective_price": money(sale_price)}

    discount = money(sale_price * Decimal(percent) / Decimal(100))
    return {
        "percent": percent,
        "discount_amount": discount,
        "effective_price": money(sale_price - discount),
    }


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------

REFERRAL_REWARD = Decimal(str(getattr(settings, "REFERRAL_REWARD_AMOUNT", "100.00")))


def get_or_create_profile(user):
    profile, created = ReferralProfile.objects.get_or_create(
        user=user, defaults={"code": ReferralProfile.generate_code()}
    )
    return profile


def apply_referral_code(new_user, code):
    """Record who referred `new_user`. No reward yet — that happens on their
    first completed order. Ignores self-referral and bad codes."""
    code = (code or "").strip().upper()
    if not code:
        return False

    referrer_profile = ReferralProfile.objects.filter(code=code).first()
    if not referrer_profile or referrer_profile.user_id == new_user.id:
        return False

    profile = get_or_create_profile(new_user)
    if profile.referred_by_id:          # already referred — don't overwrite
        return False
    profile.referred_by = referrer_profile.user
    profile.save(update_fields=["referred_by"])
    return True


def grant_referral_reward_if_due(order):
    """Called when an order completes. If the buyer was referred and hasn't been
    rewarded yet, credit both the buyer and the referrer's wallets, once."""
    from wallet import services as wallet_services

    profile = ReferralProfile.objects.filter(user=order.user).select_related("referred_by").first()
    if not profile or not profile.referred_by_id or profile.reward_granted:
        return

    profile.reward_granted = True
    profile.save(update_fields=["reward_granted"])

    wallet_services.credit(order.user, REFERRAL_REWARD, "Referral reward (welcome bonus)")
    wallet_services.credit(profile.referred_by, REFERRAL_REWARD, "Referral reward (friend's first order)")
