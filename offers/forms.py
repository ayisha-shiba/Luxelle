from django import forms
from django.utils import timezone

from .models import Offer


class OfferForm(forms.ModelForm):
    class Meta:
        model = Offer
        fields = ["offer_type", "name", "product", "category",
                  "discount_percent", "is_active", "valid_from", "valid_to"]
        widgets = {
            "valid_from": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "valid_to":   forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Accept the value the datetime-local input posts back.
        self.fields["valid_from"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["valid_to"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["product"].required = False
        self.fields["category"].required = False

    def clean(self):
        cleaned = super().clean()
        offer_type = cleaned.get("offer_type")
        product    = cleaned.get("product")
        category   = cleaned.get("category")
        valid_from = cleaned.get("valid_from")
        valid_to   = cleaned.get("valid_to")

        # A product offer needs a product (and no category), vice versa.
        if offer_type == Offer.PRODUCT:
            if not product:
                self.add_error("product", "Pick a product for a product offer.")
            cleaned["category"] = None
        elif offer_type == Offer.CATEGORY:
            if not category:
                self.add_error("category", "Pick a category for a category offer.")
            cleaned["product"] = None

        if valid_from and valid_to and valid_to <= valid_from:
            self.add_error("valid_to", "End date must be after the start date.")

        # Don't allow two active offers competing on the same target.
        target = product if offer_type == Offer.PRODUCT else category
        if offer_type and target and cleaned.get("is_active"):
            dup = Offer.objects.filter(offer_type=offer_type, is_active=True)
            dup = dup.filter(product=target) if offer_type == Offer.PRODUCT else dup.filter(category=target)
            if self.instance.pk:
                dup = dup.exclude(pk=self.instance.pk)
            if dup.exists():
                raise forms.ValidationError("An active offer already exists for this target.")

        return cleaned
