from django.conf import settings
from django.core import signing
from django.core.mail import send_mail

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User
from apps.companies.models import CompanyMember

from .serializers import (
    RegisterSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
)

from .utils import (
    generate_email_verification_token,
    verify_email_verification_token,
    generate_password_reset_token,
    verify_password_reset_token,
)


class RegisterAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        token = generate_email_verification_token(user)

        verification_url = (
            f"http://127.0.0.1:8000/api/auth/verify-email/"
            f"?token={token}"
        )

        send_mail(
            subject="Verify your Procurement AI account",
            message=(
                f"Hello {user.first_name or 'User'},\n\n"
                f"Please verify your email address by opening this link:\n\n"
                f"{verification_url}\n\n"
                f"This verification link is valid for 24 hours."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )

        return Response(
            {
                "message": "Registration successful. "
                           "Please verify your email.",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "is_email_verified": user.is_email_verified,
                },
            },
            status=status.HTTP_201_CREATED,
        )



class MeAPIView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        try:
            membership = (
                user.company_membership
            )
        except CompanyMember.DoesNotExist:
            membership = None

        company = None

        if (
            membership
            and membership.is_active
            and membership.company.is_active
        ):
            company = {
                "id": membership.company.id,
                "name": membership.company.name,
                "type": membership.company.company_type,
                "role": membership.role,
            }

        return Response({
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "is_email_verified": user.is_email_verified,
            "is_superuser": user.is_superuser,
            "company": company,
        })

class VerifyEmailAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        token = request.query_params.get("token")

        if not token:
            return Response(
                {
                    "error": "Verification token is required."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = verify_email_verification_token(token)

        except signing.SignatureExpired:
            return Response(
                {
                    "error": "Verification link has expired."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except signing.BadSignature:
            return Response(
                {
                    "error": "Invalid verification link."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(
                id=data["user_id"],
                email=data["email"],
            )
        except User.DoesNotExist:
            return Response(
                {
                    "error": "User not found."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if user.is_email_verified:
            return Response(
                {
                    "message": "Email is already verified."
                },
                status=status.HTTP_200_OK,
            )

        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])

        return Response(
            {
                "message": "Email verified successfully.",
                "email": user.email,
                "is_email_verified": True,
            },
            status=status.HTTP_200_OK,
        )

class ForgotPasswordAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(
                email=email,
                is_active=True,
            )
        except User.DoesNotExist:
            return Response(
                {
                    "message": "If an account exists with this email, "
                               "a password reset link has been sent."
                },
                status=status.HTTP_200_OK,
            )

        token = generate_password_reset_token(user)

        reset_url = (
            "http://127.0.0.1:8000/api/auth/reset-password/"
            f"?token={token}"
        )

        send_mail(
            subject="Reset your Procurement AI password",
            message=(
                f"Hello {user.first_name or 'User'},\n\n"
                f"Use the following link to reset your password:\n\n"
                f"{reset_url}\n\n"
                f"This link is valid for 1 hour."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )

        return Response(
            {
                "message": "If an account exists with this email, "
                           "a password reset link has been sent."
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        new_password = serializer.validated_data["new_password"]

        try:
            data = verify_password_reset_token(token)

        except signing.SignatureExpired:
            return Response(
                {
                    "error": "Password reset link has expired."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except signing.BadSignature:
            return Response(
                {
                    "error": "Invalid password reset link."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(
                id=data["user_id"],
                email=data["email"],
                is_active=True,
            )
        except User.DoesNotExist:
            return Response(
                {
                    "error": "User not found."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {
                "message": "Password reset successfully."
            },
            status=status.HTTP_200_OK,
        )

