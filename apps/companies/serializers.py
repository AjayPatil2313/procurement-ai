from rest_framework import serializers

from .models import (
    Company,
    CompanyMember,
    CompanyPermission,
    MemberPermission,
)
from apps.accounts.models import User

class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "legal_name",
            "company_type",
            "registration_no",
            "gst_vat_no",
            "industry",
            "company_size",
            "website",
            "email",
            "phone",
            "about",
            "description",
            "logo",
            "address_line1",
            "address_line2",
            "address",
            "city",
            "state",
            "country",
            "postal_code",
            "latitude",
            "longitude",
            "preferred_currency",
            "is_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "company_type",
            "is_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]

from rest_framework import serializers

from .models import Company, CompanyMember
from apps.accounts.models import User


class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "legal_name",
            "company_type",
            "registration_no",
            "gst_vat_no",
            "industry",
            "company_size",
            "website",
            "email",
            "phone",
            "about",
            "description",
            "logo",
            "address_line1",
            "address_line2",
            "address",
            "city",
            "state",
            "country",
            "postal_code",
            "latitude",
            "longitude",
            "preferred_currency",
            "is_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "company_type",
            "is_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]


class CreateCompanyUserSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(
        write_only=True,
        min_length=6,
        max_length=128,
    )
    first_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        default="",
    )
    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        default="",
    )
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
        default="",
    )
    is_email_verified = serializers.BooleanField(
        required=False,
        default=True,
    )
    is_active = serializers.BooleanField(
        required=False,
        default=True,
    )

    role = serializers.ChoiceField(
        choices=CompanyMember.Role.choices,
        required=False,
        default=CompanyMember.Role.USER,
    )
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )

    def validate_email(self, value):
        value = value.lower()

        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )

        return value

    def create(self, validated_data):
        company = self.context["company"]
        role = validated_data.get("role", CompanyMember.Role.USER)

        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=validated_data.get("phone", ""),
            is_email_verified=validated_data.get("is_email_verified", True),
            is_active=validated_data.get("is_active", True),
        )

        member = CompanyMember.objects.create(
            company=company,
            user=user,
            role=role,
            is_active=validated_data.get("is_active", True),
        )

        # Assign initial permissions if provided
        permissions_list = validated_data.get("permissions", [])
        if role == CompanyMember.Role.USER:
            if permissions_list:
                for code in permissions_list:
                    code_clean = code.strip().upper()
                    perm_obj, _ = CompanyPermission.objects.get_or_create(
                        module="general",
                        permission=code_clean,
                    )
                    MemberPermission.objects.get_or_create(member=member, permission=perm_obj)
            else:
                read_perm, _ = CompanyPermission.objects.get_or_create(module="general", permission="READ")
                MemberPermission.objects.get_or_create(member=member, permission=read_perm)

        return user

class UpdateCompanyUserSerializer(serializers.Serializer):
    first_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
    )
    password = serializers.CharField(
        required=False,
        write_only=True,
        min_length=6,
        max_length=128,
    )
    is_active = serializers.BooleanField(
        required=False,
    )
    is_email_verified = serializers.BooleanField(
        required=False,
    )
    role = serializers.ChoiceField(
        choices=CompanyMember.Role.choices,
        required=False,
    )

class CompanyMemberSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    is_email_verified = serializers.BooleanField(source="user.is_email_verified", read_only=True)

    class Meta:
        model = CompanyMember
        fields = [
            "id",
            "user_id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "is_email_verified",
            "role",
            "is_active",
            "joined_at",
        ]
        read_only_fields = fields

class PermissionAssignmentSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    permissions = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )
    permission_codes = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    module_permissions = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        default=dict,
    )

    def validate_user_id(self, value):
        try:
            member = CompanyMember.objects.get(
                user_id=value,
                company=self.context["company"],
                is_active=True,
            )
        except CompanyMember.DoesNotExist:
            raise serializers.ValidationError(
                "User does not belong to this company."
            )

        return value

    def validate_permissions(self, value):
        if not value:
            return value

        valid_permissions = set(
            CompanyPermission.objects.filter(
                id__in=value
            ).values_list("id", flat=True)
        )

        invalid_permissions = set(value) - valid_permissions

        if invalid_permissions:
            raise serializers.ValidationError(
                f"Invalid permission IDs: {list(invalid_permissions)}"
            )

        return value