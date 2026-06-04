import os, django, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','myproject.settings')
import django
django.setup()
from django.test import Client
from core.models import CustomUser
from django.urls import reverse
admin_email='admin_test@example.com'
admin_password='adminpass123'
admin_user, created = CustomUser.objects.get_or_create(email=admin_email, defaults={'first_name':'Admin','is_superuser':True,'is_active':True,'is_verified':True})
if created:
    admin_user.set_password(admin_password)
    admin_user.save()
    print('Created admin user')
else:
    admin_user.set_password(admin_password)
    admin_user.save()
    print('Reset admin password')
client=Client()
logged_in=client.login(email=admin_email,password=admin_password)
print('login_success',logged_in)
url=reverse('admin_forgot_password')
resp=client.post(url,{'email':admin_email})
print('POST status',resp.status_code)
print('Redirect location',resp.get('Location'))
print('Session keys after post',list(client.session.keys()))
if resp.status_code in (301,302) and resp.get('Location'):
    otp_url=resp['Location']
    resp2=client.get(otp_url)
    print('OTP GET status',resp2.status_code)
    print('Session after get',list(client.session.keys()))
    otp_code=client.session.get('pending_otp')
    print('OTP code in session',otp_code)
    resp3=client.post(otp_url,{'otp':otp_code})
    print('OTP POST status',resp3.status_code)
    print('Redirect after OTP',resp3.get('Location'))
    print('Session final',list(client.session.keys()))
else:
    print('No redirect after forgot password')
