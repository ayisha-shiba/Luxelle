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
        
class Brand(models.Model):
    name        = models.CharField(max_length=100)
    is_listed   = models.BooleanField(default=True)
    is_deleted  = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(is_deleted=False),
                name="uniq_active_brand_name",
            ),
        ]

    def __str__(self):
        return self.name


class Material(models.Model):
    name       = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    GENDER_CHOICES = [
        ("men",    "Men"),
        ("women",  "Women"),
        ("unisex", "Unisex"),
    ]

    name              = models.CharField(max_length=200)
    slug              = models.SlugField(max_length=220, unique=True, blank=True)
    short_description = models.CharField(max_length=255, blank=True)
    description       = models.TextField(blank=True)
    category          = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand          = models.ForeignKey("Brand", on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    gender         = models.CharField(max_length=10, choices=GENDER_CHOICES, default="unisex")
    is_listed      = models.BooleanField(default=True)
    is_featured    = models.BooleanField(default=False)
    featured_at    = models.DateTimeField(null=True, blank=True)
    is_deal_of_day = models.BooleanField(default=False)
    is_deleted     = models.BooleanField(default=False)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

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

    @property
    def default_variant(self):
        return (self.variants.filter(is_default=True, is_deleted=False).first()
                or self.variants.filter(is_deleted=False).first())

    @property
    def display_variant(self):
        listed = self.variants.filter(is_deleted=False, is_listed=True)
        return listed.filter(is_default=True).first() or listed.first()


class ProductVariant(models.Model):
    SIZE_CHOICES = [
        ("small",  "Small"),
        ("medium", "Medium"),
        ("large",  "Large"),
    ]
    CLOSURE_CHOICES = [
        ("zipper",     "Zipper"),
        ("magnetic",   "Magnetic Snap"),
        ("drawstring", "Drawstring"),
        ("flap",       "Flap"),
        ("turnlock",   "Turn Lock"),
        ("opentop",    "Open Top"),
        ("buckle",     "Buckle"),
    ]
    PATTERN_CHOICES = [
        ("plain",   "Plain"),
        ("quilted", "Quilted"),
        ("printed", "Printed"),
    ]

    product        = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    variant_name   = models.CharField(max_length=200)
    sku            = models.CharField(max_length=50, unique=True)

    # Attributes
    color          = models.CharField(max_length=50, blank=True)
    color_hex      = models.CharField(max_length=7, blank=True)
    material       = models.ForeignKey("Material", on_delete=models.SET_NULL, related_name="variants", null=True, blank=True)
    size           = models.CharField(max_length=10, choices=SIZE_CHOICES, blank=True)

    # Dimensions (cm)
    width_cm       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    height_cm      = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    depth_cm       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Bag details
    closure_type   = models.CharField(max_length=20, choices=CLOSURE_CHOICES, blank=True)
    compartments   = models.PositiveIntegerField(null=True, blank=True)
    pattern        = models.CharField(max_length=20, choices=PATTERN_CHOICES, blank=True)

    # Pricing & stock
    original_price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_price     = models.DecimalField(max_digits=10, decimal_places=2)
    stock          = models.PositiveIntegerField(default=0)

    is_offer       = models.BooleanField(default=False)
    is_default     = models.BooleanField(default=False)
    is_listed      = models.BooleanField(default=True)
    is_deleted     = models.BooleanField(default=False)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "created_at"]

    def __str__(self):
        return self.variant_name

    def save(self, *args, **kwargs):
        if self.is_default:
            ProductVariant.objects.filter(
                product_id=self.product_id, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @property
    def discount_percent(self):
        if self.original_price and self.sale_price and self.sale_price < self.original_price:
            return round((self.original_price - self.sale_price) / self.original_price * 100)
        return 0


class VariantImage(models.Model):
    variant    = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="images")
    image      = models.ImageField(upload_to="products/")
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "created_at"]

    def __str__(self):
        return f"Image for {self.variant}"


class Review(models.Model):
    RATING_CHOICES = [
        (1, "1"),
        (2, "2"),
        (3, "3"),
        (4, "4"),
        (5, "5"),
    ]

    product    = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user       = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="reviews")
    rating     = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment    = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} rated {self.product} - {self.rating}"


class Wishlist(models.Model):
    user     = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="wishlist_items")
    product  = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")
    variant  = models.ForeignKey(
        "ProductVariant", on_delete=models.SET_NULL,
        related_name="wishlisted_by", null=True, blank=True,
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_at"]
        unique_together = ("user", "variant")

    def __str__(self):
        return f"{self.user} - {self.product}"

    @property
    def saved_variant(self):
        """The variant the user wishlisted, falling back to the display variant."""
        return self.variant or self.product.display_variant


class Cart(models.Model):
    user       = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name="cart")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart of {self.user}"

    @property
    def total_price(self):
        return sum(item.total_price for item in self.items.all())

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def subtotal(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def total_discount(self):
        return sum(item.discount_amount for item in self.items.all())


class CartItem(models.Model):
    MAX_QUANTITY = 5

    cart     = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant  = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_at"]
        unique_together = ("cart", "variant")

    def __str__(self):
        return f"{self.quantity} x {self.variant}"

    @property
    def total_price(self):
        return self.variant.sale_price * self.quantity

    @property
    def subtotal(self):
        return self.variant.original_price * self.quantity

    @property
    def discount_amount(self):
        return self.subtotal - self.total_price
    
class Order(models.Model):
    STATUS_PENDING      ="pending"
    STATUS_SHIPPED      ="shipped"
    STATUS_OUT_FOR_DELIVERY="out_for_delivery"
    STATUS_DELIVERED     ="delivered"
    STATUS_CANCELLED     ="cancelled"
    STATUS_RETURNED     ="returned"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SHIPPED, "Shipped"),
        (STATUS_OUT_FOR_DELIVERY,"Out for Delivery"),
        (STATUS_DELIVERED,"Delivered"),
        (STATUS_CANCELLED,"Cancelled"),
        (STATUS_RETURNED,"Returned"),

    ]

    PAYMENT_COD = "COD"
    PAYMENT_RAZORPAY = "razorpay"
    PAYMENT_WALLET = "wallet"
    PAYMENT_CHOICES = [
        (PAYMENT_COD, "Cash on Delivery"),
        (PAYMENT_RAZORPAY, "Razorpay (Online)"),
        (PAYMENT_WALLET, "Wallet"),
    ]

    # Which statuses an order may move to NEXT, from each current status.
    # Terminal states (delivered / cancelled / returned) have no further moves.
    ALLOWED_TRANSITIONS = {
        STATUS_PENDING:          [STATUS_SHIPPED, STATUS_CANCELLED],
        STATUS_SHIPPED:          [STATUS_OUT_FOR_DELIVERY, STATUS_CANCELLED],
        STATUS_OUT_FOR_DELIVERY: [STATUS_DELIVERED, STATUS_CANCELLED],
        STATUS_DELIVERED:        [],
        STATUS_CANCELLED:        [],
        STATUS_RETURNED:         [],
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4,editable=False)
    order_number = models.CharField(max_length=20,unique=True,editable=False,db_index=True)
    user = models.ForeignKey(CustomUser,on_delete=models.PROTECT,related_name="orders")

    #address 
    ship_full_name = models.CharField(max_length=100)
    ship_phone     = models.CharField(max_length=15)
    ship_address_line1 = models.CharField(max_length=255)
    ship_address_line2 = models.CharField(max_length=255, blank=True)
    ship_city          = models.CharField(max_length=100)
    ship_state         = models.CharField(max_length=100)
    ship_postal_code   = models.CharField(max_length=20)
    ship_country       = models.CharField(max_length=100, default="India")

    status = models.CharField(max_length=20,choices = STATUS_CHOICES, default = STATUS_PENDING,db_index=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES,default=PAYMENT_COD)

    #money
    subtotal = models.DecimalField(max_digits=10,decimal_places=2,default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total    = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    cancellation_reason = models.TextField(blank=True)
    return_reason       = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    def _generate_order_number(self):
        from django.utils.crypto import get_random_string
        date_part = timezone.now().strftime("%Y%m%d")
        while True:
            suffix = get_random_string(4, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
            number = f"ORD-{date_part}-{suffix}"
            if not Order.objects.filter(order_number=number).exists():
                return number

    def allowed_next_statuses(self):
        label_map = dict(self.STATUS_CHOICES)
        return [(value, label_map[value])
                for value in self.ALLOWED_TRANSITIONS.get(self.status, [])]

    @property
    def cgst(self):
        from .pricing import money
        return money(self.tax / 2) if self.tax else self.tax

    @property
    def sgst(self):
        from .pricing import money
        return money(self.tax / 2) if self.tax else self.tax

    @property
    def can_download_invoice(self):
        return self.items.filter(status=OrderItem.STATUS_DELIVERED).exists()

    def item_status_summary(self):
        labels = dict(OrderItem.STATUS_CHOICES)
        seen = []
        for s in self.items.values_list("status", flat=True):
            if s not in seen:
                seen.append(s)
        return [(s, labels.get(s, s.title())) for s in seen]

    def recalculate_totals(self, save=True):
        """Recompute money fields from billable items only and persist.

        Items are fulfilled independently, so cancelled / returned items must
        drop out of the subtotal, discount, GST and grand total. Call this from
        every place an item's status changes so all pages stay consistent.
        """
        from decimal import Decimal
        from . import pricing

        billable = [i for i in self.items.all() if i.is_billable]
        products_total = sum((Decimal(i.line_total) for i in billable), Decimal("0"))
        mrp_total      = sum((Decimal(i.original_price) * i.quantity for i in billable), Decimal("0"))

        totals = pricing.compute(products_total, mrp_total, self.shipping)
        self.subtotal = totals["subtotal"]
        self.discount = totals["discount"]
        self.tax      = totals["gst"]
        self.total    = totals["grand_total"]
        if save:
            self.save(update_fields=["subtotal", "discount", "tax", "total", "updated_at"])
        return totals

    @property
    def derived_status(self):
        """A single (css_value, label) for the order, derived from its items.

        The order-level ``status`` field isn't moved when items change one by
        one, so badges read from here instead to reflect the real state.
        """
        statuses = list(self.items.values_list("status", flat=True))
        if not statuses:
            return (self.status, self.get_status_display())

        labels = dict(OrderItem.STATUS_CHOICES)
        active = [s for s in statuses if s != OrderItem.STATUS_CANCELLED]

        if not active:                                               # all cancelled
            return ("cancelled", "Cancelled")
        if all(s == OrderItem.STATUS_RETURNED for s in active):
            return ("returned", "Returned")
        if all(s == OrderItem.STATUS_DELIVERED for s in active):
            return ("delivered", "Delivered")
        if len(set(active)) == 1:
            s = active[0]
            return (s, labels.get(s, s.title()))
        return ("mixed", "Processing")


class OrderItem(models.Model):
    STATUS_PENDING          = "pending"
    STATUS_CONFIRMED        = "confirmed"
    STATUS_PACKED           = "packed"
    STATUS_SHIPPED          = "shipped"
    STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
    STATUS_DELIVERED        = "delivered"
    STATUS_CANCELLED        = "cancelled"
    STATUS_RETURN_REQUESTED = "return_requested"
    STATUS_RETURN_APPROVED  = "return_approved"
    STATUS_RETURN_REJECTED  = "return_rejected"
    STATUS_PICKUP_SCHEDULED = "pickup_scheduled"
    STATUS_RETURN_PICKED    = "return_picked"
    STATUS_RETURNED         = "returned"
    STATUS_RETURN_REPAIR    = "return_repair"

    STATUS_CHOICES = [
        (STATUS_PENDING,          "Pending"),
        (STATUS_CONFIRMED,        "Confirmed"),
        (STATUS_PACKED,           "Packed"),
        (STATUS_SHIPPED,          "Shipped"),
        (STATUS_OUT_FOR_DELIVERY, "Out for Delivery"),
        (STATUS_DELIVERED,        "Delivered"),
        (STATUS_CANCELLED,        "Cancelled"),
        (STATUS_RETURN_REQUESTED, "Return Requested"),
        (STATUS_RETURN_APPROVED,  "Return Approved"),
        (STATUS_RETURN_REJECTED,  "Return Rejected"),
        (STATUS_PICKUP_SCHEDULED, "Pickup Scheduled"),
        (STATUS_RETURN_PICKED,    "Return Picked"),
        (STATUS_RETURNED,         "Returned"),
        (STATUS_RETURN_REPAIR,    "Sent for Repair"),
    ]

    RETURN_STATUSES = [
        STATUS_RETURN_REQUESTED, STATUS_RETURN_APPROVED, STATUS_RETURN_REJECTED,
        STATUS_PICKUP_SCHEDULED, STATUS_RETURN_PICKED, STATUS_RETURNED, STATUS_RETURN_REPAIR,
    ]

    RETURN_TRANSITIONS = {
        STATUS_RETURN_APPROVED:  [STATUS_PICKUP_SCHEDULED],
        STATUS_PICKUP_SCHEDULED: [STATUS_RETURN_PICKED],
        STATUS_RETURN_PICKED:    [STATUS_RETURNED, STATUS_RETURN_REPAIR],
    }
    RETURN_STEP_LABELS = {
        STATUS_PICKUP_SCHEDULED: "Pickup Scheduled",
        STATUS_RETURN_PICKED:    "Return Picked",
        STATUS_RETURNED:         "Returned (Move to Stock)",
        STATUS_RETURN_REPAIR:    "Returned (Send for Repair)",
    }

    ADMIN_TRANSITIONS = {
        STATUS_PENDING:          [STATUS_CONFIRMED, STATUS_CANCELLED],
        STATUS_CONFIRMED:        [STATUS_PACKED, STATUS_CANCELLED],
        STATUS_PACKED:           [STATUS_SHIPPED, STATUS_CANCELLED],
        STATUS_SHIPPED:          [STATUS_OUT_FOR_DELIVERY],
        STATUS_OUT_FOR_DELIVERY: [STATUS_DELIVERED],
        STATUS_DELIVERED:        [],
        STATUS_RETURN_REQUESTED: [],
        STATUS_RETURN_APPROVED:  [],
        STATUS_RETURN_REJECTED:  [],
        STATUS_CANCELLED:        [],
        STATUS_RETURNED:         [],
    }

    ACTIVE_STATUSES = [
        STATUS_PENDING, STATUS_CONFIRMED, STATUS_PACKED,
        STATUS_SHIPPED, STATUS_OUT_FOR_DELIVERY, STATUS_DELIVERED,
    ]
    CANCELLABLE_STATUSES = [
        STATUS_PENDING, STATUS_CONFIRMED, STATUS_PACKED,
    ]
    # Statuses where the customer is no longer being charged for the item:
    # cancelled before fulfilment, or any approved/completed return (refund due).
    # A *requested* or *rejected* return still counts — the customer keeps & pays.
    NON_BILLABLE_STATUSES = [
        STATUS_CANCELLED,
        STATUS_RETURN_APPROVED, STATUS_PICKUP_SCHEDULED,
        STATUS_RETURN_PICKED, STATUS_RETURNED, STATUS_RETURN_REPAIR,
    ]

    order   = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, related_name="order_items", null=True, blank=True)

    product_name = models.CharField(max_length=200)
    variant_name = models.CharField(max_length=200)
    sku          = models.CharField(max_length=50)
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # MRP snapshot for discount recalculation
    quantity     = models.PositiveIntegerField()
    line_total   = models.DecimalField(max_digits=10, decimal_places=2)

    status                  = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    cancellation_reason     = models.TextField(blank=True)
    return_reason           = models.TextField(blank=True)
    return_rejection_reason = models.TextField(blank=True)
    return_requested_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"

    @property
    def is_cancelled(self):        return self.status == self.STATUS_CANCELLED
    @property
    def is_returned(self):         return self.status == self.STATUS_RETURNED
    @property
    def is_return_requested(self): return self.status == self.STATUS_RETURN_REQUESTED
    @property
    def is_delivered(self):        return self.status == self.STATUS_DELIVERED

    @property
    def is_billable(self):
        """True when this item still contributes to the order's payable total."""
        return self.status not in self.NON_BILLABLE_STATUSES

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def can_cancel(self):
        return self.status in self.CANCELLABLE_STATUSES

    @property
    def can_return(self):
        return self.status == self.STATUS_DELIVERED

    @property
    def can_approve_return(self): return self.status == self.STATUS_RETURN_REQUESTED
    @property
    def can_decline_return(self): return self.status == self.STATUS_RETURN_REQUESTED

    def return_next_statuses(self):
        return [(v, self.RETURN_STEP_LABELS[v])
                for v in self.RETURN_TRANSITIONS.get(self.status, [])]

    @property
    def display_status(self):
        return self.get_status_display()

    def admin_next_statuses(self):
        label_map = dict(self.STATUS_CHOICES)
        return [(value, label_map[value])
                for value in self.ADMIN_TRANSITIONS.get(self.status, [])]


class OrderStatusEvent(models.Model):
    order      = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_events", null=True, blank=True)
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="status_events", null=True, blank=True)
    status     = models.CharField(max_length=20, choices=OrderItem.STATUS_CHOICES)
    note       = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.status} @ {self.created_at:%Y-%m-%d %H:%M}"



