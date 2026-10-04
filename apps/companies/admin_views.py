from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.companies.models import Company, CompanyMember
from apps.accounts.models import User
from apps.ai_search.models import APILog, SearchJob
from apps.companies.rbac import get_user_rbac_context, superadmin_required


@login_required
@superadmin_required
def admin_panel_companies_view(request):
    companies = Company.objects.all().order_by("-created_at")
    return render(request, "admin_panel/companies.html", {
        "companies": companies,
        "page_title": "All Companies (Super Admin)",
    })


@login_required
@superadmin_required
def admin_panel_users_view(request):
    users = User.objects.all().order_by("-created_at")
    return render(request, "admin_panel/users.html", {
        "users": users,
        "page_title": "Platform Users (Super Admin)",
    })


@login_required
@superadmin_required
def admin_panel_apilogs_view(request):
    logs = APILog.objects.all().order_by("-created_at")[:50]
    return render(request, "admin_panel/api_logs.html", {
        "logs": logs,
        "page_title": "API Logs & Costs (Super Admin)",
    })
