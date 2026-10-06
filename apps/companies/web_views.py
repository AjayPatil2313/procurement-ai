from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from apps.companies.models import Company, CompanyMember, CompanyPermission, MemberPermission
from apps.companies.rbac import get_user_rbac_context, company_admin_required, ensure_default_permissions
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
        
        # 1. Add new user
        if action == "add_member":
            email = request.POST.get("email", "").strip().lower()
            password = request.POST.get("password", "").strip() or "User@12345"
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            phone = request.POST.get("phone", "").strip()
            role = request.POST.get("role", CompanyMember.Role.USER)
            
            # Checkbox values
            is_email_verified = ("is_email_verified" in request.POST)
            is_active = ("is_active" in request.POST)
            
            if not email:
                messages.error(request, "Email is required.")
            elif User.objects.filter(email=email).exists():
                messages.error(request, f"A user with email '{email}' already exists.")
            elif len(password) < 6:
                messages.error(request, "Password must be at least 6 characters.")
            else:
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    is_email_verified=is_email_verified,
                    is_active=is_active,
                )
                
                new_member = CompanyMember.objects.create(
                    company=company,
                    user=user,
                    role=role,
                    is_active=is_active,
                )
                
                # Assign permissions
                selected_perms = request.POST.getlist("permissions")
                if role == CompanyMember.Role.USER:
                    if selected_perms:
                        for code in selected_perms:
                            perm_obj, _ = CompanyPermission.objects.get_or_create(
                                module="general",
                                permission=code.strip().upper(),
                            )
                            MemberPermission.objects.get_or_create(member=new_member, permission=perm_obj)
                    else:
                        read_perm, _ = CompanyPermission.objects.get_or_create(module="general", permission="READ")
                        MemberPermission.objects.get_or_create(member=new_member, permission=read_perm)

                messages.success(request, f"Team member '{email}' added successfully.")
                return redirect("company-team-web")

        # 2. Edit existing user
        elif action == "edit_member":
            member_id = request.POST.get("member_id")
            member = get_object_or_404(CompanyMember, id=member_id, company=company)

            member.user.first_name = request.POST.get("first_name", "").strip()
            member.user.last_name = request.POST.get("last_name", "").strip()
            member.user.phone = request.POST.get("phone", "").strip()

            is_active = ("is_active" in request.POST)
            is_email_verified = ("is_email_verified" in request.POST)

            if not is_active and member.user_id == request.user.id:
                messages.error(request, "You cannot deactivate your own account.")
                return redirect("company-team-web")

            member.is_active = is_active
            member.user.is_active = is_active
            member.user.is_email_verified = is_email_verified

            new_password = request.POST.get("password", "").strip()
            if new_password:
                if len(new_password) < 6:
                    messages.error(request, "Password must be at least 6 characters.")
                    return redirect("company-team-web")
                member.user.set_password(new_password)

            if member.user_id != request.user.id:
                new_role = request.POST.get("role")
                if new_role in [CompanyMember.Role.USER, CompanyMember.Role.ADMIN]:
                    member.role = new_role

            member.user.save()
            member.save()

            messages.success(request, f"User '{member.user.email}' updated successfully.")
            return redirect("company-team-web")

        # 3. Toggle Activate / Deactivate status
        elif action == "toggle_status":
            member_id = request.POST.get("member_id")
            member = get_object_or_404(CompanyMember, id=member_id, company=company)

            if member.user_id == request.user.id:
                messages.error(request, "You cannot deactivate your own account.")
                return redirect("company-team-web")

            new_status = not (member.is_active and member.user.is_active)
            member.is_active = new_status
            member.user.is_active = new_status
            member.user.save()
            member.save()

            status_str = "activated" if new_status else "deactivated"
            messages.success(request, f"User '{member.user.email}' has been {status_str}.")
            return redirect("company-team-web")

        # 4. Update member permissions (from the 6 core permissions)
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

    total_count = members.count()
    active_count = sum(1 for m in members if m.is_active and m.user.is_active)
    inactive_count = total_count - active_count
    admin_count = sum(1 for m in members if m.role == CompanyMember.Role.ADMIN)

    return render(request, "company/team.html", {
        "company": company,
        "members": members,
        "total_count": total_count,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "admin_count": admin_count,
        "core_permissions": core_permissions,
        "rbac": rbac,
        "page_title": "Users",
    })


@login_required
def company_rbac_web_view(request):
    """
    Dedicated RBAC & Permissions Management View:
    Allows Company Admin to configure granular 6-action permissions
    (READ, EDIT, UPDATE, DELETE, IMPORT, EXPORT) across Buyer, Seller, or Both modules.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Only Company Administrators can manage RBAC permissions.")
        return redirect("dashboard")

    if not company:
        messages.error(request, "No active company found.")
        return redirect("dashboard")

    ensure_default_permissions()

    members = CompanyMember.objects.filter(company=company).select_related("user").order_by("-joined_at")

    core_permissions = [
        {
            "code": "READ",
            "name": "Read",
            "icon": "fa-eye",
            "color": "blue",
            "badge_class": "bg-blue-100 text-blue-800 border border-blue-200",
            "desc": "View dashboard, search suppliers/buyers, browse requirements & catalog",
        },
        {
            "code": "EDIT",
            "name": "Edit / Create",
            "icon": "fa-pen-to-square",
            "color": "emerald",
            "badge_class": "bg-emerald-100 text-emerald-800 border border-emerald-200",
            "desc": "Create new requirements, add products/services to catalog, post inquiries",
        },
        {
            "code": "UPDATE",
            "name": "Update",
            "icon": "fa-rotate",
            "color": "amber",
            "badge_class": "bg-amber-100 text-amber-800 border border-amber-200",
            "desc": "Modify existing requirements, update product specs, prices, and lead statuses",
        },
        {
            "code": "DELETE",
            "name": "Delete",
            "icon": "fa-trash-can",
            "color": "rose",
            "badge_class": "bg-rose-100 text-rose-800 border border-rose-200",
            "desc": "Soft delete requirements, products, saved suppliers, and inquiries",
        },
        {
            "code": "IMPORT",
            "name": "Import",
            "icon": "fa-file-import",
            "color": "purple",
            "badge_class": "bg-purple-100 text-purple-800 border border-purple-200",
            "desc": "Bulk import requirements, product catalogs, and contact lists from CSV/Excel",
        },
        {
            "code": "EXPORT",
            "name": "Export",
            "icon": "fa-file-export",
            "color": "indigo",
            "badge_class": "bg-indigo-100 text-indigo-800 border border-indigo-200",
            "desc": "Download analytics, export reports, price comparison sheets & PDF/Excel",
        },
    ]

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_permissions":
            member_id = request.POST.get("member_id")
            member = get_object_or_404(CompanyMember, id=member_id, company=company)

            if member.role == CompanyMember.Role.ADMIN:
                messages.warning(request, "Company Admin automatically has all 6 permissions and cannot be restricted.")
                return redirect("company-rbac-web")

            selected_codes = request.POST.getlist("permissions")
            valid_codes = [c for c in selected_codes if c in ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]]

            # Modules to assign based on company type (Buyer, Seller, Both)
            modules_to_assign = ["general"]
            if company.company_type in [Company.CompanyType.BUYER, Company.CompanyType.BOTH]:
                modules_to_assign.append("requirements")
            if company.company_type in [Company.CompanyType.SELLER, Company.CompanyType.BOTH]:
                modules_to_assign.extend(["products", "catalog", "leads"])

            # Delete old permissions and recreate with checked actions
            MemberPermission.objects.filter(member=member).delete()

            for code in valid_codes:
                for mod in modules_to_assign:
                    perm_obj, _ = CompanyPermission.objects.get_or_create(module=mod, permission=code)
                    MemberPermission.objects.get_or_create(member=member, permission=perm_obj)

            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("is_ajax"):
                return JsonResponse({
                    "success": True,
                    "member_id": member.id,
                    "permissions": valid_codes,
                    "message": f"Permissions updated successfully for {member.user.email}.",
                })

            messages.success(request, f"Permissions updated successfully for {member.user.email}: {', '.join(valid_codes) if valid_codes else 'All permissions restricted'}.")
            return redirect("company-rbac-web")

    # Map current active permissions for each member
    for m in members:
        if m.role == CompanyMember.Role.ADMIN:
            m.active_permissions = ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]
        else:
            m.active_permissions = list(
                MemberPermission.objects.filter(member=m)
                .values_list("permission__permission", flat=True)
                .distinct()
            )

    return render(request, "company/rbac.html", {
        "company": company,
        "members": members,
        "core_permissions": core_permissions,
        "rbac": rbac,
        "page_title": "Role-Based Access Control (RBAC)",
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
