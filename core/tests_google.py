import logging
from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch
from allauth.socialaccount.models import SocialApp, SocialLogin, SocialAccount
from core.models import CustomUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.auth.middleware import AuthenticationMiddleware

class GoogleLoginTest(TestCase):
    def setUp(self):
        SocialApp.objects.filter(provider='google').delete()
        self.app = SocialApp.objects.create(provider='google', name='Google', client_id='123', secret='456')
        self.app.sites.add(1)
        self.client = Client()

    @patch('allauth.socialaccount.providers.oauth2.views.OAuth2CallbackView.dispatch')
    def test_google_callback(self, mock_dispatch):
        from allauth.socialaccount.helpers import complete_social_login
        from allauth.socialaccount.adapter import get_adapter
        from allauth.account.models import EmailAddress

        from django.test import RequestFactory
        req = RequestFactory().get('/accounts/google/login/callback/')
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(req)
        req.session.save()
        middleware2 = AuthenticationMiddleware(lambda r: None)
        middleware2.process_request(req)
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(req, '_messages', FallbackStorage(req))
        req.allauth = __import__('types').SimpleNamespace()

        provider = get_adapter().get_provider(req, 'google')
        user = CustomUser(email='newgoogleuser@example.com', first_name='John', last_name='Doe')
        account = SocialAccount(provider='google', uid='google123', extra_data={'email':'newgoogleuser@example.com', 'given_name': 'John', 'family_name': 'Doe'})
        ea = EmailAddress(email='newgoogleuser@example.com', verified=True, primary=True)
        sociallogin = SocialLogin(account=account, user=user, provider=provider, email_addresses=[ea])
        sociallogin.state = {'process': 'login'}
        
        from allauth.core.context import request_context
        try:
            with request_context(req):
                resp = complete_social_login(req, sociallogin)
            print("TEST SUCCESS, STATUS:", resp.status_code, "URL:", resp.url)
        except Exception as e:
            import traceback
            traceback.print_exc()


