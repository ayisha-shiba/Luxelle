import re
from decimal import Decimal

from django import forms

from .models import Coupon


class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = [
            "code", "title", "description",
            "discount_type", "discount_value",
            "max_discount_amount", "min_order_amount",
            "applies_to",
            "usage_limit", "per_user_limit", "first_time_only",
            "payment_methods",
            "valid_from", "valid_to",
            "is_active",
        ]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "valid_to":   forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valid_from"].input_formats = ["%Y-%m-%d"]
        self.fields["valid_to"].input_formats   = ["%Y-%m-%d"]
        self.fields["max_discount_amount"].required = False
        self.fields["description"].required = False
        self.fields["title"].required = False
        self.fields["per_user_limit"].required = False
        self.fields["payment_methods"].required = False

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        
        if not code:
            raise forms.ValidationError("Coupon code is required.")
        
        if len(code) < 4 or len(code) > 20:
            raise forms.ValidationError("Coupon code must be between 4 and 20 characters.")
        
        if not re.match(r'^[A-Z0-9]+$', code):
            raise forms.ValidationError("Coupon code must contain only uppercase letters and numbers (no spaces).")

        qs = Coupon.objects.filter(code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A coupon with this code already exists.")
        
        return code

    def clean(self):
        cleaned = super().clean()
        
        # Title validation
        title = cleaned.get("title", "")
        if title:
            title = title.strip()
            if not title or len(title) < 3 or not re.search(r'[a-zA-Z0-9]', title):
                self.add_error("title", "Coupon title must be at least 3 characters and contain alphanumeric characters.")
            elif len(title) > 100:
                self.add_error("title", "Coupon title cannot exceed 100 characters.")
            cleaned["title"] = title
        else:
            self.add_error("title", "Coupon title is required.")

        # Discount type and value
        # Hardcode to percentage
        cleaned["discount_type"] = Coupon.PERCENTAGE
        value = cleaned.get("discount_value")
        
        if value is None:
            self.add_error("discount_value", "Discount percentage is required.")
        else:
            if value <= Decimal("0") or value > Decimal("100"):
                self.add_error("discount_value", "Discount percentage must be between 1 and 100.")

        # Min order and Max discount
        min_order = cleaned.get("min_order_amount")
        if min_order is not None and min_order < Decimal("0"):
            self.add_error("min_order_amount", "Minimum order value cannot be negative.")
        
        max_disc = cleaned.get("max_discount_amount")
        if max_disc is not None and max_disc <= Decimal("0"):
            self.add_error("max_discount_amount", "Maximum discount cap must be greater than 0 if provided.")

        # Applies To
        applies_to = cleaned.get("applies_to")
        # specific_products and specific_categories cannot be easily validated in ModelForm clean 
        # before they are saved if we are not careful with M2M, but we can trust the frontend to send them 
        # and validate them in the view if needed, or we just rely on standard M2M saving.

        # Usage Limits
        global_limit = cleaned.get("usage_limit", 0)
        per_user = cleaned.get("per_user_limit", 0)
        
        if global_limit < 0:
            self.add_error("usage_limit", "Global usage limit cannot be negative.")
        if per_user < 0:
            self.add_error("per_user_limit", "Per user limit cannot be negative.")
            
        if global_limit > 0 and per_user > global_limit:
            self.add_error("per_user_limit", "Per user limit cannot exceed global limit.")

        # Payment Methods
        pm = cleaned.get("payment_methods", "")
        if not pm or pm.strip() == "":
            self.add_error("payment_methods", "At least one payment method must be selected.")

        # Dates
        valid_from = cleaned.get("valid_from")
        valid_to   = cleaned.get("valid_to")
        from django.utils import timezone

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
            if valid_to.date() == valid_from.date():
                self.add_error("valid_to", "Start date and Expiry date cannot be on the same day.")

        return cleaned
