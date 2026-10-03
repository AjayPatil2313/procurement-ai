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