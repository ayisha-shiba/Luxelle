from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from . import services
from .models import ReferralProfile


@login_required
@never_cache
def referral_view(request):
    profile = services.get_or_create_profile(request.user)
    referral_link = request.build_absolute_uri(f"{reverse('register')}?ref={profile.code}")

    referred_count = ReferralProfile.objects.filter(referred_by=request.user).count()

    return render(request, "offers/referral.html", {
        "profile": profile,
        "referral_link": referral_link,
        "referred_count": referred_count,
        "reward_amount": services.REFERRAL_REWARD,
    })
