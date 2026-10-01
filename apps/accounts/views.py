from django.conf import settings
from django.core import signing
from django.core.mail import send_mail

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User
from .serializers import RegisterSerializer
from .utils import (
    generate_email_verification_token,
    verify_email_verification_token,
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

        memberships = user.company_memberships.select_related(
            "company"
        ).filter(
            is_active=True
        )

        companies = []

        for membership in memberships:
            companies.append({
                "company_id": membership.company.id,
                "company_name": membership.company.name,
                "company_type": membership.company.company_type,
                "role": membership.role,
            })

        return Response({
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "is_email_verified": user.is_email_verified,
            "is_superuser": user.is_superuser,
            "companies": companies,
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

