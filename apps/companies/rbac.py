from functools import wraps
from django.db import models
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from rest_framework.permissions import BasePermission
from apps.companies.models import CompanyMember, MemberPermission, Company


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


class HasModulePermission(BasePermission):
    """
    Granular RBAC permission check:
    - Superusers: always allowed
    - Company Admins: always allowed for all company modules
    - Company Users: checks MemberPermission for required_module & required_permission
    """
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

        if membership.role == CompanyMember.Role.ADMIN:
            return True

        required_permission = getattr(view, "required_permission", None)
        if not required_permission:
            required_permission = self.METHOD_PERMISSION_MAP.get(request.method, "READ")

        required_module = getattr(view, "required_module", None)

        # Check core permission (e.g. READ, EDIT, UPDATE, DELETE, IMPORT, EXPORT)
        # or module-scoped permission (e.g. requirements:READ)
        query = models.Q(permission__permission=required_permission)
        if required_module:
            query = query | models.Q(
                permission__module=required_module,
                permission__permission=required_permission,
            )

        return MemberPermission.objects.filter(
            models.Q(member=membership) & query
        ).exists()


def get_user_rbac_context(user, company_id=None):
    """
    Helper to calculate the RBAC context for a user, including:
    - role
    - company
    - membership
    - permissions map
    - module visibility
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
        }

    is_super_admin = bool(user.is_superuser or user.is_staff)

    membership = None
    company = None

    if company_id:
        if is_super_admin:
            company = Company.objects.filter(id=company_id, is_active=True).first()
        else:
            membership = CompanyMember.objects.select_related("company").filter(
                user=user,
                company_id=company_id,
                is_active=True,
                company__is_active=True,
            ).first()
            if membership:
                company = membership.company

    if not company:
        # Default to first company membership
        membership = CompanyMember.objects.select_related("company").filter(
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
        if membership.role == CompanyMember.Role.ADMIN:
            role = "COMPANY_ADMIN"
            role_label = "Company Admin"
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

    # Scope module visibility by company type and permissions
    company_type = company.company_type if company else "BOTH"
    is_buyer_company = company_type in [Company.CompanyType.BUYER, Company.CompanyType.BOTH]
    is_seller_company = company_type in [Company.CompanyType.SELLER, Company.CompanyType.BOTH]

    can_view_buyer = is_buyer_company and (
        is_company_admin or "READ" in permissions_set or "requirements:READ" in permissions_set or "search:READ" in permissions_set
    )
    can_view_seller = is_seller_company and (
        is_company_admin or "READ" in permissions_set or "products:READ" in permissions_set or "leads:READ" in permissions_set
    )

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
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            rbac = get_user_rbac_context(request.user)
            if rbac["is_company_admin"]:
                return view_func(request, *args, **kwargs)
            perm_key = f"{module}:{permission_type}"
            wildcard_key = f"{module}:*"
            if perm_key not in rbac["permissions"] and wildcard_key not in rbac["permissions"]:
                raise PermissionDenied(f"Permission denied for module '{module}' ({permission_type}).")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
