import json
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from apps.companies.models import (
    Company,
    CompanyMember,
    CompanyPermission,
    MemberPermission,
    CompanyRole,
    RolePermission,
)
from apps.companies.rbac import get_user_rbac_context, company_admin_required, ensure_default_permissions
from apps.accounts.models import User
from apps.billing.models import Subscription, CreditTransaction


def ensure_company_default_roles(company):
    """
    Ensures standard organizational roles exist for the company:
    1. Admin (System) - Full administrative access
    2. Sales Manager - Full CRUD on seller & CRM modules
    3. Sales Executive - Operational read/write on discovery & proposals
    """
    if not company:
        return
    admin_role, created = CompanyRole.objects.get_or_create(
        company=company,
        name="Admin",
        defaults={
            "description": "Full administrative access across all platform modules and settings.",
            "is_system": True,
        },
    )
    if created or not admin_role.module_permissions.exists():
        admin_role.is_system = True
        admin_role.save(update_fields=["is_system"])
        for mod in RolePermission.MODULE_TITLES.keys():
            RolePermission.objects.update_or_create(
                role=admin_role,
                module=mod,
                defaults={
                    "can_read": True,
                    "can_write": True,
                    "can_edit": True,
                    "can_delete": True,
                    "can_admin": True,
                },
            )

    # Backfill any existing admin members who lack a custom_role
    for m in CompanyMember.objects.filter(company=company, custom_role__isnull=True):
        if m.role == CompanyMember.Role.ADMIN:
            m.custom_role = admin_role
            m.save(update_fields=["custom_role"])


def sync_member_permissions_from_role(member, custom_role):
    """
    Synchronizes granular MemberPermission rows whenever a member's custom role is assigned or updated.
    """
    if not member or not custom_role:
        return

    MemberPermission.objects.filter(member=member).delete()

    if custom_role.is_system or custom_role.name.lower() == "admin":
        member.role = CompanyMember.Role.ADMIN
        member.save(update_fields=["role", "custom_role"])
        return

    member.role = CompanyMember.Role.USER
    member.save(update_fields=["role", "custom_role"])

    for mp in custom_role.module_permissions.all():
        actions = []
        if mp.can_admin:
            actions = ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"]
        else:
            if mp.can_read:
                actions.append("READ")
            if mp.can_write:
                actions.append("EDIT")
            if mp.can_edit:
                actions.append("EDIT")
            if mp.can_delete:
                actions.append("DELETE")

        for act in set(actions):
            perm_obj, _ = CompanyPermission.objects.get_or_create(module=mp.module, permission=act)
            MemberPermission.objects.get_or_create(member=member, permission=perm_obj)
            if act == "READ":
                gen_p, _ = CompanyPermission.objects.get_or_create(module="general", permission="READ")
                MemberPermission.objects.get_or_create(member=member, permission=gen_p)



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
        company.state = request.POST.get("state", company.state).strip()
        company.postal_code = request.POST.get("postal_code", company.postal_code).strip()
        company.country = request.POST.get("country", company.country).strip()
        company.gst_vat_no = request.POST.get("gst_vat_no", company.gst_vat_no).strip()
        company.description = request.POST.get("description", company.description).strip()
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

    ensure_company_default_roles(company)

    members = CompanyMember.objects.filter(company=company).select_related("user", "custom_role").prefetch_related("custom_role__module_permissions").order_by("-joined_at")
    company_roles = list(CompanyRole.objects.filter(company=company).order_by("-is_system", "name"))

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
            
            # Roles from Roles & Responsibilities
            role_id = request.POST.get("role_id")
            role = request.POST.get("role", CompanyMember.Role.USER)
            chosen_role = None

            if role_id:
                chosen_role = CompanyRole.objects.filter(id=role_id, company=company).first()
                if chosen_role:
                    role = CompanyMember.Role.ADMIN if (chosen_role.is_system or chosen_role.name.lower() == "admin") else CompanyMember.Role.USER
            elif role == CompanyMember.Role.ADMIN:
                chosen_role = CompanyRole.objects.filter(company=company, is_system=True).first()
            else:
                chosen_role = CompanyRole.objects.filter(company=company, is_system=False).first()
            
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
                    custom_role=chosen_role,
                    is_active=is_active,
                )
                
                # Assign permissions based on role
                if chosen_role:
                    sync_member_permissions_from_role(new_member, chosen_role)
                else:
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
                role_id = request.POST.get("role_id")
                if role_id:
                    chosen_role = CompanyRole.objects.filter(id=role_id, company=company).first()
                    if chosen_role:
                        member.custom_role = chosen_role
                        member.role = CompanyMember.Role.ADMIN if (chosen_role.is_system or chosen_role.name.lower() == "admin") else CompanyMember.Role.USER
                        member.save(update_fields=["role", "custom_role"])
                        sync_member_permissions_from_role(member, chosen_role)
                else:
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

        # 4. Delete user from team
        elif action == "delete_member":
            member_id = request.POST.get("member_id")
            member = get_object_or_404(CompanyMember, id=member_id, company=company)

            if member.user_id == request.user.id:
                messages.error(request, "You cannot delete your own account.")
                return redirect("company-team-web")

            user_email = member.user.email
            user_to_delete = member.user
            member.delete()
            if not CompanyMember.objects.filter(user=user_to_delete).exists() and not user_to_delete.is_superuser:
                user_to_delete.delete()

            messages.success(request, f"User '{user_email}' deleted successfully from company team.")
            return redirect("company-team-web")

        # 5. Update member permissions (from the 6 core permissions)
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
        "company_roles": company_roles,
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


# ============================================================
# SELLER / SALES: ROLES & RESPONSIBILITIES (ROLE MANAGEMENT)
# ============================================================

@login_required
def seller_roles_web_view(request):
    """
    Role Management (Roles & Responsibilities):
    Renders configured organizational roles with module permissions matrix pills.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not company:
        messages.error(request, "No active company found.")
        return redirect("dashboard")

    if not (rbac["is_company_admin"] or rbac["is_super_admin"] or rbac["can_view_seller"]):
        messages.error(request, "Access restricted: Company administrator or seller permissions required.")
        return redirect("dashboard")

    ensure_company_default_roles(company)

    roles = list(CompanyRole.objects.filter(company=company).prefetch_related("module_permissions").order_by("-is_system", "name"))

    roles_data = []
    for r in roles:
        summary = r.get_permission_summary()
        perms_map = {}
        for mp in r.module_permissions.all():
            perms_map[mp.module] = {
                "can_read": mp.can_read,
                "can_write": mp.can_write,
                "can_edit": mp.can_edit,
                "can_delete": mp.can_delete,
                "can_admin": mp.can_admin,
            }
        is_full_admin = bool(r.is_system)
        roles_data.append({
            "role": r,
            "summary": summary,
            "perms_json": json.dumps(perms_map),
            "is_full_admin": is_full_admin,
            "member_count": r.members.count(),
        })

    modules = [
        {"key": "products", "name": "Products", "icon": "fa-box-open", "desc": "Product catalog, items, specifications & MOQ"},
        {"key": "find_buyers", "name": "Find Buyers", "icon": "fa-crosshairs", "desc": "AI buyer discovery & targeted web scraping"},
        {"key": "leads", "name": "Buyer Leads", "icon": "fa-users-viewfinder", "desc": "Discovered enterprise leads & fit scoring"},
        {"key": "saved_buyers", "name": "Saved Buyers", "icon": "fa-bookmark", "desc": "CRM deal pipeline, notes, and sales rep assignments"},
        {"key": "inquiries", "name": "Buyer Inquiries", "icon": "fa-paper-plane", "desc": "Commercial RFQs, proposals, and quote capture"},
        {"key": "export_reports", "name": "Export Reports", "icon": "fa-download", "desc": "CSV downloads & catalog matrix exports"},
        {"key": "matching_parameters", "name": "Matching Parameters", "icon": "fa-sliders", "desc": "AI matching score criteria & rules"},
        {"key": "team", "name": "User Management", "icon": "fa-users-gear", "desc": "Employee management & user assignments"},
        {"key": "billing", "name": "Subscription & Credits", "icon": "fa-coins", "desc": "Plan status and AI credits allocation"},
        {"key": "requirements", "name": "Requirements", "icon": "fa-clipboard-list", "desc": "Procurement requisitions & requests"},
        {"key": "find_suppliers", "name": "Find Suppliers", "icon": "fa-magnifying-glass-chart", "desc": "Supplier discovery & search"},
        {"key": "saved_suppliers", "name": "Saved Suppliers", "icon": "fa-bookmark", "desc": "Shortlisted supplier vendors"},
    ]

    return render(request, "company/roles.html", {
        "company": company,
        "roles": roles,
        "roles_data": roles_data,
        "modules": modules,
        "system_role_count": sum(1 for r in roles if r.is_system),
        "custom_role_count": sum(1 for r in roles if not r.is_system),
        "assigned_users_count": CompanyMember.objects.filter(company=company, is_active=True).count(),
        "rbac": rbac,
        "page_title": "Role Management & Responsibilities",
    })


@login_required
def seller_role_create_view(request):
    """
    Creates a new custom role with matrix permissions.
    """
    if request.method != "POST":
        return redirect("seller-roles-web")

    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company administrator privileges required.")
        return redirect("seller-roles-web")

    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if not name:
        messages.error(request, "Role name is required.")
        return redirect("seller-roles-web")

    if CompanyRole.objects.filter(company=company, name__iexact=name).exists():
        messages.error(request, f"A role named '{name}' already exists in your company.")
        return redirect("seller-roles-web")

    role = CompanyRole.objects.create(
        company=company,
        name=name,
        description=description,
        is_system=False,
    )

    module_keys = list(RolePermission.MODULE_TITLES.keys())
    for mod in module_keys:
        can_read = request.POST.get(f"perm_{mod}_read") == "1"
        can_write = request.POST.get(f"perm_{mod}_write") == "1"
        can_edit = request.POST.get(f"perm_{mod}_edit") == "1"
        can_delete = request.POST.get(f"perm_{mod}_delete") == "1"
        can_admin = request.POST.get(f"perm_{mod}_admin") == "1"
        if can_admin:
            can_read = can_write = can_edit = can_delete = True

        RolePermission.objects.create(
            role=role,
            module=mod,
            can_read=can_read,
            can_write=can_write,
            can_edit=can_edit,
            can_delete=can_delete,
            can_admin=can_admin,
        )

    messages.success(request, f"Role '{name}' successfully created!")
    return redirect("seller-roles-web")


@login_required
def seller_role_edit_view(request, role_id):
    """
    Updates role name, description, and module permissions.
    """
    if request.method != "POST":
        return redirect("seller-roles-web")

    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company administrator privileges required.")
        return redirect("seller-roles-web")

    role = get_object_or_404(CompanyRole, id=role_id, company=company)

    new_name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if new_name and not role.is_system:
        # Check duplicate name
        if CompanyRole.objects.filter(company=company, name__iexact=new_name).exclude(id=role.id).exists():
            messages.error(request, f"Another role named '{new_name}' already exists.")
            return redirect("seller-roles-web")
        role.name = new_name

    role.description = description
    role.save()

    module_keys = list(RolePermission.MODULE_TITLES.keys())
    for mod in module_keys:
        can_read = request.POST.get(f"perm_{mod}_read") == "1"
        can_write = request.POST.get(f"perm_{mod}_write") == "1"
        can_edit = request.POST.get(f"perm_{mod}_edit") == "1"
        can_delete = request.POST.get(f"perm_{mod}_delete") == "1"
        can_admin = request.POST.get(f"perm_{mod}_admin") == "1"
        if can_admin:
            can_read = can_write = can_edit = can_delete = True

        RolePermission.objects.update_or_create(
            role=role,
            module=mod,
            defaults={
                "can_read": can_read,
                "can_write": can_write,
                "can_edit": can_edit,
                "can_delete": can_delete,
                "can_admin": can_admin,
            },
        )

    # Sync permissions for all members assigned to this role
    for m in role.members.all():
        sync_member_permissions_from_role(m, role)

    messages.success(request, f"Role '{role.name}' updated successfully!")
    return redirect("seller-roles-web")


@login_required
def seller_role_delete_view(request, role_id):
    """
    Deletes a custom role.
    """
    if request.method != "POST":
        return redirect("seller-roles-web")

    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company administrator privileges required.")
        return redirect("seller-roles-web")

    role = get_object_or_404(CompanyRole, id=role_id, company=company)

    if role.is_system:
        messages.error(request, "System default roles cannot be deleted.")
        return redirect("seller-roles-web")

    role_name = role.name
    # Fallback any assigned members to default admin or user
    default_role = CompanyRole.objects.filter(company=company, is_system=True).first()
    for m in role.members.all():
        m.custom_role = default_role
        m.save(update_fields=["custom_role"])
        if default_role:
            sync_member_permissions_from_role(m, default_role)

    role.delete()
    messages.success(request, f"Role '{role_name}' deleted successfully.")
    return redirect("seller-roles-web")

