from rest_framework import serializers

from .models import (
    Company,
    CompanyMember,
    CompanyPermission,
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
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        min_length=8
    )
    first_name = serializers.CharField(
        required=False,
        allow_blank=True
    )
    last_name = serializers.CharField(
        required=False,
        allow_blank=True
    )
    phone = serializers.CharField(
        required=False,
        allow_blank=True
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

        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone=validated_data.get("phone", ""),
        )

        CompanyMember.objects.create(
            company=company,
            user=user,
            role=CompanyMember.Role.USER,
            is_active=True,
        )

        return user

class CompanyMemberSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)

    class Meta:
        model = CompanyMember
        fields = [
            "id",
            "user_id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "is_active",
            "joined_at",
        ]
        read_only_fields = fields

class PermissionAssignmentSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    permissions = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
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