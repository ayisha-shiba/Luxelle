import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from core.models import CustomUser

email = os.environ.get('ADMIN_EMAIL')
new_password = os.environ.get('ADMIN_PASSWORD')
if not email or not new_password:
    sys.exit('Set ADMIN_EMAIL and ADMIN_PASSWORD environment variables before running this script.')

try:
    admin_user = CustomUser.objects.get(email=email)
    admin_user.set_password(new_password)
    admin_user.is_active = True
    admin_user.is_staff = True
    admin_user.is_superuser = True
    admin_user.save(update_fields=['password', 'is_active', 'is_staff', 'is_superuser'])
    print('Admin password reset and flags ensured.')
except CustomUser.DoesNotExist:
    print('Admin user does not exist. Creating new admin.')
    admin_user = CustomUser.objects.create_user(email=email, password=new_password)
    admin_user.is_staff = True
    admin_user.is_superuser = True
    admin_user.is_active = True
    admin_user.save(update_fields=['is_staff', 'is_superuser', 'is_active'])
    print('Admin user created.')
