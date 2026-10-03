from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Company, CompanyMember

from .serializers import (
    CompanyProfileSerializer,
    CreateCompanyUserSerializer,
    CompanyMemberSerializer,
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

        members = (
            CompanyMember.objects
            .select_related("user")
            .filter(
                company=membership.company,
                is_active=True,
            )
            .order_by("-joined_at")
        )

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