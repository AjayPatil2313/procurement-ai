import csv
import json
from datetime import datetime, timedelta
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from apps.companies.models import (
    Company,
    CompanyMember,
    CompanyPermission,
    MemberPermission,
    CompanyRole,
    RolePermission,
)
from decimal import Decimal
from django.db.models import Sum, Count, Q
from apps.companies.rbac import get_user_rbac_context, company_admin_required, ensure_default_permissions
from apps.accounts.models import User
from apps.billing.models import (
    Subscription,
    SubscriptionPlan,
    CompanyCreditWallet,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
    Payment,
    Invoice,
)
from apps.billing.services.wallet import CreditWalletService


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
                actions.append("UPDATE")
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
            can_add, current_count, max_limit = CreditWalletService.can_add_team_member(company)
            if not can_add:
                messages.error(request, f"Cannot add team member: Plan limit reached ({current_count}/{max_limit} seats). Please upgrade your subscription plan.")
                return redirect("company-team-web")

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
            if not new_status and member.role == CompanyMember.Role.ADMIN:
                active_admins = CompanyMember.objects.filter(
                    company=company, role=CompanyMember.Role.ADMIN, is_active=True
                ).count()
                if active_admins <= 1:
                    messages.error(request, "Action blocked: Cannot deactivate the only active Administrator for this company.")
                    return redirect("company-team-web")

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

            if member.role == CompanyMember.Role.ADMIN:
                admin_count = CompanyMember.objects.filter(
                    company=company, role=CompanyMember.Role.ADMIN
                ).count()
                if admin_count <= 1:
                    messages.error(request, "Action blocked: Cannot delete the only Administrator of this company.")
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
    """
    Unified Subscription & Credits Dashboard:
    - Company Admin: Full billing controls, wallet breakdown, dynamic plans, upgrade/top-up, team usage, invoices.
    - Company User: Personal usage metrics, searches/matching counts, personal ledger, read-only plan info.
    - Super Admin: Directed to admin overview or full management.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    CreditWalletService.ensure_default_plans_and_costs()

    if not company:
        if rbac["is_super_admin"]:
            return redirect("admin-panel-plans")
        messages.error(request, "Please switch to an active company to access Subscription & Credits.")
        return redirect("dashboard")

    wallet = CreditWalletService.get_or_create_wallet(company)
    subscription = getattr(company, "subscription", None)
    if not subscription:
        default_plan = SubscriptionPlan.objects.filter(code="pro").first()
        subscription = Subscription.objects.create(
            company=company,
            plan_tier=default_plan,
            plan=default_plan.code if default_plan else "pro",
            credits_total=default_plan.included_credits if default_plan else 500,
            credits_used=0,
            status=Subscription.Status.ACTIVE,
            valid_till=timezone.now().date() + timedelta(days=30),
            renewal_date=timezone.now() + timedelta(days=30),
        )
    elif not subscription.plan_tier:
        matched_plan = SubscriptionPlan.objects.filter(code=subscription.plan).first()
        if not matched_plan:
            matched_plan = SubscriptionPlan.objects.filter(code="pro").first()
        if matched_plan:
            subscription.plan_tier = matched_plan
            subscription.save(update_fields=["plan_tier"])

    available_plans = SubscriptionPlan.objects.filter(
        status=SubscriptionPlan.PlanStatus.ACTIVE,
        is_public=True,
    ).order_by("order", "price")

    # Check CSV export request
    if request.GET.get("export") == "ledger_csv":
        if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
            messages.error(request, "Permission denied: Company administrator privileges required to export ledger.")
            return redirect("subscription-billing-web")

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        clean_comp_name = company.name.replace(" ", "_").replace("/", "_")
        response["Content-Disposition"] = f'attachment; filename="Credit_Ledger_{clean_comp_name}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Receipt Number", "Triggered By", "Transaction Type", "Credits", "Balance After", "Reason / Notes", "Date Time"])

        for tx in CreditTransaction.objects.filter(company=company).select_related("user").order_by("-created_at"):
            user_str = tx.user.get_full_name() or tx.user.email if tx.user else "System Engine"
            writer.writerow([
                tx.receipt_number or f"RCP-{tx.id:06d}",
                user_str,
                tx.get_transaction_type_display(),
                f"+{tx.credits}" if tx.credits > 0 else tx.credits,
                tx.balance_after,
                tx.notes,
                tx.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ])
        return response

    if request.method == "POST":
        action = request.POST.get("action")

        # 0. REQUEST TOP-UP (Accessible by Company Users & Team Members)
        if action == "request_topup":
            pack_raw = request.POST.get("credits_pack", "500").strip()
            note = request.POST.get("note", "").strip()
            notified_count = CreditWalletService.request_credit_topup(
                company=company,
                user=request.user,
                requested_pack=pack_raw,
                note=note,
            )
            messages.success(request, f"Credit top-up request for {pack_raw} credits sent to {notified_count} company administrator(s).")
            return redirect("subscription-billing-web")

        # Admin-only operations below
        if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
            messages.error(request, "Only Company Admin has permission to modify subscription plans or purchase credits.")
            return redirect("subscription-billing-web")

        # 1. UPGRADE / CHANGE PLAN (with 18% GST calculation)
        if action == "upgrade_plan":
            target_plan_code = request.POST.get("plan_code", "").strip()
            target_plan = SubscriptionPlan.objects.filter(code=target_plan_code, status=SubscriptionPlan.PlanStatus.ACTIVE).first()
            if not target_plan:
                messages.error(request, "Selected subscription plan is not currently available.")
                return redirect("subscription-billing-web")

            subtotal, tax, total_amount = CreditWalletService.calculate_gst_breakdown(target_plan.price)

            payment = Payment.objects.create(
                company=company,
                subscription=subscription,
                amount=total_amount,
                currency=target_plan.currency,
                provider="Development Gateway / Instant Activation",
                provider_reference=f"PAY-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                status=Payment.Status.SUCCESS,
                payment_date=timezone.now(),
            )

            Invoice.objects.create(
                company=company,
                subscription=subscription,
                payment=payment,
                billing_period_start=timezone.now().date(),
                billing_period_end=timezone.now().date() + timedelta(days=30 if target_plan.billing_cycle == "monthly" else 365),
                subtotal=subtotal,
                tax=tax,
                total_amount=total_amount,
                currency=target_plan.currency,
                status=Invoice.Status.PAID,
                issue_date=timezone.now().date(),
                due_date=timezone.now().date(),
            )

            CreditWalletService.allocate_plan_credits(company, target_plan, is_renewal=False)
            subscription.refresh_from_db()
            subscription.auto_renew = True
            subscription.cancelled_at = None
            subscription.expiry_warning_5d_sent = False
            subscription.save(update_fields=["auto_renew", "cancelled_at", "expiry_warning_5d_sent", "updated_at"])
            messages.success(request, f"Plan successfully changed to {target_plan.name}! {target_plan.included_credits} credits allocated (Invoice generated with 18% GST). Auto-Pay is active.")
            return redirect("subscription-billing-web")

        # 2. TOP-UP / BUY CREDITS (with 18% GST calculation)
        elif action == "buy_credits":
            credits_pack_raw = request.POST.get("credits_pack", "100").strip()
            try:
                credits_pack = int(credits_pack_raw)
            except ValueError:
                credits_pack = 100

            price_map = {100: Decimal("2499.00"), 500: Decimal("9999.00"), 1000: Decimal("17999.00")}
            pack_base_price = price_map.get(credits_pack, Decimal(credits_pack * 25))
            subtotal, tax, total_amount = CreditWalletService.calculate_gst_breakdown(pack_base_price)

            payment = Payment.objects.create(
                company=company,
                subscription=subscription,
                amount=total_amount,
                currency="INR",
                provider="Development Gateway / Credit Top-up",
                provider_reference=f"TOPUP-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                status=Payment.Status.SUCCESS,
                payment_date=timezone.now(),
            )

            Invoice.objects.create(
                company=company,
                subscription=subscription,
                payment=payment,
                billing_period_start=timezone.now().date(),
                billing_period_end=timezone.now().date() + timedelta(days=365),
                subtotal=subtotal,
                tax=tax,
                total_amount=total_amount,
                currency="INR",
                status=Invoice.Status.PAID,
                issue_date=timezone.now().date(),
                due_date=timezone.now().date(),
            )

            CreditWalletService.adjust_credits_admin(
                company=company,
                delta=credits_pack,
                reason=f"Credit Top-Up Pack (+{credits_pack} credits)",
                admin_user=request.user,
            )
            messages.success(request, f"Successfully purchased {credits_pack} credits! Total ₹{total_amount} (incl. 18% GST). Balance updated immediately.")
            return redirect("subscription-billing-web")

        # 3. RENEW PLAN (with 18% GST calculation)
        elif action == "renew_plan":
            target_plan_code = request.POST.get("plan_code", "").strip()
            current_tier = None
            if target_plan_code:
                current_tier = SubscriptionPlan.objects.filter(code=target_plan_code, status=SubscriptionPlan.PlanStatus.ACTIVE).first()
            if not current_tier:
                current_tier = subscription.plan_tier or SubscriptionPlan.objects.filter(code="pro").first()
            if current_tier:
                subtotal, tax, total_amount = CreditWalletService.calculate_gst_breakdown(current_tier.price)

                payment = Payment.objects.create(
                    company=company,
                    subscription=subscription,
                    amount=total_amount,
                    currency=current_tier.currency,
                    provider="Development Gateway / Subscription Renewal",
                    provider_reference=f"REN-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                    status=Payment.Status.SUCCESS,
                    payment_date=timezone.now(),
                )
                Invoice.objects.create(
                    company=company,
                    subscription=subscription,
                    payment=payment,
                    billing_period_start=timezone.now().date(),
                    billing_period_end=timezone.now().date() + timedelta(days=30 if current_tier.billing_cycle == "monthly" else 365),
                    subtotal=subtotal,
                    tax=tax,
                    total_amount=total_amount,
                    currency=current_tier.currency,
                    status=Invoice.Status.PAID,
                    issue_date=timezone.now().date(),
                    due_date=timezone.now().date(),
                )
                CreditWalletService.allocate_plan_credits(company, current_tier, is_renewal=True)
                subscription.refresh_from_db()
                subscription.auto_renew = True
                subscription.cancelled_at = None
                subscription.expiry_warning_5d_sent = False
                subscription.save(update_fields=["auto_renew", "cancelled_at", "expiry_warning_5d_sent", "updated_at"])
                messages.success(request, f"Subscription renewed successfully! {current_tier.included_credits} credits reloaded (Total ₹{total_amount} incl. 18% GST). Auto-Pay is active.")
            return redirect("subscription-billing-web")

        # 4. TOGGLE AUTO-PAY (Action: toggle_autopay)
        elif action == "toggle_autopay":
            auto_pay_val = request.POST.get("auto_pay")
            if auto_pay_val is not None:
                enable_autopay = (auto_pay_val.strip().lower() == "true")
            else:
                enable_autopay = not subscription.auto_renew

            subscription.auto_renew = enable_autopay
            if not enable_autopay:
                subscription.cancelled_at = timezone.now()
                messages.warning(
                    request,
                    f"Auto-Pay has been paused. You will retain full access until your plan expires on "
                    f"{subscription.valid_till or subscription.renewal_date.date()}. After this date, features will be restricted unless renewed."
                )
            else:
                subscription.cancelled_at = None
                messages.success(request, "Auto-Pay has been successfully activated! Your plan will renew automatically on the renewal date.")
            subscription.save(update_fields=["auto_renew", "cancelled_at", "updated_at"])
            return redirect("subscription-billing-web")

    # Read data based on role
    team_usage = []
    company_transactions_page = []
    invoices = []
    tx_type = request.GET.get("tx_type", "all").strip().lower()
    date_range = request.GET.get("date_range", "all").strip().lower()

    if rbac["is_company_admin"] or rbac["is_super_admin"]:
        team_usage = list(
            UsageRecord.objects.filter(company=company)
            .values("user__id", "user__email", "user__first_name", "user__last_name")
            .annotate(total_credits=Sum("credits_charged"), total_jobs=Count("id"))
            .order_by("-total_credits")[:15]
        )

        # Filtered & Paginated Credit Ledger
        tx_qs = CreditTransaction.objects.filter(company=company).select_related("user")
        if tx_type != "all":
            tx_qs = tx_qs.filter(transaction_type=tx_type)
        if date_range == "7d":
            tx_qs = tx_qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
        elif date_range == "30d":
            tx_qs = tx_qs.filter(created_at__gte=timezone.now() - timedelta(days=30))
        elif date_range == "90d":
            tx_qs = tx_qs.filter(created_at__gte=timezone.now() - timedelta(days=90))

        paginator = Paginator(tx_qs.order_by("-created_at"), 20)
        page_num = request.GET.get("page", 1)
        company_transactions_page = paginator.get_page(page_num)

        invoices = list(Invoice.objects.filter(company=company).order_by("-issue_date")[:15])

    # Company User personal metrics & tables
    my_credits_used = 0
    my_searches_count = 0
    my_extractions_count = 0
    my_matching_count = 0
    my_recent_jobs = []
    my_transactions = []

    if rbac["is_company_user"]:
        my_credits_used = (
            UsageRecord.objects.filter(
                company=company,
                user=request.user,
                status=UsageRecord.ProcessingStatus.COMPLETED,
            ).aggregate(s=Sum("credits_charged"))["s"]
            or 0
        )
        my_searches_count = UsageRecord.objects.filter(company=company, user=request.user, feature_code="vendor_discovery").count()
        my_extractions_count = UsageRecord.objects.filter(company=company, user=request.user, feature_code="website_extraction").count()
        my_matching_count = UsageRecord.objects.filter(company=company, user=request.user, feature_code="ai_matching").count()
        my_recent_jobs = list(
            UsageRecord.objects.filter(company=company, user=request.user)
            .select_related("search_job")
            .order_by("-started_at")[:15]
        )
        my_transactions = list(
            CreditTransaction.objects.filter(company=company, user=request.user).order_by("-created_at")[:20]
        )

    # Check proactive 5-day expiry alert
    CreditWalletService.check_and_notify_5day_expiry(company)

    # Dynamic 3-Plan Tier State Calculation (Points 1 - 4)
    is_expired = subscription.is_expired if subscription else False
    current_tier = subscription.plan_tier if subscription else None
    has_chosen_paid_plan = bool(current_tier and current_tier.code != "free" and subscription.plan != "free")
    current_order = current_tier.order if current_tier else 0
    current_price = current_tier.price if current_tier else Decimal("0.00")

    annotated_plans = []
    for p in available_plans:
        p_is_current = bool(current_tier and p.code == current_tier.code)
        p_is_higher = p.order > current_order or p.price > current_price
        p_is_lower = p.order < current_order or p.price < current_price

        if not has_chosen_paid_plan:
            # Scenario 1: Company is on free tier / has not chosen any paid plan
            if p.code == "free" or (current_tier and p.code == current_tier.code):
                button_state = "current"
                button_label = "Current Plan Active"
            else:
                button_state = "choose"
                button_label = f"Choose {p.name}"
        elif is_expired:
            # Scenario 4: Plan expired (e.g. 2 days ago / renewal date passed)
            if p_is_current:
                button_state = "renew"
                button_label = f"Renew {p.name}"
            elif p_is_higher:
                button_state = "upgrade"
                button_label = f"Upgrade to {p.name}"
            else:
                button_state = "disabled"
                button_label = "Downgrade Unavailable"
        else:
            # Scenarios 2 & 3: Active plan chosen
            if p_is_current:
                button_state = "current"
                button_label = "Current Plan Active"
            elif p_is_higher:
                button_state = "upgrade"
                button_label = f"Upgrade to {p.name}"
            else:
                button_state = "disabled"
                button_label = "Downgrade Unavailable"

        annotated_plans.append({
            "plan": p,
            "is_current": p_is_current,
            "is_higher": p_is_higher,
            "is_lower": p_is_lower,
            "button_state": button_state,
            "button_label": button_label,
        })

    return render(request, "billing/subscription.html", {
        "company": company,
        "subscription": subscription,
        "wallet": wallet,
        "available_plans": available_plans,
        "annotated_plans": annotated_plans,
        "is_expired": is_expired,
        "has_chosen_paid_plan": has_chosen_paid_plan,
        "team_usage": team_usage,
        "company_transactions": company_transactions_page,
        "invoices": invoices,
        "my_credits_used": my_credits_used,
        "my_searches_count": my_searches_count,
        "my_extractions_count": my_extractions_count,
        "my_matching_count": my_matching_count,
        "my_recent_jobs": my_recent_jobs,
        "my_transactions": my_transactions,
        "tx_type": tx_type,
        "date_range": date_range,
        "rbac": rbac,
        "page_title": "Subscription & Credits",
    })


@login_required
def company_invoices_web_view(request):
    """
    Dedicated Company Invoices & Credit Ledger Dashboard:
    - Official tax invoices with GST breakdown, status, and PDF download.
    - Company credit ledger & receipts with transaction filtering.
    - Configurable pagination (default 10 items per page; options: 10, 25, 50, 100).
    - Bank statement style CSV export modal with custom date ranges.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not company:
        messages.error(request, "Please switch to an active company to access Invoices & Billing.")
        return redirect("dashboard")

    wallet = CreditWalletService.get_or_create_wallet(company)
    subscription = getattr(company, "subscription", None)

    # 1. Invoices Query with configurable pagination (default 10)
    invoices_qs = Invoice.objects.filter(company=company).order_by("-issue_date", "-created_at")
    inv_status_filter = request.GET.get("inv_status", "all").strip().lower()
    if inv_status_filter and inv_status_filter != "all":
        invoices_qs = invoices_qs.filter(status=inv_status_filter)

    try:
        inv_page_size = int(request.GET.get("inv_limit", 10))
        if inv_page_size not in [10, 25, 50, 100]:
            inv_page_size = 10
    except (ValueError, TypeError):
        inv_page_size = 10

    inv_paginator = Paginator(invoices_qs, inv_page_size)
    inv_page_num = request.GET.get("inv_page", 1)
    invoices_page = inv_paginator.get_page(inv_page_num)

    # 2. Credit Transactions Ledger with filtering & configurable pagination (default 10)
    tx_qs = CreditTransaction.objects.filter(company=company).select_related("user")
    tx_type = request.GET.get("tx_type", "all").strip().lower()
    date_range = request.GET.get("date_range", "all").strip().lower()

    if tx_type != "all":
        tx_qs = tx_qs.filter(transaction_type=tx_type)

    if date_range == "7d":
        tx_qs = tx_qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
    elif date_range == "30d":
        tx_qs = tx_qs.filter(created_at__gte=timezone.now() - timedelta(days=30))
    elif date_range == "90d":
        tx_qs = tx_qs.filter(created_at__gte=timezone.now() - timedelta(days=90))

    try:
        ledger_page_size = int(request.GET.get("ledger_limit", 10))
        if ledger_page_size not in [10, 25, 50, 100]:
            ledger_page_size = 10
    except (ValueError, TypeError):
        ledger_page_size = 10

    ledger_paginator = Paginator(tx_qs.order_by("-created_at"), ledger_page_size)
    ledger_page_num = request.GET.get("ledger_page", 1)
    ledger_page = ledger_paginator.get_page(ledger_page_num)

    # Summary statistics
    total_invoiced = Invoice.objects.filter(company=company, status=Invoice.Status.PAID).aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    total_credits_inflow = CreditTransaction.objects.filter(company=company, credits__gt=0).aggregate(s=Sum("credits"))["s"] or 0
    total_credits_consumed = CreditTransaction.objects.filter(company=company, credits__lt=0).aggregate(s=Sum("credits"))["s"] or 0
    total_credits_consumed = abs(total_credits_consumed)

    return render(request, "billing/invoices.html", {
        "company": company,
        "subscription": subscription,
        "wallet": wallet,
        "invoices_page": invoices_page,
        "ledger_page": ledger_page,
        "inv_limit": inv_page_size,
        "ledger_limit": ledger_page_size,
        "inv_status": inv_status_filter,
        "tx_type": tx_type,
        "date_range": date_range,
        "total_invoiced": total_invoiced,
        "total_credits_inflow": total_credits_inflow,
        "total_credits_consumed": total_credits_consumed,
        "rbac": rbac,
        "page_title": "Invoices & Billing History",
    })


@login_required
def export_statement_csv_view(request):
    """
    Exports official bank-statement style CSV for company audit:
    - Custom date range filter (start_date, end_date)
    - Opening and closing credit balances
    - Summary of credits inflow and outflow
    - Itemized transaction ledger with receipt IDs, timestamps, and balance after
    - Itemized invoice ledger
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not company:
        messages.error(request, "No active company found.")
        return redirect("dashboard")

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Permission denied: Company administrator privileges required to export account statement.")
        return redirect("company-invoices")

    # Date range filters
    start_date_str = request.GET.get("start_date", "").strip()
    end_date_str = request.GET.get("end_date", "").strip()

    today = timezone.now().date()
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = today - timedelta(days=30)
    else:
        start_date = today - timedelta(days=30)

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            end_date = today
    else:
        end_date = today

    # End of day datetime for end_date
    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

    # Transactions in period
    tx_qs = CreditTransaction.objects.filter(
        company=company,
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).select_related("user").order_by("created_at")

    # Invoices in period
    invoices_qs = Invoice.objects.filter(
        company=company,
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).order_by("created_at")

    # Balances
    wallet = getattr(company, "credit_wallet", None)
    closing_balance = wallet.total_available if wallet else 0

    # Calculate inflows and outflows in period
    inflow = tx_qs.filter(credits__gt=0).aggregate(s=Sum("credits"))["s"] or 0
    outflow = abs(tx_qs.filter(credits__lt=0).aggregate(s=Sum("credits"))["s"] or 0)

    # Transactions prior to start_dt to find opening balance
    prior_tx = CreditTransaction.objects.filter(company=company, created_at__lt=start_dt).order_by("-created_at").first()
    opening_balance = prior_tx.balance_after if prior_tx else 0

    # Build CSV Response
    clean_comp_name = company.name.replace(" ", "_").replace("/", "_")
    filename = f"Account_Statement_{clean_comp_name}_{start_date}_{end_date}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    # Bank-Statement Style Headers
    writer.writerow(["================================================================================="])
    writer.writerow(["PROCUREMENT AI - OFFICIAL ACCOUNT STATEMENT & AUDIT LEDGER"])
    writer.writerow(["================================================================================="])
    writer.writerow(["Company Name:", company.name])
    writer.writerow(["Company ID:", company.id])
    writer.writerow(["Statement Period:", f"{start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"])
    writer.writerow(["Statement Generated On:", timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")])
    writer.writerow(["Generated By:", request.user.get_full_name() or request.user.email])
    writer.writerow([])
    writer.writerow(["---------------------------------------------------------------------------------"])
    writer.writerow(["EXECUTIVE SUMMARY"])
    writer.writerow(["---------------------------------------------------------------------------------"])
    writer.writerow(["Opening Credit Balance:", opening_balance])
    writer.writerow(["Total Credit Inflows (+):", f"+{inflow}"])
    writer.writerow(["Total Credit Outflows (-):", f"-{outflow}"])
    writer.writerow(["Closing Credit Balance:", closing_balance])
    writer.writerow(["Total Tax Invoices in Period:", invoices_qs.count()])
    total_inv_amount = invoices_qs.aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    writer.writerow(["Total Invoiced Amount in Period:", f"INR {total_inv_amount}"])
    writer.writerow([])
    writer.writerow(["================================================================================="])
    writer.writerow(["PART 1: ITEMIZED CREDIT LEDGER TRANSACTIONS"])
    writer.writerow(["================================================================================="])
    writer.writerow([
        "Date & Time",
        "Receipt Reference",
        "Transaction Type",
        "Credits Inflow (+)",
        "Credits Outflow (-)",
        "Balance After",
        "Triggered By",
        "Notes / Feature Description",
    ])

    for tx in tx_qs:
        user_name = tx.user.get_full_name() or tx.user.email if tx.user else "System Engine"
        credits_in = f"+{tx.credits}" if tx.credits > 0 else ""
        credits_out = str(tx.credits) if tx.credits < 0 else ""
        writer.writerow([
            tx.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            tx.receipt_number or f"RCP-{tx.id:06d}",
            tx.get_transaction_type_display(),
            credits_in,
            credits_out,
            tx.balance_after,
            user_name,
            tx.notes,
        ])

    writer.writerow([])
    writer.writerow(["================================================================================="])
    writer.writerow(["PART 2: ITEMIZED TAX INVOICES & BILLING IN PERIOD"])
    writer.writerow(["================================================================================="])
    writer.writerow([
        "Invoice Number",
        "Issue Date",
        "Billing Period Start",
        "Billing Period End",
        "Subtotal (Net)",
        "GST Tax (18%)",
        "Total Amount",
        "Currency",
        "Status",
    ])

    for inv in invoices_qs:
        writer.writerow([
            inv.invoice_number,
            inv.issue_date.strftime("%Y-%m-%d"),
            inv.billing_period_start.strftime("%Y-%m-%d"),
            inv.billing_period_end.strftime("%Y-%m-%d"),
            inv.subtotal,
            inv.tax,
            inv.total_amount,
            inv.currency,
            inv.get_status_display(),
        ])

    writer.writerow([])
    writer.writerow(["================================================================================="])
    writer.writerow(["END OF ACCOUNT STATEMENT"])
    writer.writerow(["================================================================================="])

    return response


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

    roles = list(CompanyRole.objects.filter(company=company).prefetch_related("module_permissions", "members__user").order_by("-is_system", "name"))

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
        assigned_members = [
            {
                "id": m.id,
                "email": m.user.email,
                "name": m.user.get_full_name() or m.user.email.split("@")[0],
                "joined_at": m.joined_at.strftime("%d %b %Y") if m.joined_at else "",
                "is_active": bool(m.is_active and m.user.is_active),
            }
            for m in r.members.all()
        ]
        roles_data.append({
            "role": r,
            "summary": summary,
            "perms_json": json.dumps(perms_map),
            "is_full_admin": is_full_admin,
            "member_count": len(assigned_members),
            "members": assigned_members,
            "members_json": json.dumps(assigned_members),
        })

    modules = [
        {"key": "products", "name": "Products", "icon": "fa-box-open", "desc": "Product catalog, items, specifications & MOQ"},
        {"key": "find_buyers", "name": "Find vendors", "icon": "fa-crosshairs", "desc": "AI vendor discovery & targeted web scraping"},
        {"key": "leads", "name": "Vendor list", "icon": "fa-users-viewfinder", "desc": "Discovered enterprise vendors & fit scoring"},
        {"key": "saved_buyers", "name": "Prequalified Vendors", "icon": "fa-bookmark", "desc": "Prequalified vendor pipeline, notes, and assignments"},
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


SELLER_MODULES_CONFIG = [
    {"key": "products", "name": "Products", "icon": "fa-box-open", "desc": "Product catalog, items, specifications & MOQ"},
    {"key": "find_buyers", "name": "Find vendors", "icon": "fa-crosshairs", "desc": "AI vendor discovery & targeted web scraping"},
    {"key": "leads", "name": "Vendor list", "icon": "fa-users-viewfinder", "desc": "Discovered enterprise vendors & fit scoring"},
    {"key": "saved_buyers", "name": "Prequalified Vendors", "icon": "fa-bookmark", "desc": "Prequalified vendor pipeline, notes, and assignments"},
    {"key": "inquiries", "name": "Buyer Inquiries", "icon": "fa-paper-plane", "desc": "Commercial RFQs, proposals, and quote capture"},
    {"key": "export_reports", "name": "Export Reports", "icon": "fa-download", "desc": "CSV downloads & catalog matrix exports"},
    {"key": "matching_parameters", "name": "Matching Parameters", "icon": "fa-sliders", "desc": "AI matching score criteria & rules"},
    {"key": "team", "name": "User Management", "icon": "fa-users-gear", "desc": "Employee management & user assignments"},
    {"key": "billing", "name": "Subscription & Credits", "icon": "fa-coins", "desc": "Plan status and AI credits allocation"},
    {"key": "requirements", "name": "Requirements", "icon": "fa-clipboard-list", "desc": "Procurement requisitions & requests"},
    {"key": "find_suppliers", "name": "Find Suppliers", "icon": "fa-magnifying-glass-chart", "desc": "Supplier discovery & search"},
    {"key": "saved_suppliers", "name": "Saved Suppliers", "icon": "fa-bookmark", "desc": "Shortlisted supplier vendors"},
]


@login_required
def seller_role_create_view(request):
    """
    Creates a new custom role with matrix permissions.
    GET: Renders dedicated full-page role creation form.
    POST: Validates and saves role with configured module permissions.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company administrator privileges required.")
        return redirect("seller-roles-web")

    if request.method == "GET":
        modules_with_perms = []
        for mod in SELLER_MODULES_CONFIG:
            m_copy = dict(mod)
            m_copy.update({
                "can_read": False,
                "can_write": False,
                "can_edit": False,
                "can_delete": False,
                "can_admin": False,
            })
            modules_with_perms.append(m_copy)

        return render(request, "company/role_form.html", {
            "company": company,
            "rbac": rbac,
            "modules": modules_with_perms,
            "is_edit": False,
            "role": None,
            "page_title": "Add New Role",
        })

    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if not name:
        messages.error(request, "Role name is required.")
        return redirect("seller-role-create")

    if CompanyRole.objects.filter(company=company, name__iexact=name).exists():
        messages.error(request, f"A role named '{name}' already exists in your company.")
        return redirect("seller-role-create")

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
    GET: Renders dedicated full-page role edit form with pre-populated permissions.
    POST: Updates role identity and saves modified permissions.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["is_company_admin"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company administrator privileges required.")
        return redirect("seller-roles-web")

    role = get_object_or_404(CompanyRole, id=role_id, company=company)

    if request.method == "GET":
        perms_map = {
            mp.module: mp
            for mp in role.module_permissions.all()
        }
        modules_with_perms = []
        for mod in SELLER_MODULES_CONFIG:
            m_copy = dict(mod)
            mp = perms_map.get(mod["key"])
            m_copy.update({
                "can_read": mp.can_read if mp else False,
                "can_write": mp.can_write if mp else False,
                "can_edit": mp.can_edit if mp else False,
                "can_delete": mp.can_delete if mp else False,
                "can_admin": mp.can_admin if mp else False,
            })
            modules_with_perms.append(m_copy)

        return render(request, "company/role_form.html", {
            "company": company,
            "rbac": rbac,
            "modules": modules_with_perms,
            "is_edit": True,
            "role": role,
            "page_title": f"Edit Role: {role.name}",
        })

    new_name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if new_name and not role.is_system:
        # Check duplicate name
        if CompanyRole.objects.filter(company=company, name__iexact=new_name).exclude(id=role.id).exists():
            messages.error(request, f"Another role named '{new_name}' already exists.")
            return redirect("seller-role-edit", role_id=role.id)
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
    fallback_role_id = request.POST.get("fallback_role_id")
    fallback_role = None
    if fallback_role_id:
        fallback_role = CompanyRole.objects.filter(company=company, id=fallback_role_id).exclude(id=role.id).first()

    for m in role.members.all():
        if fallback_role:
            m.custom_role = fallback_role
            m.save(update_fields=["custom_role"])
            sync_member_permissions_from_role(m, fallback_role)
        else:
            # Safe default: Unassign custom role and revert to standard USER, never escalate to Admin!
            m.custom_role = None
            m.role = CompanyMember.Role.USER
            m.save(update_fields=["role", "custom_role"])
            MemberPermission.objects.filter(member=m).delete()

    role.delete()
    messages.success(request, f"Role '{role_name}' deleted successfully.")
    return redirect("seller-roles-web")

