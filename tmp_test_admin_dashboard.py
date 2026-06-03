import os, sys, django
sys.path.append(r'c:/Users/shina/Downloads/project mock')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()
from django.test import Client
c = Client()
resp = c.get('/admin-panel/dashboard/')
print('status', resp.status_code)
print('content', resp.content[:200])
