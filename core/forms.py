

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
import re

from .models import CustomUser, UserProfile, Address, Category, Product, ProductVariant, Brand, Material

PRODUCT_NAME_RE = re.compile(r"^[A-Za-z0-9 '\-]+$")
HEX_COLOR_RE    = re.compile(r"^#[0-9A-Fa-f]{6}$")

PHONE_NORMALIZATION_REGEX = re.compile(r"[^\d+]")
PHONE_ALLOWED_CHARS_REGEX = re.compile(r"^\+?[0-9\-\s\(\)]{10,20}$")


def normalize_phone_number(phone):
    phone = str(phone or "").strip()
    if not phone:
        return ""
    normalized = PHONE_NORMALIZATION_REGEX.sub("", phone)
    if normalized.startswith("+"):
        normalized = normalized[1:]
    if normalized.startswith("0") and len(normalized) == 11:
        normalized = normalized[1:]
    elif normalized.startswith("91") and len(normalized) == 12:
        normalized = normalized[2:]
    return normalized


def validate_phone_number(phone):
    phone = str(phone or "").strip()
    if not phone:
        raise ValidationError("Phone number is required.")
    if not PHONE_ALLOWED_CHARS_REGEX.match(phone):
        raise ValidationError(
            "Enter a valid Indian mobile number using digits, spaces, hyphens, parentheses, or a leading +91."
        )
    normalized = normalize_phone_number(phone)
    if not normalized.isdigit():
        raise ValidationError("Phone number must contain digits only after removing formatting characters.")
    if len(normalized) != 10:
        raise ValidationError("Enter a valid 10-digit Indian mobile number.")
    if normalized[0] not in "6789":
        raise ValidationError("Enter a valid Indian mobile number starting with 6, 7, 8, or 9.")
    return normalized


# ─────────────────────────────────────────────
# Custom Password Validator
# ─────────────────────────────────────────────

def validate_strong_password(password):
    errors = []
    if len(password) < 8:
        errors.append("Password must contain at least 8 characters.")
    if not any(char.isupper() for char in password):
        errors.append("Password must include at least one uppercase letter.")
    if not any(char.islower() for char in password):
        errors.append("Password must include at least one lowercase letter.")
    if not any(char.isdigit() for char in password):
        errors.append("Password must include at least one number.")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", password):
        errors.append("Password must include at least one special character.")
    
    if errors:
        raise ValidationError(errors)

# ─────────────────────────────────────────────
# Reusable widget helpers# ─────────────────────────────────────────────

def _input(placeholder, type_="text", extra_classes=""):
    return forms.TextInput(attrs={
        "placeholder":  placeholder,
        "class":        f"form-control {extra_classes}",
        "autocomplete": "off",
    })


def _email(placeholder):
    return forms.EmailInput(attrs={"placeholder": placeholder, "class": "form-control"})


def _password(placeholder):
    return forms.PasswordInput(
        attrs={"placeholder": placeholder, "class": "form-control"},
        render_value=False,
    )


# ─────────────────────────────────────────────
# Registration Form
# ─────────────────────────────────────────────

class RegistrationForm(forms.ModelForm):
    full_name = forms.CharField(
        label="Full Name",
        widget=_input("Full Name"),
        required=True,
    )

    password1 = forms.CharField(
        label="Password",
        widget=_password("Create a password"),
        validators=[validate_strong_password],
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
        required = {
            "email": True,
            "phone": True,
        }

    def clean_full_name(self):
        name = self.cleaned_data.get("full_name", "").strip()
        if len(name) < 2:
            raise ValidationError("Full name must be at least 2 characters.")
        if not all(ch.isalpha() or ch.isspace() for ch in name):
            raise ValidationError("Full name can only contain letters and spaces.")
        return name

    def clean_email(self):
        email = self.cleaned_data.get("email", "").lower()

        existing_user_qs = CustomUser.objects.filter(email__iexact=email)
        if existing_user_qs.exists():
            if existing_user_qs.filter(is_active=False, is_verified=True).exists():
                raise ValidationError("This email has been suspended by the admin. Please contact support.")

            ghost_qs = existing_user_qs.filter(is_active=False, is_verified=False)
            if ghost_qs.exists():
                ghost_qs.delete()
            else:
                raise ValidationError("An account with this email already exists.")

        return email

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "")
        normalized_phone = validate_phone_number(phone)
        if CustomUser.objects.filter(phone=normalized_phone).exists():
            raise ValidationError("A user with this phone number already exists.")
        return normalized_phone

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        full_name = self.cleaned_data.get("full_name", "").strip()
        parts = full_name.split(maxsplit=1)
        if len(parts) == 2:
            user.first_name = parts[0]
            user.last_name = parts[1]
        else:
            user.first_name = full_name
            user.last_name = ""
        user.set_password(self.cleaned_data["password1"])
        user.is_active = False
        if commit:
            user.save()
        return user


# ─────────────────────────────────────────────
# Login Form# ─────────────────────────────────────────────

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
# OTP Verification Form# ─────────────────────────────────────────────

class OTPVerificationForm(forms.Form):
    otp = forms.CharField(
        max_length=6,
        min_length=6,
        label="OTP",
        widget=forms.TextInput(attrs={
            "placeholder":  "Enter 6-digit OTP",
            "class":        "form-control otp-input",
            "inputmode":    "numeric",
            "autocomplete": "one-time-code",
            "maxlength":    "6",
        }),
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
        try:
            self._user = CustomUser.objects.get(email__iexact=email, is_active=True)
        except CustomUser.DoesNotExist:
            raise ValidationError("No active account found with this email.")
        return email
    def get_user(self):
        return getattr(self, "_user", None)


# ─────────────────────────────────────────────
# Set New Password Form# ─────────────────────────────────────────────

class SetNewPasswordForm(forms.Form):
    new_password = forms.CharField(
        label="New Password",
        widget=_password("New password"),
        validators=[validate_strong_password],
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
# Change Password Form# ─────────────────────────────────────────────

class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(
        label="Current Password",
        widget=_password("Current password"),
    )
    new_password = forms.CharField(
        label="New Password",
        widget=_password("New password"),
        validators=[validate_strong_password],
    )
    confirm_password = forms.CharField(
        label="Confirm New Password",
        widget=_password("Confirm new password"),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_current_password(self):
        current = self.cleaned_data.get("current_password")
        if not self.user.check_password(current):
            raise ValidationError("Current password is incorrect.")
        return current

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password")
        p2 = cleaned.get("confirm_password")
        if p1 and p2 and p1 != p2:
            self.add_error("confirm_password", "Passwords do not match.")
        if p1 and self.user.check_password(p1):
            self.add_error("new_password", "New password must be different from the current password.")
        return cleaned


# ─────────────────────────────────────────────
# Email Change Form
# ─────────────────────────────────────────────

class EmailChangeForm(forms.Form):
    new_email = forms.EmailField(
        label="New Email",
        widget=_email("New email address"),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_new_email(self):
        email = self.cleaned_data.get("new_email", "").lower()
        if email == self.user.email:
            raise ValidationError("New email must be different from your current email.")
        if CustomUser.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise ValidationError("This email is already in use by another account.")
        return email


# ─────────────────────────────────────────────
# Profile Edit Forms# ─────────────────────────────────────────────

class ProfileEditForm(forms.ModelForm):
    full_name = forms.CharField(
        label="Full Name",
        widget=_input("Enter your full name"),
        required=True,
    )

    class Meta:
        model = CustomUser
        fields = ["phone"]
        widgets = {
            "phone": _input("Phone number", type_="tel"),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["full_name"].initial = self.instance.get_full_name()

    def clean_full_name(self):
        name = self.cleaned_data.get("full_name", "").strip()
        if len(name) < 2:
            raise ValidationError("Name must be at least 2 characters.")
        if not all(ch.isalpha() or ch.isspace() for ch in name):
            raise ValidationError("Name can only contain alphabets and spaces.")
        return name

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "")
        normalized_phone = validate_phone_number(phone)
        qs = CustomUser.objects.filter(phone=normalized_phone)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("This phone number is already in use by another user.")
        return normalized_phone

    def save(self, commit=True):
        user      = super().save(commit=False)
        full_name = self.cleaned_data.get("full_name", "").strip()
        parts     = full_name.split(maxsplit=1)
        if len(parts) == 2:
            user.first_name = parts[0]
            user.last_name  = parts[1]
        else:
            user.first_name = full_name
            user.last_name  = ""
        if commit:
            user.save(update_fields=["first_name", "last_name", "phone"])
        return user


class UserProfileForm(forms.ModelForm):
    class Meta:
        model  = UserProfile
        fields = ["avatar", "gender", "date_of_birth", "bio"]
        widgets = {
            "gender":        forms.Select(attrs={"class": "form-control"}),
            "date_of_birth": forms.DateInput(attrs={"class": "form-control", "type": "date", "max": timezone.localdate().isoformat()}),
            "bio":           forms.Textarea(attrs={
                "class":       "form-control",
                "rows":        3,
                "placeholder": "Write a short bio",
                "maxlength":   300,
            }),
            "avatar": forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
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

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get('date_of_birth')
        if dob:
            today = timezone.localdate()
            if dob > today:
                raise ValidationError('Date of birth cannot be in the future.')
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age < 13:
                raise ValidationError('You must be at least 13 years old.')
            if age > 120:
                raise ValidationError('Age cannot exceed 120 years.')
        return dob


# ─────────────────────────────────────────────
# Address Form# ─────────────────────────────────────────────

class AddressForm(forms.ModelForm):
    class Meta:
        model  = Address
        fields = [
            "full_name", "phone", "address_line1", "address_line2",
            "city", "state", "postal_code", "country",
            "address_type", "is_default",
        ]
        widgets = {
            "full_name":     _input("Full name"),
            "phone":         _input("Phone number", type_="tel"),
            "address_line1": _input("Street address"),
            "address_line2": _input("Apartment, suite, etc. (optional)"),
            "city":          _input("City"),
            "state":         _input("State / Province"),
            "postal_code":   _input("Postal / ZIP code"),
            "country":       _input("Country"),
            "address_type":  forms.Select(attrs={"class": "form-control"}),
            "is_default":    forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_full_name(self):
        name = self.cleaned_data.get("full_name", "").strip()
        if len(name) < 2:
            raise ValidationError("Full name must be at least 2 characters.")
        if not all(ch.isalpha() or ch.isspace() for ch in name):
            raise ValidationError("Full name can only contain letters and spaces.")
        return name

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "")
        return validate_phone_number(phone)

    def clean_address_line1(self):
        value = self.cleaned_data.get("address_line1", "").strip()
        if len(value) < 5:
            raise ValidationError("Street address must be at least 5 characters.")
        forbidden = ["<", ">", "{", "}", "|", "\\", "^", "`"]
        if any(c in value for c in forbidden):
            raise ValidationError("Address contains invalid characters.")
        return value

    def clean_city(self):
        city = self.cleaned_data.get("city", "").strip()
        if len(city) < 2:
            raise ValidationError("City name must be at least 2 characters.")
        if not all(ch.isalpha() or ch.isspace() or ch == "-" for ch in city):
            raise ValidationError("City name can only contain letters, spaces, or hyphens.")
        return city

    def clean_state(self):
        state = self.cleaned_data.get("state", "").strip()
        if len(state) < 2:
            raise ValidationError("State/Province must be at least 2 characters.")
        if not all(ch.isalpha() or ch.isspace() or ch == "-" for ch in state):
            raise ValidationError("State name can only contain letters, spaces, or hyphens.")
        return state

    def clean_country(self):
        country = self.cleaned_data.get("country", "").strip()
        if len(country) < 2:
            raise ValidationError("Country name must be at least 2 characters.")
        if not all(ch.isalpha() or ch.isspace() for ch in country):
            raise ValidationError("Country name can only contain letters and spaces.")
        return country

    def clean_postal_code(self):
        code = self.cleaned_data.get("postal_code", "").strip()
        if not code.isdigit():
            raise ValidationError("Postal code must contain digits only.")
        if len(code) < 4 or len(code) > 10:
            raise ValidationError("Postal code must be between 4 and 10 digits.")
        return code


# ─────────────────────────────────────────────
# Category Form (Admin)
# ─────────────────────────────────────────────

class CategoryForm(forms.ModelForm):
    class Meta:
        model  = Category
        fields = ["name", "description", "is_listed", "image"]
        widgets = {
            "name":        _input("Category name"),
            "description": forms.Textarea(attrs={
                "class":       "form-control",
                "rows":        3,
                "placeholder": "Short description (optional)",
                "maxlength":   500,
            }),
            "is_listed":   forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "image":       forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if len(name) < 2:
            raise ValidationError("Category name must be at least 2 characters.")
        if not all(ch.isalnum() or ch.isspace() or ch in "&-" for ch in name):
            raise ValidationError("Category name can only contain letters, numbers, spaces, & and -.")
        qs = Category.objects.filter(name__iexact=name, is_deleted=False)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("A category with this name already exists.")
        return name

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and hasattr(image, "size"):
            if image.size > 2 * 1024 * 1024:
                raise ValidationError("Image must be under 2 MB.")
            allowed = ["image/jpeg", "image/png", "image/webp"]
            if hasattr(image, "content_type") and image.content_type not in allowed:
                raise ValidationError("Only JPEG, PNG, or WebP images are allowed.")
        return image


# ─────────────────────────────────────────────
# Product Forms (Admin)
# ─────────────────────────────────────────────

class ProductForm(forms.ModelForm):
    class Meta:
        model  = Product
        fields = ["name", "short_description", "description", "category", "brand", "is_listed"]
        widgets = {
            "short_description": forms.TextInput(attrs={"placeholder": "One-line summary shown on listings"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(is_deleted=False)
        self.fields["category"].required = True
        self.fields["brand"].queryset    = Brand.objects.filter(is_deleted=False)
        self.fields["brand"].required    = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Product name is required.")
        if len(name) < 3:
            raise ValidationError("Product name must be at least 3 characters.")
        if len(name) > 150:
            raise ValidationError("Product name cannot exceed 150 characters.")
        if "  " in name:
            raise ValidationError("Product name cannot contain consecutive spaces.")
        if not PRODUCT_NAME_RE.match(name):
            raise ValidationError("Only letters, numbers, spaces, apostrophes and hyphens are allowed.")
        if not any(ch.isalpha() for ch in name):
            raise ValidationError("Product name must contain letters — it cannot be only numbers.")
        qs = Product.objects.filter(name__iexact=name, is_deleted=False)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("A product with this name already exists.")
        return name

    def clean_short_description(self):
        value = (self.cleaned_data.get("short_description") or "").strip()
        if not value:
            raise ValidationError("Short description is required.")
        if len(value) < 20:
            raise ValidationError("Short description must be at least 20 characters.")
        if len(value) > 250:
            raise ValidationError("Short description cannot exceed 250 characters.")
        return value

    def clean_description(self):
        value = (self.cleaned_data.get("description") or "").strip()
        if not value:
            raise ValidationError("Full description is required.")
        if len(value) < 50:
            raise ValidationError("Full description must be at least 50 characters.")
        if len(value) > 3000:
            raise ValidationError("Full description cannot exceed 3000 characters.")
        return value


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model  = ProductVariant
        fields = ["sku", "color", "color_hex", "material", "size",
                  "width_cm", "height_cm", "depth_cm",
                  "closure_type", "compartments", "pattern",
                  "original_price", "sale_price", "stock"]
        widgets = {
            "color":        forms.TextInput(attrs={"placeholder": "Black, Brown, Beige"}),
            "width_cm":     forms.NumberInput(attrs={"placeholder": "30", "step": "0.01", "min": "1", "max": "100"}),
            "height_cm":    forms.NumberInput(attrs={"placeholder": "22", "step": "0.01", "min": "1", "max": "100"}),
            "depth_cm":     forms.NumberInput(attrs={"placeholder": "12", "step": "0.01", "min": "1", "max": "100"}),
            "compartments": forms.NumberInput(attrs={"placeholder": "3", "min": "1", "max": "20"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sku"].required      = False  # auto-generated when left blank
        self.fields["material"].required = True
        for name in ("color_hex", "original_price", "sale_price", "stock",
                     "width_cm", "height_cm", "depth_cm", "closure_type", "compartments", "pattern"):
            self.fields[name].required = True

    def clean_color(self):
        return (self.cleaned_data.get("color") or "").strip()

    def clean_color_hex(self):
        value = (self.cleaned_data.get("color_hex") or "").strip()
        if not value:
            raise ValidationError("Please pick a colour.")
        if not HEX_COLOR_RE.match(value):
            raise ValidationError("Enter a valid hex colour code (e.g. #D8C3A5).")
        return value.upper()

    def clean_original_price(self):
        price = self.cleaned_data.get("original_price")
        if price is None:
            raise ValidationError("MRP is required.")
        if price <= 0:
            raise ValidationError("MRP must be greater than 0.")
        if price > Decimal("1000000"):
            raise ValidationError("MRP cannot exceed ₹10,00,000.")
        return price

    def clean_sale_price(self):
        price = self.cleaned_data.get("sale_price")
        if price is None:
            raise ValidationError("Sale price is required.")
        if price <= 0:
            raise ValidationError("Sale price must be greater than 0.")
        return price

    def clean_stock(self):
        stock = self.cleaned_data.get("stock")
        if stock is None:
            raise ValidationError("Stock quantity is required.")
        if stock > 9999:
            raise ValidationError("Stock cannot exceed 9999.")
        return stock

    def _clean_dimension(self, field, label):
        value = self.cleaned_data.get(field)
        if value is None:
            raise ValidationError(f"{label} is required.")
        if value <= 0:
            raise ValidationError(f"{label} must be greater than 0.")
        if value > 100:
            raise ValidationError(f"{label} cannot exceed 100 cm.")
        return value

    def clean_width_cm(self):
        return self._clean_dimension("width_cm", "Width")

    def clean_height_cm(self):
        return self._clean_dimension("height_cm", "Height")

    def clean_depth_cm(self):
        return self._clean_dimension("depth_cm", "Depth")

    def clean_compartments(self):
        value = self.cleaned_data.get("compartments")
        if value is None:
            raise ValidationError("Number of compartments is required.")
        if value < 1 or value > 20:
            raise ValidationError("Compartments must be between 1 and 20.")
        return value

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if sku:
            qs = ProductVariant.objects.filter(sku__iexact=sku)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("This SKU is already in use.")
        return sku

    def clean(self):
        cleaned = super().clean()
        op = cleaned.get("original_price")
        sp = cleaned.get("sale_price")
        if op and sp and sp > op:
            self.add_error("sale_price", "Sale price cannot be greater than the MRP.")
        return cleaned
