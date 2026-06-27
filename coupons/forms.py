from decimal import Decimal

from django import forms

from .models import Coupon


class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = ["code", "description", "discount_type", "discount_value",
                  "max_discount_amount", "min_order_amount", "valid_from",
                  "valid_to", "usage_limit", "is_active"]
        widgets = {
            "valid_from": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "valid_to":   forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valid_from"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["valid_to"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["max_discount_amount"].required = False
        self.fields["description"].required = False

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        qs = Coupon.objects.filter(code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A coupon with this code already exists.")
        return code

    def clean(self):
        cleaned = super().clean()
        dtype = cleaned.get("discount_type")
        value = cleaned.get("discount_value")
        valid_from = cleaned.get("valid_from")
        valid_to   = cleaned.get("valid_to")

        if dtype == Coupon.PERCENTAGE and value and value > Decimal("90"):
            self.add_error("discount_value", "Percentage discount can't exceed 90%.")

        if valid_from and valid_to and valid_to <= valid_from:
            self.add_error("valid_to", "End date must be after the start date.")

        return cleaned
