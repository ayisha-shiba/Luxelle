import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from core.models import CustomUser

email = os.environ.get('ADMIN_EMAIL')
password = os.environ.get('ADMIN_PASSWORD')
if not email or not password:
    sys.exit('Set ADMIN_EMAIL and ADMIN_PASSWORD environment variables before running this script.')

user, created = CustomUser.objects.get_or_create(email=email)
user.is_active = True
user.is_staff = True
user.is_superuser = True
user.set_password(password)
user.save()
print('Admin user created' if created else 'Admin user updated')
