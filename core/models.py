import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.text import slugify 


# Custom User Manager

class CustomUserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required.")
        email = self.normalize_email(email)
        extra_fields.setdefault("is_active", False)  
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


# Custom User Model

class CustomUser(AbstractBaseUser, PermissionsMixin):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email      = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=50, blank=True)
    last_name  = models.CharField(max_length=50, blank=True)
    full_name  = models.CharField(max_length=150, blank=True, default="")
    phone      = models.CharField(max_length=15, blank=True)

    is_active   = models.BooleanField(default=False)   
    is_staff    = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)   

    date_joined = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name        = "User"
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


# OTP Model

class OTPVerification(models.Model):

    RESEND_COOLDOWN_SECONDS = 60

    PURPOSE_CHOICES = [
        ("registration",   "Registration"),
        ("password_reset", "Password Reset"),
        ("email_change",   "Email Change"),
        ("account_delete", "Account Deletion"),
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
        
        return not self.is_used and timezone.now() < self.expires_at

    def seconds_until_resend_allowed(self):
    
        elapsed   = (timezone.now() - self.created_at).total_seconds()
        remaining = self.RESEND_COOLDOWN_SECONDS - elapsed
        return max(0, int(remaining))

    def __str__(self):
        return f"OTP({self.user.email} | {self.purpose})"


# User Profile Model

class UserProfile(models.Model):
    GENDER_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
        ("",  "Prefer not to say"),
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
        if self.avatar:
            return self.avatar.url
        return "/static/images/default_avatar.png"


# Address Model

class Address(models.Model):

    ADDRESS_TYPE_CHOICES = [
        ("home",  "Home"),
        ("work",  "Work"),
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
        ordering            = ["-is_default", "-created_at"]
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.full_name} - {self.city}, {self.state}"

    def save(self, *args, **kwargs):
        if self.is_default:
            Address.objects.filter(
                user=self.user, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

class Category(models.Model):
    name        = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    image       = models.ImageField(upload_to="categories/", blank=True, null=True)
    is_listed   = models.BooleanField(default=True)
    is_deleted  = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["-created_at"]
        verbose_name_plural = "Categories"
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(is_deleted=False),
                name="uniq_active_category_name",
            ),
        ]

    def __str__(self):
        return self.name

    def image_url(self):
        if self.image:
            return self.image.url
        return "/static/image/default_category.svg"
        
class Product(models.Model):
    name        = models.CharField(max_length=200)
    slug        = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    category    = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand       = models.CharField(max_length=100, blank=True)
    price       = models.DecimalField(max_digits=10, decimal_places=2)
    stock       = models.PositiveIntegerField(default=0)
    is_listed   = models.BooleanField(default=True)
    is_deleted  = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)


    class Meta:
        ordering= ["-created_at"]

    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

class ProductImage(models.Model):
    product     = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image       = models.ImageField(upload_to="products/")
    is_primary  = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "created_at"]

    def __str__(self):
        return f"Image for {self.product.name}"
    


