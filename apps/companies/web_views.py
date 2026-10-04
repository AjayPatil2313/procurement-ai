from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from apps.companies.models import Company, CompanyMember, CompanyPermission, MemberPermission
from apps.companies.rbac import get_user_rbac_context, company_admin_required
from apps.accounts.models import User
from apps.billing.models import Subscription, CreditTransaction


@login_required
def company_profile_web_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not company:
        messages.error(request, "No active company found.")
        return redirect("dashboard")

    if request.method == "POST":
        if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
            messages.error(request, "Only Company Admin can edit the company profile.")
            return redirect("company-profile-web")

        company.name = request.POST.get("name", company.name).strip()
        company.legal_name = request.POST.get("legal_name", company.legal_name).strip()
        company.company_type = request.POST.get("company_type", company.company_type)
        company.industry = request.POST.get("industry", company.industry).strip()
        company.company_size = request.POST.get("company_size", company.company_size)
        company.website = request.POST.get("website", company.website).strip()
        company.email = request.POST.get("email", company.email).strip()
        company.phone = request.POST.get("phone", company.phone).strip()
        company.address_line1 = request.POST.get("address_line1", company.address_line1).strip()
        company.city = request.POST.get("city", company.city).strip()
        company.country = request.POST.get("country", company.country).strip()
        company.preferred_currency = request.POST.get("preferred_currency", company.preferred_currency)
        company.save()

        messages.success(request, "Company profile updated successfully.")
        return redirect("company-profile-web")

    return render(request, "company/profile.html", {
        "company": company,
        "rbac": rbac,
        "page_title": "Company Profile",
    })


@login_required
def company_team_web_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Only Company Admin or Super Admin can manage team members.")
        return redirect("dashboard")

    members = CompanyMember.objects.filter(company=company).select_related("user").order_by("-joined_at")

    # The 6 core permissions
    core_permissions = [
        {
            "code": "READ",
            "name": "Read",
            "icon": "fa-eye",
            "color": "blue",
            "badge_class": "bg-blue-50 text-blue-700 border border-blue-200",
            "description": "View dashboard, browse requirements, products, suppliers, and leads",
        },
        {
            "code": "EDIT",
            "name": "Edit",
            "icon": "fa-pen-to-square",
            "color": "emerald",
            "badge_class": "bg-emerald-50 text-emerald-700 border border-emerald-200",
            "description": "Create new requirements, add products/services, and run AI searches",
        },
        {
            "code": "UPDATE",
            "name": "Update",
            "icon": "fa-rotate",
            "color": "amber",
            "badge_class": "bg-amber-50 text-amber-700 border border-amber-200",
            "description": "Modify existing requirements, product specs, notes, and lead statuses",
        },
        {
            "code": "DELETE",
            "name": "Delete",
            "icon": "fa-trash",
            "color": "red",
            "badge_class": "bg-red-50 text-red-700 border border-red-200",
            "description": "Delete requirements, products, saved leads, and inquiries",
        },
        {
            "code": "IMPORT",
            "name": "Import",
            "icon": "fa-file-import",
            "color": "purple",
            "badge_class": "bg-purple-50 text-purple-700 border border-purple-200",
            "description": "Bulk import requirements, product catalogs, or supplier contact sheets",
        },
        {
            "code": "EXPORT",
            "name": "Export",
            "icon": "fa-file-export",
            "color": "indigo",
            "badge_class": "bg-indigo-50 text-indigo-700 border border-indigo-200",
            "description": "Download reports, supplier comparisons, and export Excel/PDF files",
        },
    ]
    
    # Map active permissions for each member
    # Admin gets all 6 permissions automatically
    for m in members:
        if m.role == CompanyMember.Role.ADMIN:
            m.active_permissions = ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]
        else:
            perms = list(
                MemberPermission.objects.filter(member=m)
                .values_list("permission__permission", flat=True)
                .distinct()
            )
            m.active_permissions = perms

    if request.method == "POST":
        action = request.POST.get("action")
        
        # 1. Invite / Add new member
        if action == "add_member":
            email = request.POST.get("email", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            role = request.POST.get("role", CompanyMember.Role.USER)
            
            if not email:
                messages.error(request, "Email is required.")
            else:
                user, created = User.objects.get_or_create(
                    email=email,
                    defaults={
                        "first_name": first_name,
                        "last_name": last_name,
                        "is_active": True,
                        "is_email_verified": True,
                    }
                )
                if created:
                    user.set_password("Admin@12345")
                    user.save()
                
                new_member, _ = CompanyMember.objects.update_or_create(
                    user=user,
                    defaults={
                        "company": company,
                        "role": role,
                        "is_active": True,
                    }
                )
                # If Company User, give default READ permission
                if role == CompanyMember.Role.USER:
                    read_perm, _ = CompanyPermission.objects.get_or_create(module="general", permission="READ")
                    MemberPermission.objects.get_or_create(member=new_member, permission=read_perm)

                messages.success(request, f"Team member {email} added successfully (Default Password: Admin@12345).")
                return redirect("company-team-web")

        # 2. Update member permissions (from the 6 core permissions)
        elif action == "update_permissions":
            member_id = request.POST.get("member_id")
            member = get_object_or_404(CompanyMember, id=member_id, company=company)

            if member.role == CompanyMember.Role.ADMIN:
                messages.warning(request, "Company Admin automatically has all permissions and cannot be restricted.")
                return redirect("company-team-web")

            selected_codes = request.POST.getlist("permissions")
            valid_codes = [c for c in selected_codes if c in ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]]

            MemberPermission.objects.filter(member=member).delete()
            for code in valid_codes:
                perm_obj, _ = CompanyPermission.objects.get_or_create(
                    module="general",
                    permission=code,
                )
                MemberPermission.objects.get_or_create(member=member, permission=perm_obj)
                # Also link across other modules if they exist
                for mp in CompanyPermission.objects.filter(permission=code):
                    MemberPermission.objects.get_or_create(member=member, permission=mp)

            messages.success(request, f"Permissions updated for {member.user.email}: {', '.join(valid_codes) if valid_codes else 'None'}.")
            return redirect("company-team-web")

    return render(request, "company/team.html", {
        "company": company,
        "members": members,
        "core_permissions": core_permissions,
        "rbac": rbac,
        "page_title": "Team Members & Permissions",
    })


@login_required
def subscription_billing_web_view(request):
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    subscription = getattr(company, "subscription", None)
    if not subscription:
        subscription = Subscription.objects.create(
            company=company,
            plan=Subscription.Plan.PRO,
            credits_total=500,
            credits_used=180,
        )

    if request.method == "POST":
        if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
            messages.error(request, "Only Company Admin can upgrade subscriptions.")
            return redirect("subscription-billing-web")

        action = request.POST.get("action")
        if action == "upgrade_plan":
            new_plan = request.POST.get("plan", "pro")
            subscription.plan = new_plan
            if new_plan == "enterprise":
                subscription.credits_total += 2000
            elif new_plan == "pro":
                subscription.credits_total += 500
            subscription.save()
            messages.success(request, f"Plan upgraded to {subscription.get_plan_display()}!")
            return redirect("subscription-billing-web")

    return render(request, "billing/subscription.html", {
        "company": company,
        "subscription": subscription,
        "rbac": rbac,
        "page_title": "Subscription & Credits",
    })
