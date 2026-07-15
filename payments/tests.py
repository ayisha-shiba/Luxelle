from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from core.models import CustomUser, Address, Category, Product, ProductVariant, Cart, CartItem, Order, OrderItem
from payments.models import PendingRazorpayOrder, Payment


class RazorpayOrderFlowTest(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="testcustomer@example.com",
            password="customerpassword123",
            first_name="Test",
            last_name="Customer",
            is_active=True
        )
        self.client = Client()
        self.client.login(email="testcustomer@example.com", password="customerpassword123")

        # Set up address
        self.address = Address.objects.create(
            user=self.user,
            full_name="Test Customer",
            phone="9876543210",
            address_line1="123 Luxury St",
            city="Mumbai",
            state="Maharashtra",
            postal_code="400001",
            is_default=True
        )

        # Set up product & variant
        self.category = Category.objects.create(name="Handbags")
        self.product = Product.objects.create(name="Luxury Clutch", category=self.category)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            variant_name="Gold Clutch",
            sku="GOLD-CLUTCH",
            original_price=Decimal("5000.00"),
            sale_price=Decimal("4500.00"),
            stock=10,
            is_default=True
        )

        # Set up cart
        self.cart = Cart.objects.create(user=self.user)
        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            variant=self.variant,
            quantity=2
        )

    @patch("razorpay.Client")
    def test_checkout_redirects_and_stashes_data_for_razorpay(self, mock_razorpay_client):
        # Initial state checks
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(PendingRazorpayOrder.objects.count(), 0)

        # Submit checkout form
        response = self.client.post(
            reverse("checkout"),
            data={
                "address_id": str(self.address.id),
                "payment_method": "razorpay",
            }
        )

        # Should redirect to payment_start view with generated order number
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payments/start/ORD-", response.url)

        # Data should be in session
        session = self.client.session
        self.assertIn("pending_razorpay_checkout", session)
        checkout_data = session["pending_razorpay_checkout"]
        self.assertEqual(checkout_data["payment_method"], "razorpay")
        self.assertEqual(checkout_data["address"]["full_name"], "Test Customer")
        self.assertEqual(len(checkout_data["items"]), 1)
        self.assertEqual(checkout_data["items"][0]["variant_id"], self.variant.id)
        self.assertEqual(checkout_data["items"][0]["quantity"], 2)

        # No order created yet
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.variant.stock, 10) # stock NOT decremented

    @patch("razorpay.Client")
    def test_payment_start_creates_pending_razorpay_order(self, mock_razorpay_client):
        # Mock Razorpay Client response
        instance = mock_razorpay_client.return_value
        instance.order.create.return_value = {"id": "rzp_order_test_999"}

        # Stash checkout data in session first
        session = self.client.session
        order_number = "ORD-20260702-WXYZ"
        checkout_data = {
            "order_number": order_number,
            "address": {
                "full_name": "Test Customer",
                "phone": "9876543210",
                "address_line1": "123 Luxury St",
                "address_line2": "",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
                "country": "India",
            },
            "coupon_id": None,
            "payment_method": "razorpay",
            "items": [{"variant_id": self.variant.id, "quantity": 2}],
            "total_amount": "9000.00"
        }
        session["pending_razorpay_checkout"] = checkout_data
        session.save()

        # Hit payment_start view
        response = self.client.get(reverse("payment_start", args=[order_number]))
        self.assertEqual(response.status_code, 200)

        # Verify PendingRazorpayOrder was created
        pending = PendingRazorpayOrder.objects.filter(razorpay_order_id="rzp_order_test_999").first()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.user, self.user)
        self.assertEqual(pending.amount, Decimal("9000.00"))
        self.assertEqual(pending.checkout_data["order_number"], order_number)

        # Verify context passed to payment page
        self.assertIn("razorpay_order_id", response.context)
        self.assertEqual(response.context["razorpay_order_id"], "rzp_order_test_999")
        self.assertEqual(response.context["amount_paise"], 900000)

    @patch("razorpay.Client")
    def test_payment_callback_success_creates_order(self, mock_razorpay_client):
        # Stash pending order in DB
        order_number = "ORD-20260702-WXYZ"
        checkout_data = {
            "order_number": order_number,
            "address": {
                "full_name": "Test Customer",
                "phone": "9876543210",
                "address_line1": "123 Luxury St",
                "address_line2": "",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
                "country": "India",
            },
            "coupon_id": None,
            "payment_method": "razorpay",
            "items": [{"variant_id": self.variant.id, "quantity": 2}],
            "total_amount": "10620.00"
        }
        pending = PendingRazorpayOrder.objects.create(
            razorpay_order_id="rzp_order_test_999",
            user=self.user,
            checkout_data=checkout_data,
            amount=Decimal("10620.00")
        )

        # Mock utility.verify_payment_signature to pass without exception
        instance = mock_razorpay_client.return_value
        instance.utility.verify_payment_signature.return_value = True

        # Post callback response
        response = self.client.post(
            reverse("payment_callback"),
            data={
                "razorpay_order_id": "rzp_order_test_999",
                "razorpay_payment_id": "pay_test_123",
                "razorpay_signature": "sig_test_456"
            }
        )

        # Redirect to success
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("order_success", args=[order_number]))

        # Check Order in DB
        order = Order.objects.filter(order_number=order_number).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.total, Decimal("10620.00"))
        self.assertEqual(order.ship_full_name, "Test Customer")

        # Check OrderItem
        self.assertEqual(order.items.count(), 1)
        item = order.items.first()
        self.assertEqual(item.variant, self.variant)
        self.assertEqual(item.quantity, 2)

        # Verify stock decremented
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 8)

        # Verify cart cleared
        self.assertEqual(self.cart.items.count(), 0)

        # Verify Payment record created
        payment = Payment.objects.filter(razorpay_order_id="rzp_order_test_999").first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.razorpay_payment_id, "pay_test_123")

        # Verify PendingRazorpayOrder deleted
        self.assertFalse(PendingRazorpayOrder.objects.filter(razorpay_order_id="rzp_order_test_999").exists())

    @patch("razorpay.Client")
    def test_payment_callback_signature_failure(self, mock_razorpay_client):
        # Stash pending order in DB
        order_number = "ORD-20260702-WXYZ"
        checkout_data = {
            "order_number": order_number,
            "address": {
                "full_name": "Test Customer",
                "phone": "9876543210",
                "address_line1": "123 Luxury St",
                "address_line2": "",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
                "country": "India",
            },
            "coupon_id": None,
            "payment_method": "razorpay",
            "items": [{"variant_id": self.variant.id, "quantity": 2}],
            "total_amount": "10620.00"
        }
        PendingRazorpayOrder.objects.create(
            razorpay_order_id="rzp_order_test_999",
            user=self.user,
            checkout_data=checkout_data,
            amount=Decimal("10620.00")
        )

        # Mock verify_payment_signature to raise SignatureVerificationError
        import razorpay.errors
        instance = mock_razorpay_client.return_value
        instance.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError()

        # Post callback response
        response = self.client.post(
            reverse("payment_callback"),
            data={
                "razorpay_order_id": "rzp_order_test_999",
                "razorpay_payment_id": "pay_test_123",
                "razorpay_signature": "sig_test_456"
            }
        )

        # Should redirect to failure page
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("payment_failure", args=[order_number]), target_status_code=400)

        # NO Order or Payment PAID record should be created
        self.assertEqual(Order.objects.count(), 0)
        self.assertFalse(Payment.objects.filter(status=Payment.STATUS_PAID).exists())

        # Cart should NOT be cleared, stock should NOT be decremented
        self.assertEqual(self.cart.items.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 10)
