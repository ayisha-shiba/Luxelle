import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()
from core.models import CustomUser
email = 'ayishashiba.p@gmail.com'
password = 'Ayisha@123'
user, created = CustomUser.objects.get_or_create(email=email)
user.is_active = True
user.is_staff = True
user.is_superuser = True
user.set_password(password)
user.save()
print('Admin user created' if created else 'Admin user updated')
