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
        self.fields["valid_from"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d"]
        self.fields["valid_to"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d"]
        self.fields["product"].required = False
        self.fields["category"].required = False

    def clean(self):
        cleaned = super().clean()
        offer_type = cleaned.get("offer_type")
        name       = cleaned.get("name", "")
        product    = cleaned.get("product")
        category   = cleaned.get("category")
        discount   = cleaned.get("discount_percent")
        valid_from = cleaned.get("valid_from")
        valid_to   = cleaned.get("valid_to")

        discount_type = "percentage"
        if self.data:
            discount_type = self.data.get("dummy_discount_type", "percentage")

        # 1. Name validation 
        import re
        if not name or not name.strip():
            if name and len(name) > 0:
                self.add_error("name", "Offer name cannot consist of only spaces.")
            else:
                self.add_error("name", "Offer name is required.")
        else:
            name = name.strip()
            cleaned["name"] = name
            
            if len(name) < 3:
                self.add_error("name", "Offer name must be at least 3 characters.")
            elif not re.search(r'[a-zA-Z0-9]', name):
                self.add_error("name", "Offer name must contain at least one letter or number.")

            
            if cleaned.get("is_active"):
                dup_qs = Offer.objects.filter(name__iexact=name, is_active=True)
                if self.instance.pk:
                    dup_qs = dup_qs.exclude(pk=self.instance.pk)
                if dup_qs.exists():
                    self.add_error("name", "An active offer with this name already exists.")

        # 3. Discount Validation 
        if discount is not None:
            if discount <= 0:
                if discount_type == "percentage":
                    self.add_error("discount_percent", "Percentage discount must be between 1 and 100.")
                else:
                    self.add_error("discount_percent", "Discount amount must be greater than 0.")
            elif discount_type == "percentage" and discount > 100:
                self.add_error("discount_percent", "Percentage discount must be between 1 and 100.")

        # 4. Date Validation
        now = timezone.now()

        if valid_from:
            if not self.instance.pk or ('valid_from' in self.changed_data):
                if valid_from.date() < now.date():
                    self.add_error("valid_from", "Start date cannot be in the past.")
                if valid_from.year < now.year:
                    self.add_error("valid_from", "Please select a valid future date.")

        if valid_to:
            if valid_to.year < now.year:
                self.add_error("valid_to", "Please select a valid future date.")

        if valid_from and valid_to:
            if valid_to <= valid_from:
                self.add_error("valid_to", "Expiry date must be after the start date.")
            elif valid_to.date() == valid_from.date():
                self.add_error("valid_to", "Start date and Expiry date cannot be on the same day.")

        # 5. Applicability Validation
        if offer_type == Offer.PRODUCT:
            if not product:
                self.add_error("product", "Pick a product for a product offer.")
            cleaned["category"] = None
        elif offer_type == Offer.CATEGORY:
            if not category:
                self.add_error("category", "Pick a category for a category offer.")
            cleaned["product"] = None
        else:
            cleaned["product"] = None
            cleaned["category"] = None

        # 6. Competing offer validation 
        target = product if offer_type == Offer.PRODUCT else category
        if offer_type != Offer.GLOBAL and target and cleaned.get("is_active"):
            dup = Offer.objects.filter(offer_type=offer_type, is_active=True)
            dup = dup.filter(product=target) if offer_type == Offer.PRODUCT else dup.filter(category=target)
            if self.instance.pk:
                dup = dup.exclude(pk=self.instance.pk)
            if dup.exists():
                raise forms.ValidationError("An active offer already exists for this target.")

        return cleaned
