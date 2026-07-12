from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import (
    Brand,
    Category,
    CustomUser,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Review,
)
from wallet.models import Wallet
from offers.models import ReferralProfile
from .forms import RegistrationForm


class AdminForgotPasswordFlowTest(TestCase):
    def setUp(self):
        self.admin_email = 'admin@test.com'
        self.admin_password = 'admin123'
        self.admin = CustomUser.objects.create_superuser(email=self.admin_email, password=self.admin_password)

    def test_forgot_password_flow(self):
        client = Client()
        response = client.post(reverse('admin_forgot_password'), {'email': self.admin_email}, follow=True)
        self.assertRedirects(response, reverse('admin_forgot_password_otp'))
        session = client.session
        self.assertIn('pending_user_id', session)
        self.assertEqual(session.get('otp_purpose'), 'password_reset')
        otp_page = client.get(reverse('admin_forgot_password_otp'))
        self.assertEqual(otp_page.status_code, 200)
        self.assertTemplateUsed(otp_page, 'admin_panel/otp.html')


class RegistrationFlowTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.register_url = reverse('register')
        self.verify_url = reverse('verify_otp')
        self.user_model = get_user_model()

    def test_registration_and_otp_redirection(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'testuser@example.com',
            'phone': '+91 98765 43210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        response = self.client.post(self.register_url, data=form_data)
        self.assertRedirects(response, self.verify_url)

        response = self.client.get(self.verify_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.register_url}"')

    def test_registration_form_rejects_invalid_phone(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'invalidphone@example.com',
            'phone': '123-abc-4567',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        form = RegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
        self.assertEqual(form.errors['phone'][0], 'Enter a valid Indian mobile number using digits, spaces, hyphens, parentheses, or a leading +91.')

    def test_registration_form_accepts_formatted_phone_and_normalizes_it(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'validphone@example.com',
            'phone': '+91 98765-43210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        form = RegistrationForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['phone'], '9876543210')

    def test_resend_otp_keeps_user_on_verify_page_and_preserves_session(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'resendtest@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        response = self.client.post(self.register_url, data=form_data)
        self.assertRedirects(response, self.verify_url)

        session = self.client.session
        session['pending_otp_sent_at'] = (timezone.now() - timedelta(seconds=61)).isoformat()
        session.save()

        response = self.client.get(reverse('resend_otp'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], self.verify_url)
        self.assertEqual(self.client.session.get('pending_registration')['email'], 'resendtest@example.com')
        self.assertEqual(self.client.session.get('pending_registration')['phone'], '9876543210')
        self.assertIsNotNone(self.client.session.get('pending_otp'))
        self.assertContains(response, 'OTP resent successfully.')

    def test_verify_otp_invalid_preserves_resend_timer_state(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'timerstate@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        response = self.client.post(self.register_url, data=form_data)
        self.assertRedirects(response, self.verify_url)

        session = self.client.session
        session['pending_otp_sent_at'] = (timezone.now() - timedelta(seconds=30)).isoformat()
        session.save()

        response = self.client.post(self.verify_url, data={'otp': '000000'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="timerSec">30</strong>')
        self.assertContains(response, 'Resend code in')

    def test_registration_form_rejects_invalid_mobile_prefix(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'invalidprefix@example.com',
            'phone': '1234567899',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        form = RegistrationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
        self.assertEqual(form.errors['phone'][0], 'Enter a valid Indian mobile number starting with 6, 7, 8, or 9.')

    def test_change_email_link_redirects_to_register(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'testchange@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        self.client.post(self.register_url, data=form_data)
        response = self.client.get(self.verify_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.register_url}"')

    @patch('offers.services.apply_referral_code')
    def test_referral_signup_failure_rolls_back_user(self, mock_apply_referral_code):
        mock_apply_referral_code.side_effect = ValueError('Simulated referral failure')

        referrer = self.user_model.objects.create_user(
            email='referrer@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        ReferralProfile.objects.create(user=referrer, code='REFCODE')

        form_data = {
            'full_name': 'Referred User',
            'email': 'referred@example.com',
            'phone': '9876543211',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
            'referral_code': 'REFCODE',
        }
        response = self.client.post(self.register_url, data=form_data)
        self.assertRedirects(response, self.verify_url)

        otp = self.client.session['pending_otp']
        response = self.client.post(self.verify_url, data={'otp': otp}, follow=True)

        self.assertRedirects(response, self.register_url)
        self.assertFalse(self.user_model.objects.filter(email='referred@example.com').exists())
        self.assertFalse(Wallet.objects.filter(user__email='referred@example.com').exists())

    def test_resend_otp_after_otp_expires(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'expiredotp@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        response = self.client.post(self.register_url, data=form_data)
        self.assertRedirects(response, self.verify_url)

        session = self.client.session
        past_time = (timezone.now() - timedelta(seconds=120)).isoformat()
        session['pending_otp_expires_at'] = past_time
        session['pending_otp_sent_at'] = past_time
        session.save()

        response = self.client.get(reverse('resend_otp'), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], self.verify_url)

        self.assertEqual(self.client.session.get('pending_registration')['email'], 'expiredotp@example.com')
        self.assertEqual(self.client.session.get('pending_registration')['phone'], '9876543210')

        new_otp = self.client.session.get('pending_otp')
        self.assertIsNotNone(new_otp)

        self.assertContains(response, 'OTP resent successfully.')

    def test_otp_expiry_enforcement_registration(self):
        form_data = {
            'full_name': 'Test User',
            'email': 'expirycheck@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        self.client.post(self.register_url, data=form_data)
        otp = self.client.session['pending_otp']
        
        # 1. Wrong OTP within 1 minute -> "Invalid OTP."
        response = self.client.post(self.verify_url, data={'otp': '000000'})
        self.assertContains(response, 'Invalid OTP. Please try again.')
        
        # 2. Correct OTP after 1 minute -> "OTP has expired. Please request a new OTP."
        session = self.client.session
        past_time = (timezone.now() - timedelta(seconds=61)).isoformat()
        session['pending_otp_expires_at'] = past_time
        session.save()
        
        response = self.client.post(self.verify_url, data={'otp': otp})
        self.assertContains(response, 'OTP has expired. Please request a new OTP.')
        
        # 3. Wrong OTP after 1 minute -> "OTP has expired. Please request a new OTP."
        response = self.client.post(self.verify_url, data={'otp': '000000'})
        self.assertContains(response, 'OTP has expired. Please request a new OTP.')

    def test_write_review_allowed_for_item_delivered_order(self):
        user = get_user_model().objects.create_user(
            email='buyer@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        category = Category.objects.create(name='Bags')
        brand = Brand.objects.create(name='Test Brand')
        product = Product.objects.create(
            name='Test Product',
            category=category,
            brand=brand,
            short_description='Test',
            description='Test product',
        )
        variant = ProductVariant.objects.create(
            product=product,
            variant_name='Default',
            sku='TEST-SKU',
            original_price=100,
            sale_price=100,
            stock=10,
        )
        order = Order.objects.create(
            user=user,
            ship_full_name='Buyer',
            ship_phone='9876543210',
            ship_address_line1='123 Test St',
            ship_city='Test City',
            ship_state='Test State',
            ship_postal_code='123456',
            ship_country='India',
            status=Order.STATUS_PENDING,
            payment_method=Order.PAYMENT_COD,
            subtotal=100,
            discount=0,
            shipping=0,
            tax=0,
            total=100,
        )
        OrderItem.objects.create(
            order=order,
            variant=variant,
            product_name=product.name,
            variant_name=variant.variant_name,
            sku=variant.sku,
            unit_price=100,
            original_price=100,
            quantity=1,
            line_total=100,
            status=OrderItem.STATUS_DELIVERED,
        )

        client = Client()
        client.force_login(user)
        response = client.get(reverse('write_review', args=[product.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'write_review.html')
        self.assertNotContains(response, 'You can only review products you have purchased and received.')


class ReviewValidationTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            email='reviewer@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        self.category = Category.objects.create(name='Bags')
        self.brand = Brand.objects.create(name='Test Brand')
        self.product = Product.objects.create(
            name='Test Product',
            category=self.category,
            brand=self.brand,
            short_description='Test',
            description='Test product',
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            variant_name='Default',
            sku='TEST-SKU-2',
            original_price=100,
            sale_price=100,
            stock=10,
        )
        self.order = Order.objects.create(
            user=self.user,
            ship_full_name='Reviewer',
            ship_phone='9876543210',
            ship_address_line1='123 Test St',
            ship_city='Test City',
            ship_state='Test State',
            ship_postal_code='123456',
            ship_country='India',
            status=Order.STATUS_PENDING,
            payment_method=Order.PAYMENT_COD,
            subtotal=100,
            discount=0,
            shipping=0,
            tax=0,
            total=100,
        )
        OrderItem.objects.create(
            order=self.order,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.variant_name,
            sku=self.variant.sku,
            unit_price=100,
            original_price=100,
            quantity=1,
            line_total=100,
            status=OrderItem.STATUS_DELIVERED,
        )
        self.client.force_login(self.user)

    def _post_review(self, title, comment, rating='5'):
        return self.client.post(
            reverse('write_review', args=[self.product.id]),
            data={'rating': rating, 'title': title, 'comment': comment},
            follow=True,
        )

    def test_review_rejects_only_special_characters(self):
        response = self._post_review('', '@@@@@@@@!!!!')
        self.assertContains(response, 'Review cannot contain only special characters.')
        self.assertContains(response, 'Write Review')

    def test_review_rejects_repeated_character_comment(self):
        response = self._post_review('', 'aaaaaaaaaa')
        self.assertContains(response, 'Review must contain valid text.')

    def test_review_rejects_whitespace_only_comment(self):
        response = self._post_review('', '          ')
        self.assertContains(response, 'Please enter a meaningful review.')

    def test_review_rejects_short_meaningless_comment(self):
        response = self._post_review('', '123456789')
        self.assertContains(response, 'Review must be at least 10 characters long.')

    def test_review_accepts_valid_comment(self):
        response = self._post_review('Nice bag', 'This bag is excellent and well made.')
        self.assertRedirects(response, reverse('product_detail', args=[self.product.slug]))

    def test_hidden_review_allows_new_submission(self):
        # Create a hidden review for this user and product
        Review.objects.create(
            user=self.user,
            product=self.product,
            rating=4,
            title='Hidden',
            comment='This was hidden by admin',
            is_hidden=True,
        )

        # Now posting a new visible review should create a new review and redirect
        response = self._post_review('New title', 'I really liked this product. Great quality!')
        self.assertRedirects(response, reverse('product_detail', args=[self.product.slug]))
        # Ensure there's one visible review for this user & product
        visible = Review.objects.filter(user=self.user, product=self.product, is_hidden=False)
        self.assertEqual(visible.count(), 1)


class ReferralStatsTest(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.client = Client()

    def test_profile_referral_stats_user_with_no_referrals(self):
        user = self.user_model.objects.create_user(
            email='noreferrals@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['referred_count'], 0)
        self.assertEqual(response.context['rewards_released'], 0)
        self.assertEqual(response.context['referral_earnings'], 0)

    def test_profile_referral_stats_referred_friend(self):
        from decimal import Decimal
        from wallet.models import WalletTransaction

        referrer = self.user_model.objects.create_user(
            email='referrer@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        friend = self.user_model.objects.create_user(
            email='friend@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )

        friend_profile = ReferralProfile.objects.create(
            user=friend,
            code=ReferralProfile.generate_code(),
            referred_by=referrer,
            reward_granted=True
        )

        wallet, _ = Wallet.objects.get_or_create(user=friend)
        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.CREDIT,
            amount=Decimal("100.00"),
            reason="Referral bonus for signing up",
            balance_after=Decimal("100.00")
        )

        self.client.force_login(friend)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['referred_count'], 0)
        self.assertEqual(response.context['rewards_released'], '₹100 (signup reward)')
        self.assertEqual(response.context['referral_earnings'], 100)

    def test_profile_referral_stats_referrer_one_successful(self):
        from decimal import Decimal
        from wallet.models import WalletTransaction

        referrer = self.user_model.objects.create_user(
            email='referrer2@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        friend = self.user_model.objects.create_user(
            email='friend2@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )

        referrer_profile = ReferralProfile.objects.create(
            user=referrer,
            code=ReferralProfile.generate_code()
        )

        friend_profile = ReferralProfile.objects.create(
            user=friend,
            code=ReferralProfile.generate_code(),
            referred_by=referrer,
            reward_granted=True
        )

        wallet, _ = Wallet.objects.get_or_create(user=referrer)
        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.CREDIT,
            amount=Decimal("200.00"),
            reason="Referral bonus for referring friend",
            balance_after=Decimal("200.00")
        )

        self.client.force_login(referrer)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['referred_count'], 1)
        self.assertEqual(response.context['rewards_released'], '₹200 per successful referral')
        self.assertEqual(response.context['referral_earnings'], 200)

    def test_profile_referral_stats_referrer_multiple_successful(self):
        from decimal import Decimal
        from wallet.models import WalletTransaction

        referrer = self.user_model.objects.create_user(
            email='referrer3@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        friend1 = self.user_model.objects.create_user(
            email='friend3a@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )
        friend2 = self.user_model.objects.create_user(
            email='friend3b@example.com',
            password='password123',
            is_active=True,
            is_verified=True,
        )

        referrer_profile = ReferralProfile.objects.create(
            user=referrer,
            code=ReferralProfile.generate_code()
        )

        ReferralProfile.objects.create(
            user=friend1,
            code=ReferralProfile.generate_code(),
            referred_by=referrer,
            reward_granted=True
        )
        ReferralProfile.objects.create(
            user=friend2,
            code=ReferralProfile.generate_code(),
            referred_by=referrer,
            reward_granted=True
        )

        wallet, _ = Wallet.objects.get_or_create(user=referrer)
        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.CREDIT,
            amount=Decimal("200.00"),
            reason="Referral bonus for friend1",
            balance_after=Decimal("200.00")
        )
        WalletTransaction.objects.create(
            wallet=wallet,
            txn_type=WalletTransaction.CREDIT,
            amount=Decimal("200.00"),
            reason="Referral bonus for friend2",
            balance_after=Decimal("400.00")
        )

        self.client.force_login(referrer)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['referred_count'], 2)
        self.assertEqual(response.context['rewards_released'], '₹200 per successful referral')
        self.assertEqual(response.context['referral_earnings'], 400)
