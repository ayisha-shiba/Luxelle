import os
import django
import sys
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.contrib.auth import get_user_model

def ensure_admin_user(email='ayishashiba.p@gmail.com', password='Ayisha@123'):
    User = get_user_model()
    try:
        user = User.objects.get(email=email)
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save(update_fields=['password', 'is_staff', 'is_superuser', 'is_active'])
        print('Admin user updated.')
    except User.DoesNotExist:
        user = User.objects.create_user(
            email=email,
            password=password,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        print('Admin user created.')

if __name__ == '__main__':
    ensure_admin_user()
