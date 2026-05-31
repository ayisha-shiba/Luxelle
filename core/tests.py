from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

class RegistrationFlowTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.register_url = reverse('register')
        self.verify_url = reverse('verify_otp')
        self.user_model = get_user_model()

    def test_registration_and_otp_redirection(self):
        # Submit registration form
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'testuser@example.com',
            'phone': '+1234567890',
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

    def test_change_email_link_redirects_to_register(self):
        # First create a pending user via registration flow
        form_data = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'testchange@example.com',
            'phone': '+1234567890',
            'password1': 'StrongPass!123',
            'password2': 'StrongPass!123',
        }
        self.client.post(self.register_url, data=form_data)
        # Now get OTP page and check the link
        response = self.client.get(self.verify_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.register_url}"')
