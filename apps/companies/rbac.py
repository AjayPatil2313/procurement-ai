from functools import wraps
from django.db import models
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from rest_framework.permissions import BasePermission
from apps.companies.models import CompanyMember, MemberPermission, Company, CompanyPermission


class IsSuperAdmin(BasePermission):
    """Allows access only to superadmins / staff users."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and (request.user.is_superuser or request.user.is_staff))


class IsCompanyMember(BasePermission):
    """Allows access to active members of an active company."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return CompanyMember.objects.filter(
            user=request.user,
            is_active=True,
            company__is_active=True,
        ).exists()


class IsCompanyAdmin(BasePermission):
    """Allows access only to Company Admins (or superadmins)."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return CompanyMember.objects.filter(
            user=request.user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
            company__is_active=True,
        ).exists()


class IsBuyerCompany(BasePermission):
    """Allows access only to companies that are BUYER or BOTH (or Super Admins)."""
    message = "Access restricted: This API is only available for Buyer accounts."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        rbac = get_user_rbac_context(request.user)
        return bool(rbac.get("can_view_buyer", False))


class IsSellerCompany(BasePermission):
    """Allows access only to companies that are SELLER or BOTH (or Super Admins)."""
    message = "Access restricted: This API is only available for Seller accounts."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        rbac = get_user_rbac_context(request.user)
        return bool(rbac.get("can_view_seller", False))


class HasModulePermission(BasePermission):
    """
    Granular RBAC permission check:
    - Superusers: always allowed
    - Checks company_type eligibility for module:
        * Buyer modules (requirements, suppliers) require BUYER or BOTH
        * Seller modules (products, leads) require SELLER or BOTH
    - Company Admins: allowed for all modules permitted to their company type
    - Company Users: checks MemberPermission for required_module & required_permission
    """
    BUYER_MODULES = {"requirements", "procurement", "suppliers"}
    SELLER_MODULES = {"catalog", "products", "leads", "sales"}

    METHOD_PERMISSION_MAP = {
        "GET": "READ",
        "HEAD": "READ",
        "OPTIONS": "READ",
        "POST": "EDIT",
        "PUT": "UPDATE",
        "PATCH": "UPDATE",
        "DELETE": "DELETE",
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        membership = getattr(request, "current_membership", None)
        if not membership:
            try:
                membership = CompanyMember.objects.select_related("company").get(
                    user=request.user,
                    is_active=True,
                    company__is_active=True,
                )
                request.current_membership = membership
                request.current_company = membership.company
            except CompanyMember.DoesNotExist:
                return False

        company = membership.company
        required_module = getattr(view, "required_module", None)

        # 1. Company-level type restriction
        if required_module:
            if required_module in self.BUYER_MODULES and company.company_type == Company.CompanyType.SELLER:
                self.message = "Access restricted: This module is only available for Buyer accounts."
                return False
            if required_module in self.SELLER_MODULES and company.company_type == Company.CompanyType.BUYER:
                self.message = "Access restricted: This module is only available for Seller accounts."
                return False

        # 2. Company Admin has full access to all company-allowed modules
        if membership.role == CompanyMember.Role.ADMIN:
            return True

        # 3. Company User: check assigned granular permission
        required_permission = getattr(view, "required_permission", None)
        if not required_permission:
            required_permission = self.METHOD_PERMISSION_MAP.get(request.method, "READ")

        query = models.Q(permission__permission=required_permission) & (
            (models.Q(permission__module=required_module) | models.Q(permission__module="general"))
            if required_module
            else models.Q()
        )

        has_perm = MemberPermission.objects.filter(
            models.Q(member=membership) & query
        ).exists()

        if not has_perm:
            self.message = f"You do not have '{required_permission}' permission for module '{required_module or 'general'}'."
        return has_perm


def ensure_default_permissions():
    """
    Ensures standard 6 permissions exist for all modules in CompanyPermission.
    """
    modules = ["requirements", "products", "leads", "search", "categories", "dashboard", "general"]
    actions = ["READ", "EDIT", "DELETE", "UPDATE", "IMPORT", "EXPORT"]
    for mod in modules:
        for act in actions:
            CompanyPermission.objects.get_or_create(module=mod, permission=act)


def get_user_rbac_context(user, company_id=None):
    """
    Helper to calculate the RBAC context for a user, including:
    - role
    - company
    - membership
    - permissions map
    - module visibility
    - navigation structure
    """
    if not user or not user.is_authenticated:
        return {
            "is_authenticated": False,
            "role": None,
            "is_super_admin": False,
            "is_company_admin": False,
            "is_company_user": False,
            "company": None,
            "membership": None,
            "permissions": set(),
            "can_view_buyer": False,
            "can_view_seller": False,
            "is_buyer_only": False,
            "is_seller_only": False,
            "is_both": False,
            "visible_modules": [],
            "navigation": {},
        }

    is_super_admin = bool(user.is_superuser or user.is_staff)

    membership = None
    company = None

    if company_id:
        if is_super_admin:
            company = Company.objects.filter(id=company_id, is_active=True).first()
        else:
            membership = CompanyMember.objects.select_related("company", "custom_role").filter(
                user=user,
                company_id=company_id,
                is_active=True,
                company__is_active=True,
            ).first()
            if membership:
                company = membership.company

    if not company:
        # Default to first company membership
        membership = CompanyMember.objects.select_related("company", "custom_role").filter(
            user=user,
            is_active=True,
            company__is_active=True,
        ).first()
        if membership:
            company = membership.company
        elif is_super_admin:
            company = Company.objects.filter(is_active=True).first()

    role = "GUEST"
    role_label = "Guest"
    is_company_admin = False
    is_company_user = False
    permissions_set = set()

    if is_super_admin:
        role = "SUPER_ADMIN"
        role_label = "Super Admin"
        is_company_admin = True
        permissions_set = {"READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"}
    elif membership:
        is_admin_flag = (
            membership.role == CompanyMember.Role.ADMIN
            or (membership.custom_role and (membership.custom_role.is_system or membership.custom_role.name.lower() == "admin"))
        )
        if is_admin_flag:
            role = "COMPANY_ADMIN"
            role_label = membership.custom_role.name if membership.custom_role else "Company Admin"
            is_company_admin = True
            # Company Admin gets all 6 permissions automatically
            permissions_set = {"READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"}
        else:
            role = "COMPANY_USER"
            is_company_user = True
            # Load user specific permissions
            perms = MemberPermission.objects.filter(member=membership).select_related("permission")
            for p in perms:
                permissions_set.add(p.permission.permission)
                permissions_set.add(f"{p.permission.module}:{p.permission.permission}")
                permissions_set.add(f"{p.permission.module}:*")

            if membership.custom_role:
                role_label = membership.custom_role.name
            else:
                # Determine specific role label (Buyer, Seller, or Company User)
                has_buyer_perm = any(p.startswith("requirements:") for p in permissions_set)
                has_seller_perm = any(p.startswith("products:") or p.startswith("leads:") for p in permissions_set)
                if has_buyer_perm and not has_seller_perm:
                    role_label = "Buyer"
                elif has_seller_perm and not has_buyer_perm:
                    role_label = "Seller"
                elif company and company.company_type == Company.CompanyType.BUYER:
                    role_label = "Buyer"
                elif company and company.company_type == Company.CompanyType.SELLER:
                    role_label = "Seller"
                else:
                    role_label = "Company User"

    # Scope module visibility strictly by company type and permissions
    company_type = company.company_type if company else "BOTH"
    is_buyer_company = (company_type in [Company.CompanyType.BUYER, Company.CompanyType.BOTH]) or is_super_admin
    is_seller_company = (company_type in [Company.CompanyType.SELLER, Company.CompanyType.BOTH]) or is_super_admin

    is_buyer_only = bool(company and company.company_type == Company.CompanyType.BUYER and not is_super_admin)
    is_seller_only = bool(company and company.company_type == Company.CompanyType.SELLER and not is_super_admin)
    is_both = bool((company and company.company_type == Company.CompanyType.BOTH) or is_super_admin)

    can_view_buyer = is_buyer_company and (
        is_company_admin or "READ" in permissions_set or "requirements:READ" in permissions_set or "search:READ" in permissions_set
    )
    can_view_seller = is_seller_company and (
        is_company_admin or "READ" in permissions_set or "products:READ" in permissions_set or "leads:READ" in permissions_set
    )

    # Build visible modules list
    visible_modules = []
    if can_view_buyer:
        visible_modules.extend([
            "buyer_dashboard",
            "my_requirements",
            "find_suppliers",
            "supplier_search",
            "saved_suppliers",
            "inquiries",
        ])
    if can_view_seller:
        visible_modules.extend([
            "seller_dashboard",
            "my_products",
            "find_buyers",
            "buyer_leads",
            "saved_buyers",
            "inquiries",
        ])
    if is_company_admin or is_super_admin:
        visible_modules.extend([
            "company_profile",
            "team_members",
            "billing",
            "permissions",
        ])

    # Build hierarchical navigation representation
    navigation = {}
    if is_buyer_only:
        navigation = {
            "title": "Buyer Dashboard",
            "modules": [
                {"name": "Buyer Dashboard", "url": "/dashboard/", "icon": "fa-house"},
                {"name": "My Requirements", "url": "/procurement/requirements/", "icon": "fa-clipboard-list"},
                {"name": "Find Suppliers", "url": "/procurement/find-suppliers/", "icon": "fa-magnifying-glass-chart"},
                {"name": "Supplier Search", "url": "/procurement/find-suppliers/", "icon": "fa-magnifying-glass"},
                {"name": "Saved Suppliers", "url": "/procurement/saved-suppliers/", "icon": "fa-bookmark"},
                {"name": "Inquiries", "url": "/procurement/inquiries/", "icon": "fa-paper-plane"},
            ],
        }
    elif is_seller_only:
        seller_mods = [
            {"name": "Seller Dashboard", "url": "/dashboard/", "icon": "fa-house"},
            {"name": "My Products", "url": "/sales/products/", "icon": "fa-box-open"},
            {"name": "Find Buyers", "url": "/sales/find-buyers/", "icon": "fa-crosshairs"},
            {"name": "Buyer Leads", "url": "/sales/leads/", "icon": "fa-users-viewfinder"},
            {"name": "Saved Buyers", "url": "/sales/saved-leads/", "icon": "fa-bookmark"},
            {"name": "Inquiries", "url": "/sales/inquiries/", "icon": "fa-paper-plane"},
        ]
        if is_company_admin or is_super_admin:
            seller_mods.append({"name": "Roles & Responsibilities", "url": "/sales/roles/", "icon": "fa-user-shield"})
        navigation = {
            "title": "Seller Dashboard",
            "modules": seller_mods,
        }
    else:  # BOTH or Super Admin
        seller_mods = [
            {"name": "My Products", "url": "/sales/products/", "icon": "fa-box-open"},
            {"name": "Find Buyers", "url": "/sales/find-buyers/", "icon": "fa-crosshairs"},
            {"name": "Buyer Leads", "url": "/sales/leads/", "icon": "fa-users-viewfinder"},
            {"name": "Saved Buyers", "url": "/sales/saved-leads/", "icon": "fa-bookmark"},
            {"name": "Buyer Inquiries", "url": "/sales/inquiries/", "icon": "fa-paper-plane"},
        ]
        if is_company_admin or is_super_admin:
            seller_mods.append({"name": "Roles & Responsibilities", "url": "/sales/roles/", "icon": "fa-user-shield"})
        navigation = {
            "title": "Overview",
            "sections": [
                {
                    "title": "BUYER",
                    "modules": [
                        {"name": "My Requirements", "url": "/procurement/requirements/", "icon": "fa-clipboard-list"},
                        {"name": "Find Suppliers", "url": "/procurement/find-suppliers/", "icon": "fa-magnifying-glass-chart"},
                        {"name": "Saved Suppliers", "url": "/procurement/saved-suppliers/", "icon": "fa-bookmark"},
                        {"name": "Supplier Inquiries", "url": "/procurement/inquiries/", "icon": "fa-paper-plane"},
                    ],
                },
                {
                    "title": "SELLER",
                    "modules": seller_mods,
                },
            ],
        }

    return {
        "is_authenticated": True,
        "role": role,
        "role_label": role_label,
        "is_super_admin": is_super_admin,
        "is_company_admin": is_company_admin,
        "is_company_user": is_company_user,
        "company": company,
        "membership": membership,
        "permissions": permissions_set,
        "can_view_buyer": can_view_buyer,
        "can_view_seller": can_view_seller,
        "is_buyer_only": is_buyer_only,
        "is_seller_only": is_seller_only,
        "is_both": is_both,
        "visible_modules": visible_modules,
        "navigation": navigation,
    }


def superadmin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if not (request.user.is_superuser or request.user.is_staff):
            raise PermissionDenied("Super Admin access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def company_admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        rbac = get_user_rbac_context(request.user)
        if not rbac["is_company_admin"]:
            raise PermissionDenied("Company Admin access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def module_permission_required(module, permission_type="READ"):
    BUYER_MODULES = {"requirements", "procurement", "suppliers"}
    SELLER_MODULES = {"catalog", "products", "leads", "sales"}

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            rbac = get_user_rbac_context(request.user)
            if not rbac["is_super_admin"]:
                if module in BUYER_MODULES and not rbac["can_view_buyer"]:
                    raise PermissionDenied("Access restricted: This module is only available for Buyer accounts.")
                if module in SELLER_MODULES and not rbac["can_view_seller"]:
                    raise PermissionDenied("Access restricted: This module is only available for Seller accounts.")

            if rbac["is_company_admin"] or rbac["is_super_admin"]:
                return view_func(request, *args, **kwargs)

            perm_key = f"{module}:{permission_type}"
            wildcard_key = f"{module}:*"
            if (
                perm_key not in rbac["permissions"]
                and wildcard_key not in rbac["permissions"]
                and permission_type not in rbac["permissions"]
            ):
                raise PermissionDenied(f"Permission denied for module '{module}' ({permission_type}).")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
