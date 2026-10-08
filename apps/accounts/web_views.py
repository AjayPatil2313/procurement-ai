from datetime import timedelta
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from apps.accounts.utils import generate_password_reset_token, verify_password_reset_token
from apps.companies.models import Company, CompanyMember
from apps.billing.models import Subscription


def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.is_staff:
            return redirect("admin-panel-dashboard")
        return redirect("dashboard")

    next_url = request.GET.get("next") or request.POST.get("next") or ""
    login_type = request.POST.get("login_type") or request.GET.get("type") or "company"

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f"Welcome back, {user.first_name or user.email}!")
            if user.is_superuser or user.is_staff:
                request.session["active_company_id"] = None
                if not next_url or next_url in ["dashboard", "/dashboard/", "dashboard"]:
                    return redirect("admin-panel-dashboard")
                return redirect(next_url)
            else:
                return redirect(next_url or "dashboard")
        else:
            existing_user = User.objects.filter(email=email).first()
            if existing_user and existing_user.check_password(password) and not existing_user.is_active:
                messages.error(request, "This account is inactive. Please contact your company administrator.")
            else:
                messages.error(request, "Invalid email or password.")

    return render(request, "auth/login.html", {
        "next": next_url,
        "login_type": login_type,
    })


@transaction.atomic
def register_view(request):
    """
    Complete B2B Registration View:
    Creates User, Company, assigns Company Admin role (full 6-action authorization),
    and provisions trial search credits.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        # 1. Personal credentials
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        # 2. Company business details
        company_name = request.POST.get("company_name", "").strip()
        company_type = request.POST.get("company_type", "BOTH").strip().upper()
        legal_name = request.POST.get("legal_name", "").strip()
        registration_no = request.POST.get("registration_no", "").strip()
        gst_vat_no = request.POST.get("gst_vat_no", "").strip()
        industry = request.POST.get("industry", "").strip()
        company_size = request.POST.get("company_size", "11-50").strip()
        city = request.POST.get("city", "").strip()
        state = request.POST.get("state", "").strip()
        country = request.POST.get("country", "India").strip()
        address_line1 = request.POST.get("address_line1", "").strip()
        preferred_currency = request.POST.get("preferred_currency", "INR").strip().upper()

        # Validations
        errors = []
        if not first_name:
            errors.append("First name is required.")
        if not email:
            errors.append("Email address is required.")
        elif User.objects.filter(email=email).exists():
            errors.append("An account with this email address already exists. Please sign in.")

        if not password:
            errors.append("Password is required.")
        elif len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        elif password != confirm_password:
            errors.append("Passwords do not match.")

        if not company_name:
            errors.append("Company name is required.")

        if company_type not in ["BUYER", "SELLER", "BOTH"]:
            company_type = "BOTH"

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "auth/register.html", {
                "form_data": request.POST,
            })

        # 3. Create User
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            is_active=True,
            is_email_verified=True,
        )

        # 4. Create Company
        company = Company.objects.create(
            name=company_name,
            legal_name=legal_name or company_name,
            company_type=company_type,
            registration_no=registration_no,
            gst_vat_no=gst_vat_no,
            industry=industry or "Manufacturing",
            company_size=company_size or "11-50",
            city=city or "Mumbai",
            state=state or "Maharashtra",
            country=country or "India",
            address_line1=address_line1,
            preferred_currency=preferred_currency or "INR",
            is_active=True,
            is_verified=True,
            created_by=user,
        )

        # 5. Authorize User as Company Admin (grants all 6 RBAC permissions)
        CompanyMember.objects.create(
            company=company,
            user=user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 6. Initialize Subscription with 100 Search Credits
        Subscription.objects.create(
            company=company,
            plan=Subscription.Plan.PRO,
            credits_total=100,
            credits_used=0,
            valid_till=timezone.now().date() + timedelta(days=30),
        )

        # 7. Log in immediately
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session["active_company_id"] = company.id

        messages.success(
            request,
            f"Welcome to AI Procurement & Sales Platform, {user.first_name}! Your company workspace '{company.name}' is now active."
        )
        return redirect("dashboard")

    return render(request, "auth/register.html", {
        "form_data": {},
    })


def logout_view(request):
    user_name = ""
    if request.user.is_authenticated:
        user_name = request.user.get_full_name() or request.user.email
    logout(request)
    return render(request, "auth/logout.html", {
        "user_name": user_name,
    })


def forgot_password_view(request):
    """
    Renders forgot password form and sends password reset link to user's email.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    reset_url = None
    email_sent = False

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        if not email:
            messages.error(request, "Please enter your registered email address.")
            return render(request, "auth/forgot_password.html")

        user = User.objects.filter(email=email, is_active=True).first()

        if user:
            token = generate_password_reset_token(user)
            reset_url = request.build_absolute_uri(
                reverse("reset-password", kwargs={"token": token})
            )
            email_sent = True

            try:
                send_mail(
                    subject="Reset your Procurement AI password",
                    message=(
                        f"Hello {user.first_name or 'User'},\n\n"
                        f"We received a request to reset your password. Use the link below to set a new password:\n\n"
                        f"{reset_url}\n\n"
                        f"This link is valid for 1 hour.\n\n"
                        f"If you did not request this, please ignore this email."
                    ),
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@procurement.ai"),
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception:
                pass

            messages.success(
                request,
                f"Password reset link generated for {email}."
            )
        else:
            messages.info(
                request,
                f"If an active account exists for {email}, a reset link has been dispatched."
            )
            email_sent = True

        return render(request, "auth/forgot_password.html", {
            "email_sent": email_sent,
            "email": email,
            "reset_url": reset_url,
        })

    return render(request, "auth/forgot_password.html")


def reset_password_view(request, token):
    """
    Validates password reset token and allows the user to set a new password.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    # 1. Verify token
    try:
        data = verify_password_reset_token(token)
    except signing.SignatureExpired:
        messages.error(request, "This password reset link has expired (valid for 1 hour). Please request a new one.")
        return render(request, "auth/reset_password.html", {"token_error": True, "token": token})
    except signing.BadSignature:
        messages.error(request, "Invalid or corrupted password reset link.")
        return render(request, "auth/reset_password.html", {"token_error": True, "token": token})

    user_id = data.get("user_id")
    user = User.objects.filter(id=user_id, is_active=True).first()
    if not user:
        messages.error(request, "User associated with this reset link was not found.")
        return render(request, "auth/reset_password.html", {"token_error": True, "token": token})

    # 2. Handle POST with new password
    if request.method == "POST":
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        errors = []
        if not new_password:
            errors.append("New password is required.")
        elif len(new_password) < 6:
            errors.append("Password must be at least 6 characters.")
        elif new_password != confirm_password:
            errors.append("Passwords do not match.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "auth/reset_password.html", {
                "token": token,
                "user_email": user.email,
            })

        user.set_password(new_password)
        user.save()

        messages.success(request, "Your password has been successfully reset! You can now sign in with your new password.")
        return redirect("login")

    return render(request, "auth/reset_password.html", {
        "token": token,
        "user_email": user.email,
    })



