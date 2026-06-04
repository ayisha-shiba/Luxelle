"""
forms.py — Luxelle Ecommerce (app: core)
All Django forms for authentication, profile, and address management.
"""

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import CustomUser, UserProfile, Address

# ─────────────────────────────────────────────
# Reusable widget helpers
# ─────────────────────────────────────────────


def _input(placeholder, type_="text", extra_classes=""):
    return forms.TextInput(
        attrs={
            "placeholder": placeholder,
            "class": f"form-control {extra_classes}",
            "autocomplete": "off",
        }
    )


def _email(placeholder):
    return forms.EmailInput(attrs={"placeholder": placeholder, "class": "form-control"})


def _password(placeholder):
    return forms.PasswordInput(
        attrs={"placeholder": placeholder, "class": "form-control"}, render_value=False
    )


# ─────────────────────────────────────────────
# Registration Form
# ─────────────────────────────────────────────
class RegistrationForm(forms.ModelForm):
    full_name = forms.CharField(
        label="Full Name",
        widget=_input("Full Name"),
    )
    password1 = forms.CharField(
        label="Password",
        widget=_password("Create a password"),
        help_text="Minimum 8 characters.",
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=_password("Confirm your password"),
    )

    class Meta:
        model = CustomUser
        fields = ["email", "phone"]
        widgets = {
            "email":      _email("Email address"),
            "phone":      _input("Phone number", type_="tel"),
        }

    def clean_full_name(self):
        full_name = self.cleaned_data.get("full_name", "").strip()
        if len(full_name.split()) < 2:
            raise ValidationError("Please enter your full name (first and last name).")
        if not all(c.isalpha() or c.isspace() for c in full_name):
            raise ValidationError("Name must contain letters only.")
        return full_name

    def clean_email(self):
        email = self.cleaned_data.get("email", "").lower()
        existing = CustomUser.objects.filter(email=email).first()
        if existing:
            if existing.is_active:
                raise ValidationError("An account with this email already exists.")
            else:
                existing.delete()
        return email

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters.")
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.is_active = False
        if commit:
            user.save()
        return user

# ─────────────────────────────────────────────
# Login Form
# ─────────────────────────────────────────────
class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=_email("Email address"),
        label="Email",
    )
    password = forms.CharField(
        widget=_password("Password"),
        label="Password",
    )

    def clean_email(self):
        return self.cleaned_data.get("email", "").lower()

# ─────────────────────────────────────────────
# OTP Verification Form
# ─────────────────────────────────────────────


class OTPVerificationForm(forms.Form):
    otp = forms.CharField(
        max_length=6,
        min_length=6,
        label="OTP",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Enter 6-digit OTP",
                "class": "form-control otp-input",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "maxlength": "6",
            }
        ),
    )

    def clean_otp(self):
        otp = self.cleaned_data.get("otp", "").strip()
        if not otp.isdigit():
            raise ValidationError("OTP must contain digits only.")
        return otp


# ─────────────────────────────────────────────
# Forgot Password Form
# ─────────────────────────────────────────────


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        widget=_email("Registered email address"),
        label="Email",
    )

    def clean_email(self):
        email = self.cleaned_data.get("email", "").lower()
        if not CustomUser.objects.filter(email=email, is_active=True).exists():
            raise ValidationError("No active account found with this email.")
        return email


# ─────────────────────────────────────────────
# Set New Password Form
# ─────────────────────────────────────────────


class SetNewPasswordForm(forms.Form):
    new_password = forms.CharField(
        label="New Password",
        widget=_password("New password"),
        validators=[validate_password],
    )
    confirm_password = forms.CharField(
        label="Confirm Password",
        widget=_password("Confirm new password"),
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password")
        p2 = cleaned.get("confirm_password")
        if p1 and p2 and p1 != p2:
            self.add_error("confirm_password", "Passwords do not match.")
        return cleaned


# ─────────────────────────────────────────────
# Profile Edit Forms
# ─────────────────────────────────────────────


class ProfileEditForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ["first_name", "last_name", "phone"]
        widgets = {
            "first_name": _input("First name"),
            "last_name": _input("Last name"),
            "phone": _input("Phone number", type_="tel"),
        }

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if phone and not phone.replace("+", "").isdigit():
            raise ValidationError("Enter a valid phone number.")
        return phone


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["avatar", "gender", "date_of_birth", "bio"]
        widgets = {
            "gender": forms.Select(attrs={"class": "form-control"}),
            "date_of_birth": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "bio": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Write a short bio…",
                    "maxlength": 300,
                }
            ),
            "avatar": forms.FileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
        }

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and hasattr(avatar, "size"):
            if avatar.size > 2 * 1024 * 1024:
                raise ValidationError("Image must be under 2 MB.")
            allowed = ["image/jpeg", "image/png", "image/webp"]
            if hasattr(avatar, "content_type") and avatar.content_type not in allowed:
                raise ValidationError("Only JPEG, PNG, or WebP images are allowed.")
        return avatar


# ─────────────────────────────────────────────
# Address Form
# ─────────────────────────────────────────────


class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = [
            "full_name",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "address_type",
            "is_default",
        ]
        widgets = {
            "full_name": _input("Full name"),
            "phone": _input("Phone number", type_="tel"),
            "address_line1": _input("Street address"),
            "address_line2": _input("Apartment, suite, etc. (optional)"),
            "city": _input("City"),
            "state": _input("State / Province"),
            "postal_code": _input("Postal / ZIP code"),
            "country": _input("Country"),
            "address_type": forms.Select(attrs={"class": "form-control"}),
            "is_default": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone.replace("+", "").isdigit():
            raise ValidationError("Enter a valid phone number.")
        return phone

    def clean_postal_code(self):
        code = self.cleaned_data.get("postal_code", "").strip()
        if not code.isdigit():
            raise ValidationError("Postal code must be numeric.")
        return code
