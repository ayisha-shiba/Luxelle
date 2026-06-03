import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.messages.storage.fallback import FallbackStorage
from allauth.socialaccount.models import SocialLogin, SocialAccount, SocialApp
from core.models import CustomUser
from allauth.socialaccount.helpers import complete_social_login

try:
    req = RequestFactory().get('/accounts/google/login/callback/')
    req.session = SessionStore()
    req.session.save()
    setattr(req, 'session', req.session)
    messages = FallbackStorage(req)
    setattr(req, '_messages', messages)
    
    req.user = CustomUser()
    app = SocialApp.objects.first()
    acc = SocialAccount(provider='google', uid='testnew_uid', extra_data={'email':'testnew@example.com'})
    sl = SocialLogin(account=acc, user=CustomUser(email='testnew@example.com'))
    sl.state = {'process': 'login'}
    
    response = complete_social_login(req, sl)
    print("SUCCESS, Response URL:", response.url if hasattr(response, 'url') else response)
except Exception as e:
    import traceback
    traceback.print_exc()
