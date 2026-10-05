from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db import models
from .models import (
    Company,
    CompanyMember,
    CompanyPermission,
    MemberPermission,
)
from .serializers import (
    CompanyProfileSerializer,
    CreateCompanyUserSerializer,
    UpdateCompanyUserSerializer,
    CompanyMemberSerializer,
    PermissionAssignmentSerializer,
)

class CompanyProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_membership(self, company_id, user):
        return get_object_or_404(
            CompanyMember.objects.select_related("company"),
            company_id=company_id,
            user=user,
            is_active=True,
            company__is_active=True,
        )

    def get(self, request, company_id):
        membership = self.get_membership(
            company_id,
            request.user,
        )

        serializer = CompanyProfileSerializer(
            membership.company
        )

        return Response(serializer.data)

    def patch(self, request, company_id):
        membership = self.get_membership(
            company_id,
            request.user,
        )

        if membership.role != CompanyMember.Role.ADMIN:
            return Response(
                {
                    "error": (
                        "Only Company Admin can update "
                        "the company profile."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CompanyProfileSerializer(
            membership.company,
            data=request.data,
            partial=True,
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response({
            "message": "Company profile updated successfully.",
            "company": serializer.data,
        })

class CreateCompanyUserAPIView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        try:
            membership = (
                CompanyMember.objects
                .select_related("company")
                .get(
                    user=request.user,
                    is_active=True,
                    company__is_active=True,
                )
            )
        except CompanyMember.DoesNotExist:
            return Response(
                {"error": "You are not a member of any active company."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if membership.role != CompanyMember.Role.ADMIN:
            return Response(
                {"error": "Only Company Admin can create users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CreateCompanyUserSerializer(
            data=request.data,
            context={"company": membership.company},
        )

        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        return Response(
            {
                "message": "Company user created successfully.",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "phone": user.phone,
                    "is_email_verified": user.is_email_verified,
                    "is_active": user.is_active,
                    "role": CompanyMember.Role.USER,
                    "company": {
                        "id": membership.company.id,
                        "name": membership.company.name,
                    },
                },
            },
            status=status.HTTP_201_CREATED,
        )
    
class CompanyUserListAPIView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):

        try:
            membership = (
                CompanyMember.objects
                .select_related("company")
                .get(
                    user=request.user,
                    is_active=True,
                    company__is_active=True,
                )
            )
        except CompanyMember.DoesNotExist:
            return Response(
                {
                    "error": "You are not a member of any active company."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if membership.role != CompanyMember.Role.ADMIN:
            return Response(
                {
                    "error": "Only Company Admin can view company users."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        is_active_param = request.GET.get("is_active")
        q_param = request.GET.get("q", "").strip()

        qs = CompanyMember.objects.select_related("user").filter(
            company=membership.company
        )
        if is_active_param is not None:
            if is_active_param.lower() in ["true", "1"]:
                qs = qs.filter(is_active=True, user__is_active=True)
            elif is_active_param.lower() in ["false", "0"]:
                qs = qs.filter(models.Q(is_active=False) | models.Q(user__is_active=False))

        if q_param:
            qs = qs.filter(
                models.Q(user__email__icontains=q_param) |
                models.Q(user__first_name__icontains=q_param) |
                models.Q(user__last_name__icontains=q_param)
            )

        members = qs.order_by("-joined_at")

        serializer = CompanyMemberSerializer(
            members,
            many=True
        )

        return Response(
            {
                "company": {
                    "id": membership.company.id,
                    "name": membership.company.name,
                },
                "count": members.count(),
                "users": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

class CompanyUserDetailUpdateAPIView(APIView):
    """
    Retrieve, update (name, phone, role, password, active status), or deactivate a company user.
    """
    permission_classes = [IsAuthenticated]

    def get_membership_and_target(self, request, user_id):
        try:
            membership = CompanyMember.objects.select_related("company").get(
                user=request.user,
                is_active=True,
                company__is_active=True,
            )
        except CompanyMember.DoesNotExist:
            return None, None, Response({"error": "You are not a member of any active company."}, status=status.HTTP_403_FORBIDDEN)

        if membership.role != CompanyMember.Role.ADMIN:
            return None, None, Response({"error": "Only Company Admin can manage company users."}, status=status.HTTP_403_FORBIDDEN)

        try:
            target_member = CompanyMember.objects.select_related("user").get(
                user_id=user_id,
                company=membership.company,
            )
        except CompanyMember.DoesNotExist:
            return None, None, Response({"error": "User not found in this company."}, status=status.HTTP_404_NOT_FOUND)

        return membership, target_member, None

    def get(self, request, user_id):
        membership, target_member, error_resp = self.get_membership_and_target(request, user_id)
        if error_resp:
            return error_resp

        serializer = CompanyMemberSerializer(target_member)
        data = serializer.data
        perms = list(MemberPermission.objects.filter(member=target_member).values_list("permission__permission", flat=True).distinct())
        data["permissions"] = perms
        return Response(data, status=status.HTTP_200_OK)

    def patch(self, request, user_id):
        return self.put(request, user_id)

    def put(self, request, user_id):
        membership, target_member, error_resp = self.get_membership_and_target(request, user_id)
        if error_resp:
            return error_resp

        serializer = UpdateCompanyUserSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        val_data = serializer.validated_data

        if "is_active" in val_data:
            if val_data["is_active"] is False and user_id == request.user.id:
                return Response({"error": "You cannot deactivate your own account."}, status=status.HTTP_400_BAD_REQUEST)
            target_member.is_active = val_data["is_active"]
            target_member.user.is_active = val_data["is_active"]

        if "first_name" in val_data:
            target_member.user.first_name = val_data["first_name"]
        if "last_name" in val_data:
            target_member.user.last_name = val_data["last_name"]
        if "phone" in val_data:
            target_member.user.phone = val_data["phone"]
        if "is_email_verified" in val_data:
            target_member.user.is_email_verified = val_data["is_email_verified"]
        if "password" in val_data and val_data["password"]:
            target_member.user.set_password(val_data["password"])
        if "role" in val_data and user_id != request.user.id:
            target_member.role = val_data["role"]

        target_member.user.save()
        target_member.save()

        updated_serializer = CompanyMemberSerializer(target_member)
        return Response({
            "message": "User updated successfully.",
            "user": updated_serializer.data,
        }, status=status.HTTP_200_OK)

    def delete(self, request, user_id):
        membership, target_member, error_resp = self.get_membership_and_target(request, user_id)
        if error_resp:
            return error_resp

        if user_id == request.user.id:
            return Response({"error": "You cannot delete or deactivate your own account."}, status=status.HTTP_400_BAD_REQUEST)

        target_member.is_active = False
        target_member.user.is_active = False
        target_member.user.save()
        target_member.save()

        return Response({"message": f"User '{target_member.user.email}' has been deactivated."}, status=status.HTTP_200_OK)

class AssignPermissionsAPIView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        try:
            membership = (
                CompanyMember.objects
                .select_related("company")
                .get(
                    user=request.user,
                    is_active=True,
                    company__is_active=True,
                )
            )
        except CompanyMember.DoesNotExist:
            return Response(
                {
                    "error": "You are not a member of any active company."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if membership.role != CompanyMember.Role.ADMIN:
            return Response(
                {
                    "error": "Only Company Admin can assign permissions."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PermissionAssignmentSerializer(
            data=request.data,
            context={
                "company": membership.company
            },
        )

        serializer.is_valid(raise_exception=True)

        user_id = serializer.validated_data["user_id"]
        permission_ids = serializer.validated_data.get("permissions", [])
        permission_codes = serializer.validated_data.get("permission_codes", [])
        module_permissions = serializer.validated_data.get("module_permissions", {})

        target_member = CompanyMember.objects.get(
            user_id=user_id,
            company=membership.company,
            is_active=True,
        )

        MemberPermission.objects.filter(
            member=target_member
        ).delete()

        permission_objects = list(CompanyPermission.objects.filter(
            id__in=permission_ids
        ))

        # Support assigning permissions via code list e.g. ["READ", "EDIT"]
        for code in permission_codes:
            code_upper = code.strip().upper()
            perm_obj, _ = CompanyPermission.objects.get_or_create(
                module="general",
                permission=code_upper,
            )
            if perm_obj not in permission_objects:
                permission_objects.append(perm_obj)

        # Support assigning permissions via module map e.g. {"requirements": ["READ", "EDIT"]}
        for mod, acts in module_permissions.items():
            for act in acts:
                act_upper = act.strip().upper()
                perm_obj, _ = CompanyPermission.objects.get_or_create(
                    module=mod.strip().lower(),
                    permission=act_upper,
                )
                if perm_obj not in permission_objects:
                    permission_objects.append(perm_obj)

        MemberPermission.objects.bulk_create(
            [
                MemberPermission(
                    member=target_member,
                    permission=permission,
                )
                for permission in permission_objects
            ]
        )

        return Response(
            {
                "message": "Permissions assigned successfully.",
                "user": {
                    "id": target_member.user.id,
                    "email": target_member.user.email,
                },
                "permissions": [
                    {
                        "id": permission.id,
                        "module": permission.module,
                        "permission": permission.permission,
                    }
                    for permission in permission_objects
                ],
            },
            status=status.HTTP_200_OK,
        )

class UserPermissionsAPIView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):

        try:
            admin_membership = (
                CompanyMember.objects
                .select_related("company")
                .get(
                    user=request.user,
                    is_active=True,
                    company__is_active=True,
                )
            )
        except CompanyMember.DoesNotExist:
            return Response(
                {
                    "error": "You are not a member of any active company."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Only Company Admin can view permissions
        if admin_membership.role != CompanyMember.Role.ADMIN:
            return Response(
                {
                    "error": "Only Company Admin can view user permissions."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Target user must belong to same company
        try:
            target_member = (
                CompanyMember.objects
                .select_related("user")
                .get(
                    user_id=user_id,
                    company=admin_membership.company,
                    is_active=True,
                )
            )
        except CompanyMember.DoesNotExist:
            return Response(
                {
                    "error": "User does not belong to this company."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get all available permissions
        all_permissions = CompanyPermission.objects.all().order_by(
            "module",
            "id",
        )

        # Get permissions assigned to target user
        assigned_permission_ids = set(
            MemberPermission.objects.filter(
                member=target_member
            ).values_list(
                "permission_id",
                flat=True,
            )
        )

        modules = {}

        for permission in all_permissions:

            if permission.module not in modules:
                modules[permission.module] = []

            modules[permission.module].append(
                {
                    "id": permission.id,
                    "permission": permission.permission,
                    "assigned": permission.id in assigned_permission_ids,
                }
            )

        return Response(
            {
                "user": {
                    "id": target_member.user.id,
                    "email": target_member.user.email,
                    "first_name": target_member.user.first_name,
                    "last_name": target_member.user.last_name,
                    "role": target_member.role,
                },
                "modules": modules,
            },
            status=status.HTTP_200_OK,
        )


class CompanyModulesAPIView(APIView):
    """
    Returns dynamically allowed modules and navigation based on:
    1. Company Type (BUYER, SELLER, BOTH)
    2. User Role (ADMIN, USER)
    3. Assigned Granular Permissions (READ, EDIT, DELETE, UPDATE, IMPORT, EXPORT)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.companies.rbac import get_user_rbac_context

        company_id = request.query_params.get("company_id") or request.session.get("active_company_id")
        rbac = get_user_rbac_context(request.user, company_id=company_id)
        company = rbac.get("company")

        if not company and not rbac.get("is_super_admin"):
            return Response(
                {"error": "No active company found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "company": {
                "id": company.id if company else None,
                "name": company.name if company else "Super Admin View",
                "company_type": company.company_type if company else "BOTH",
            },
            "user": {
                "id": request.user.id,
                "email": request.user.email,
                "role": rbac.get("role"),
                "role_label": rbac.get("role_label"),
            },
            "company_type": rbac.get("company_type", company.company_type if company else "BOTH"),
            "can_view_buyer": rbac.get("can_view_buyer", False),
            "can_view_seller": rbac.get("can_view_seller", False),
            "is_buyer_only": rbac.get("is_buyer_only", False),
            "is_seller_only": rbac.get("is_seller_only", False),
            "is_both": rbac.get("is_both", False),
            "visible_modules": rbac.get("visible_modules", []),
            "navigation": rbac.get("navigation", {}),
            "permissions": sorted(list(rbac.get("permissions", []))),
        }, status=status.HTTP_200_OK)