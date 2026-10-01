from django.db import transaction
from rest_framework import serializers

from .models import User
from apps.companies.models import Company, CompanyMember


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=8
    )

    company_name = serializers.CharField(write_only=True)

    company_type = serializers.ChoiceField(
        choices=Company.CompanyType.choices,
        write_only=True
    )

    industry = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True
    )

    country = serializers.CharField(
        write_only=True,
        required=False,
        default="India"
    )

    class Meta:
        model = User
        fields = [
            "email",
            "password",
            "first_name",
            "last_name",
            "phone",
            "company_name",
            "company_type",
            "industry",
            "country",
        ]

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )

        return value.lower()

    @transaction.atomic
    def create(self, validated_data):
        company_name = validated_data.pop("company_name")
        company_type = validated_data.pop("company_type")
        industry = validated_data.pop("industry", "")
        country = validated_data.pop("country", "India")
        password = validated_data.pop("password")

        user = User.objects.create_user(
            password=password,
            **validated_data
        )

        company = Company.objects.create(
            name=company_name,
            company_type=company_type,
            industry=industry,
            country=country,
            created_by=user
        )

        CompanyMember.objects.create(
            company=company,
            user=user,
            role=CompanyMember.Role.ADMIN
        )

        return user


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()

    new_password = serializers.CharField(
        min_length=8,
        write_only=True,
    )