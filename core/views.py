from django.shortcuts import render

def login_view(request):
    return render(request, 'login.html')

def register_view(request):
    return render(request, 'register.html')

def home_view(request):
    return render(request, 'home.html')

def profile_view(request):
    return render(request, 'profile.html')

def profile_edit_view(request):
    return render(request, 'profile_edit.html')

def addresses_view(request):
    return render(request, 'addresses.html')

def verify_otp_view(request):
    return render(request, 'verify_otp.html')

def forgot_password_view(request):
    return render(request, 'forgot_password.html')

def forgot_password_otp_view(request):
    return render(request, 'forgot_password_otp.html')

def set_new_password_view(request):
    return render(request, 'set_new_password.html')



