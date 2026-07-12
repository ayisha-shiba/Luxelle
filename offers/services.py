
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
    product = variant.product
    offers = Offer.objects.filter(_live_filter()).filter(
        Q(offer_type=Offer.PRODUCT,  product=product)
        | Q(offer_type=Offer.CATEGORY, category=product.category_id)
    )
    percents = [o.discount_percent for o in offers]
    return max(percents) if percents else 0


def best_offer_for(variant):
    
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


# Referral

import logging
from django.db import transaction
from wallet import services as wallet_services

logger = logging.getLogger(__name__)

REFERRAL_REWARD = Decimal(str(getattr(settings, "REFERRAL_REWARD_AMOUNT", "100.00")))


def get_or_create_profile(user):
    profile, created = ReferralProfile.objects.get_or_create(
        user=user, defaults={"code": ReferralProfile.generate_code()}
    )
    return profile


@transaction.atomic
def apply_referral_code(new_user, code):
    code = (code or "").strip().upper()
    if not code:
        return False

    referrer_profile = ReferralProfile.objects.filter(code=code).first()
    if not referrer_profile or referrer_profile.user_id == new_user.id:
        logger.warning(f"[REFERRAL] Invalid or self-referral code: {code} for user {new_user.email}")
        return False

    referrer = referrer_profile.user
    if not referrer.is_active:
        logger.warning(f"[REFERRAL] Referrer user {referrer.email} is not active for code {code}")
        return False

    profile = get_or_create_profile(new_user)
    
    profile = ReferralProfile.objects.select_for_update().get(pk=profile.pk)
    
    if profile.referred_by_id:
        logger.info(f"[REFERRAL] User {new_user.email} already has a referrer set, skipping.")
        return False

    profile.referred_by = referrer
    profile.reward_granted = True
    profile.save(update_fields=["referred_by", "reward_granted"])

    referrer_reward_amount = Decimal("200.00")
    referee_reward_amount = Decimal("100.00")

    wallet_services.credit(
        user=referrer,
        amount=referrer_reward_amount,
        reason=f"Referral bonus for referring {new_user.email}"
    )

    wallet_services.credit(
        user=new_user,
        amount=referee_reward_amount,
        reason=f"Referral bonus for signing up with code {code}"
    )

    logger.info(
        f"[REFERRAL] Successfully credited rewards. Referrer ({referrer.email}): ₹{referrer_reward_amount}, "
        f"Referee ({new_user.email}): ₹{referee_reward_amount}"
    )
    return True


def grant_referral_reward_if_due(order):
    from wallet import services as wallet_services

    profile = ReferralProfile.objects.filter(user=order.user).select_related("referred_by").first()
    if not profile or not profile.referred_by_id or profile.reward_granted:
        return

    profile.reward_granted = True
    profile.save(update_fields=["reward_granted"])

    wallet_services.credit(order.user, Decimal("100.00"), "Referral reward (welcome bonus)")
    wallet_services.credit(profile.referred_by, Decimal("200.00"), "Referral reward (friend's first order)")

