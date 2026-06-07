
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.http import HttpResponseRedirect
from django.views.decorators.cache import never_cache
from django.urls import reverse
from .decorators import admin_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q
from .forms import SetNewPasswordForm
from .models import CustomUser, Category
from .utils import (
    OTP_EXPIRY_MINUTES,
    check_resend_cooldown,
    clear_pending_user_session,
    create_otp_for_user,
    get_pending_user,
    send_otp_email,
    set_pending_user_session,
    verify_otp,
)

logger = logging.getLogger(__name__)


# ADMIN LOGIN / LOGOUT

@never_cache
def admin_login_view(request):

    if request.session.get("_is_admin") and request.session.get("_admin_user_id"):
        return redirect("admin_dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")

        user = authenticate(request, email=email, password=password)

        if user is not None and user.is_staff:
            request.session.cycle_key()
            request.session["_is_admin"] = True
            request.session["_admin_user_id"] = str(user.id)
            request.session["_admin_pw_hash"] = user.password
            request.session.modified = True
            logger.info(f"[ADMIN LOGIN] Staff user logged in: {user.email}")
            messages.success(request, "Welcome to the Luxelle Admin Panel.")
            return redirect("admin_dashboard")
        else:
            messages.error(request, "Invalid admin credentials.")

    return render(request, "admin_panel/login.html")


@admin_required
@never_cache
def admin_logout_view(request):

    request.session.pop('_is_admin', None)
    request.session.pop('_admin_user_id', None)
    request.session.modified = True
    messages.success(request, "You have been logged out of the Admin Panel.")
    return redirect("admin_login")



# ADMIN DASHBOARD

@admin_required
def admin_dashboard_view(request):
    from django.utils import timezone

    non_superusers = CustomUser.objects.filter(is_staff=False)
    first_of_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    context = {
        "total_users":       non_superusers.count(),
        "active_count":      non_superusers.filter(is_active=True).count(),
        "blocked_count":     non_superusers.filter(is_active=False).count(),
        "this_month_count":  non_superusers.filter(date_joined__gte=first_of_month).count(),
        "recent_users":      non_superusers.order_by("-date_joined")[:5],
    }
    return render(request, "admin_panel/dashboard.html", context)


# USER MANAGEMENT

@admin_required
def admin_user_management_view(request):
        from django.utils import timezone
        search_query = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '')  

        from django.db.models import Q
        
        users_qs = CustomUser.objects.filter(is_staff=False).order_by('-date_joined')
        if search_query:
            users_qs = users_qs.filter(
                Q(email__icontains=search_query) |
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query)
            )
        if status_filter == 'active':
            users_qs = users_qs.filter(is_active=True)
        elif status_filter == 'blocked':
            users_qs = users_qs.filter(is_active=False)

        paginator = Paginator(users_qs,5)
        page_number = request.GET.get('page')
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        first_of_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        context = {
            'users': page_obj.object_list,
            'page_obj': page_obj,
            'is_paginated': page_obj.has_other_pages(),
            'search_query': search_query,
            'status_filter': status_filter,
            'active_count': users_qs.filter(is_active=True).count(),
            'blocked_count': users_qs.filter(is_active=False).count(),
            'this_month_count': users_qs.filter(date_joined__gte=first_of_month).count(),
        }
        return render(request, 'admin_panel/user_management.html', context)


@admin_required
def admin_user_profile_view(request):
    
    return render(request, "admin_panel/user_profile.html")


@admin_required
def admin_toggle_user_status_view(request, user_id):

    try:
        user        = CustomUser.objects.get(id=user_id, is_staff=False)
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        action = "unblocked" if user.is_active else "blocked"
        messages.success(request, f"User {user.get_full_name()} has been {action}.")
        admin_email = request.session.get('_admin_email', 'unknown')
        logger.info(f"[ADMIN] User {user.email} {action} by {admin_email}")
    except CustomUser.DoesNotExist:
        messages.error(request, "User not found.")

    return redirect("admin_users")




@admin_required
@never_cache
def admin_delete_user_view(request, user_id):
    if request.method not in ("POST", "GET"):
        return redirect('admin_users')
    try:
        user = CustomUser.objects.get(id=user_id, is_staff=False)
        user_name = user.get_full_name()
        user.delete()
        messages.success(request, f"User {user_name} has been permanently deleted.")
        admin_email = request.session.get('_admin_email', 'unknown')
        logger.info(f"[ADMIN] User {user_name} deleted by {admin_email}")
    except CustomUser.DoesNotExist:
        messages.error(request, "User not found.")
    return redirect('admin_users')


# ADMIN FORGOT PASSWORD - REQUEST OTP


def admin_forgot_password_view(request):
    logger.debug("admin_forgot_password_view called with method=%s", request.method)
    if request.method != "POST":
        return render(request, "admin_panel/forgot_password.html")
    email = request.POST.get("email", "").strip()
    if not email:
        messages.error(request, "Please provide an email address.")
        return render(request, "admin_panel/forgot_password.html")
    try:
        user = CustomUser.objects.get(email=email, is_staff=True)
    except CustomUser.DoesNotExist:
        messages.success(request, "If an admin account with that email exists, an OTP has been sent.")
        return render(request, "admin_panel/forgot_password.html")
    allowed, seconds_left = check_resend_cooldown(user, purpose="password_reset")
    if not allowed:
        messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new OTP.")
        return render(request, "admin_panel/forgot_password.html")
    otp_obj = create_otp_for_user(user, purpose="password_reset")
    email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")
    if not email_sent:
        messages.warning(request, "OTP generated, but email could not be sent. Proceeding with verification.")

    request.session.cycle_key()
    set_pending_user_session(request, user.id, purpose="password_reset")
    request.session["pending_otp"] = otp_obj.otp
    request.session["pending_otp_expires_at"] = (otp_obj.created_at + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()
    request.session["pending_otp_sent_at"] = otp_obj.created_at.isoformat()
    request.session.set_expiry(OTP_EXPIRY_MINUTES * 60)
    request.session.save()
    logger.debug("Redirecting to OTP page for user %s", user.email)
    return redirect('admin_forgot_password_otp')


# ADMIN FORGOT PASSWORD - VERIFY OTP

@never_cache
def admin_forgot_password_otp_view(request):

    user = get_pending_user(request)
    logger.debug("OTP view session data: %s", request.session.items())
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please request a new OTP.")
        return redirect("admin_forgot_password")

    if request.method == "POST":
        otp_input = request.POST.get("hiddenOtp", "").strip()
        if not otp_input:
            otp_input = request.POST.get("otp", "").strip()
        is_valid, msg = verify_otp(user, otp_input, purpose="password_reset")

        if is_valid:
            request.session["password_reset_verified"] = True
            request.session["password_reset_user_id"]  = str(user.id)
            clear_pending_user_session(request)
            messages.success(request, "OTP verified. Please enter your new password.")
            return redirect("admin_reset_password")
        else:
            messages.error(request, msg)

    return render(request, "admin_panel/otp.html", {"email": user.email})


# ADMIN FORGOT PASSWORD - RESEND OTP

@never_cache
def admin_resend_forgot_password_otp_view(request):

    user = get_pending_user(request)
    if not user or request.session.get("otp_purpose") != "password_reset":
        messages.error(request, "Session expired. Please request a new OTP.")
        return redirect("admin_forgot_password")

    allowed, seconds_left = check_resend_cooldown(user, purpose="password_reset")
    if not allowed:
        messages.warning(request, f"Please wait {seconds_left} seconds before requesting a new OTP.")
        return redirect("admin_forgot_password_otp")

    otp_obj    = create_otp_for_user(user, purpose="password_reset")
    email_sent = send_otp_email(user, otp_obj.otp, purpose="password_reset")

    if email_sent:
        messages.success(request, f"A new OTP has been sent to {user.email}.")
    else:
        messages.error(request, "Failed to resend OTP. Please try again.")

    return redirect("admin_forgot_password_otp")


# ADMIN FORGOT PASSWORD - RESET PASSWORD

@never_cache
def admin_reset_password_view(request):

    if not request.session.get("password_reset_verified"):
        messages.error(request, "Please verify OTP first.")
        return redirect("admin_forgot_password")

    user_id = request.session.get("password_reset_user_id")
    try:
        user = CustomUser.objects.get(pk=user_id, is_staff=True)
    except CustomUser.DoesNotExist:
        messages.error(request, "Session expired. Please start again.")
        return redirect("admin_forgot_password")

    form = SetNewPasswordForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user.set_password(form.cleaned_data["new_password"])
            user.save(update_fields=["password"])

            for key in (
                "password_reset_verified",
                "password_reset_user_id",
                "_is_admin",
                "_admin_user_id",
                "_admin_pw_hash",
            ):
                request.session.pop(key, None)
            request.session.cycle_key()

            messages.success(request, "Password updated successfully. Please log in with your new password.")
            return redirect("admin_login")

    return render(request, "admin_panel/reset_password.html", {"form": form})


#ADMIN CATEGORY MANAGEMENT

@admin_required
def admin_category_list_view(request):
    search_query = request.GET.get("search", "").strip()
    categories = Category.objects.filter(is_deleted=False)
    if search_query:
        categories = categories.filter(Q(name__icontains=search_query))
    categories = categories.order_by("-created_at")

    paginator   = Paginator(categories, 5)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    context = {
        "categories":   page_obj.object_list,
        "page_obj":     page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "search_query": search_query,
    }
    return render(request, "admin_panel/category_list.html", context)
    
