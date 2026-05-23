"""
models.py — Luxelle Ecommerce (app: core)
Custom User, OTP, UserProfile, and Address models.
"""

import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────
# Custom User Manager
# ─────────────────────────────────────────────

class CustomUserManager(BaseUserManager):
    """Manager for CustomUser: email is the unique identifier."""

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required.")
        email = self.normalize_email(email)
        extra_fields.setdefault("is_active", False)  # Inactive until OTP verified
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_verified", True)
        return self.create_user(email, password, **extra_fields)


# ─────────────────────────────────────────────
# Custom User Model
# ─────────────────────────────────────────────

class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Main user model. Uses email as login.
    Replaces Django's default User model.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email      = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=50, blank=True)
    last_name  = models.CharField(max_length=50, blank=True)
    phone      = models.CharField(max_length=15, blank=True)

    is_active   = models.BooleanField(default=False)   # Activated after OTP
    is_staff    = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)   # Email verified

    date_joined = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        if created:
            UserProfile.objects.get_or_create(user=self)


# ─────────────────────────────────────────────
# OTP Model (Registration + Password Reset)
# ─────────────────────────────────────────────

class OTPVerification(models.Model):
    """Stores OTPs for email verification and password reset."""

    PURPOSE_CHOICES = [
        ("registration", "Registration"),
        ("password_reset", "Password Reset"),
    ]

    user       = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="otps")
    otp        = models.CharField(max_length=6)
    purpose    = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    is_used    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def is_valid(self):
        """Returns True if OTP is unused and not expired."""
        return not self.is_used and timezone.now() < self.expires_at

    def __str__(self):
        return f"OTP({self.user.email} | {self.purpose})"


# ─────────────────────────────────────────────
# User Profile Model
# ─────────────────────────────────────────────

class UserProfile(models.Model):
    """
    Extended profile info linked 1-to-1 with CustomUser.
    Created automatically via CustomUser.save() override.
    """
    GENDER_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
        ("", "Prefer not to say"),
    ]

    user          = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name="profile")
    avatar        = models.ImageField(upload_to="avatars/", blank=True, null=True)
    gender        = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    date_of_birth = models.DateField(blank=True, null=True)
    bio           = models.TextField(max_length=300, blank=True)
    updated_at    = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile({self.user.email})"

    def avatar_url(self):
        """Returns avatar URL or a default placeholder."""
        if self.avatar:
            return self.avatar.url
        return "/static/images/default_avatar.png"


# ─────────────────────────────────────────────
# Address Model
# ─────────────────────────────────────────────

class Address(models.Model):
    """Shipping/billing addresses for a user."""

    ADDRESS_TYPE_CHOICES = [
        ("home", "Home"),
        ("work", "Work"),
        ("other", "Other"),
    ]

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user          = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="addresses")
    full_name     = models.CharField(max_length=100)
    phone         = models.CharField(max_length=15)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city          = models.CharField(max_length=100)
    state         = models.CharField(max_length=100)
    postal_code   = models.CharField(max_length=20)
    country       = models.CharField(max_length=100, default="India")
    address_type  = models.CharField(max_length=10, choices=ADDRESS_TYPE_CHOICES, default="home")
    is_default    = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.full_name} — {self.city}, {self.state}"

    def save(self, *args, **kwargs):
        """Ensure only one address is default per user."""
        if self.is_default:
            Address.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)