<<<<<<< HEAD
import datetime
from django.test import TestCase, Client
from django.urls import reverse
from core.models import CustomUser
=======
from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from .forms import RegistrationForm
>>>>>>> feat/email-ver

class AdminForgotPasswordFlowTest(TestCase):
    def setUp(self):
        self.admin_email = 'admin@test.com'
        self.admin_password = 'admin123'
        self.admin = CustomUser.objects.create_superuser(email=self.admin_email, password=self.admin_password)

<<<<<<< HEAD
    def test_forgot_password_flow(self):
        client = Client()
        # Post email to request OTP
        response = client.post(reverse('admin_forgot_password'), {'email': self.admin_email}, follow=True)
        # Should redirect to OTP page
        self.assertRedirects(response, reverse('admin_forgot_password_otp'))
        # Session should have pending_user_id and otp_purpose
        session = client.session
        self.assertIn('pending_user_id', session)
        self.assertEqual(session.get('otp_purpose'), 'password_reset')
        # Access OTP page GET
        otp_page = client.get(reverse('admin_forgot_password_otp'))
        self.assertEqual(otp_page.status_code, 200)
        self.assertTemplateUsed(otp_page, 'admin_panel/otp.html')
=======
    def test_registration_and_otp_redirection(self):
    
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'testuser@example.com',
            'phone': '+91 98765 43210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        response = self.client.post(self.register_url, data=form_data)
        # Should redirect to OTP verification page
        self.assertRedirects(response, self.verify_url)

        # Follow redirect to OTP page
        response = self.client.get(self.verify_url)
        self.assertEqual(response.status_code, 200)
        # The page should contain the CHANGE EMAIL link pointing to register
        self.assertContains(response, f'href="{self.register_url}"')

    def test_registration_form_rejects_invalid_phone(self):
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
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
            'first_name': 'Test',
            'last_name': 'User',
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
            'first_name': 'Test',
            'last_name': 'User',
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
        self.assertContains(response, 'OTP sent successfully.')

    def test_verify_otp_invalid_preserves_resend_timer_state(self):
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
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
            'first_name': 'Test',
            'last_name': 'User',
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
        # First create a pending user via registration flow
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'testchange@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        self.client.post(self.register_url, data=form_data)
        # Now get OTP page and check the link
        response = self.client.get(self.verify_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.register_url}"')

    def test_resend_otp_after_otp_expires(self):
        """
        Critical test: Verify resend works AFTER OTP expires.
        Root cause bug fix: Decorator should NOT check OTP expiry on resend endpoint.
        Expected: User stays on verify_otp (NOT redirected to register).
        Bug scenario (before fix): Decorator checked OTP expiry and redirected to register.
        """
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'expiredotp@example.com',
            'phone': '9876543210',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        response = self.client.post(self.register_url, data=form_data)
        self.assertRedirects(response, self.verify_url)

        # Simulate OTP expiry AND cooldown expiry
        session = self.client.session
        past_time = (timezone.now() - timedelta(seconds=120)).isoformat()  # 120 sec ago (past both expiry and cooldown)
        session['pending_otp_expires_at'] = past_time
        session['pending_otp_sent_at'] = past_time
        session.save()

        # Call resend_otp - should NOT redirect to register despite expired OTP
        response = self.client.get(reverse('resend_otp'), follow=True)
        
        # PRIMARY TEST: User stayed on verify_otp, NOT redirected to register
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], self.verify_url)
        
        # SECONDARY TEST: Session data is preserved (email, phone intact)
        self.assertEqual(self.client.session.get('pending_registration')['email'], 'expiredotp@example.com')
        self.assertEqual(self.client.session.get('pending_registration')['phone'], '9876543210')
        
        # TERTIARY TEST: New OTP was generated and stored
        new_otp = self.client.session.get('pending_otp')
        self.assertIsNotNone(new_otp)
        
        # FINAL TEST: Success message shown (confirms resend succeeded, not blocked by cooldown warning)
        self.assertContains(response, 'OTP sent successfully.')
>>>>>>> feat/email-ver
