import datetime
from django.test import TestCase, Client
from django.urls import reverse
from core.models import CustomUser

class AdminForgotPasswordFlowTest(TestCase):
    def setUp(self):
        self.admin_email = 'admin@test.com'
        self.admin_password = 'admin123'
        self.admin = CustomUser.objects.create_superuser(email=self.admin_email, password=self.admin_password)

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
