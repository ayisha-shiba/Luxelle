import secrets

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Offer(models.Model):
    """A percentage discount applied on top of a variant's sale_price.
    Either a product offer or a category offer; for any product the larger
    of the two (by rupee value) wins."""

    PRODUCT  = "product"
    CATEGORY = "category"
    TYPE_CHOICES = [
        (PRODUCT,  "Product Offer"),
        (CATEGORY, "Category Offer"),
    ]

    offer_type = models.CharField(max_length=10, choices=TYPE_CHOICES, db_index=True)
    name       = models.CharField(max_length=120)

    product  = models.ForeignKey("core.Product",  on_delete=models.CASCADE, null=True, blank=True, related_name="offers")
    category = models.ForeignKey("core.Category", on_delete=models.CASCADE, null=True, blank=True, related_name="offers")

    discount_percent = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(90)]
    )

    is_active  = models.BooleanField(default=True)
    valid_from = models.DateTimeField()
    valid_to   = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.product if self.offer_type == self.PRODUCT else self.category
        return f"{self.get_offer_type_display()}: {target} ({self.discount_percent}%)"

    @property
    def is_live(self):
        """Active and within its date window right now."""
        now = timezone.now()
        return self.is_active and self.valid_from <= now <= self.valid_to


class ReferralProfile(models.Model):
    """Per-user referral code + who referred them. Reward (wallet credit to both)
    is granted once, on the referred user's first completed order."""

    user        = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_profile")
    code        = models.CharField(max_length=12, unique=True, db_index=True)
    referred_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="referrals_made")
    reward_granted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} ({self.code})"

    @staticmethod
    def generate_code():
        """A short, unambiguous, URL-safe code."""
        while True:
            code = secrets.token_urlsafe(6)[:8].upper().replace("_", "").replace("-", "")
            if len(code) >= 6 and not ReferralProfile.objects.filter(code=code).exists():
                return code
