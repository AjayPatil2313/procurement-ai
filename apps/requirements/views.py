from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Requirement
from .serializers import RequirementSerializer
from apps.companies.rbac import (
    IsBuyerCompany,
    HasModulePermission,
    get_user_rbac_context,
)


class RequirementListCreateAPIView(APIView):
    """
    Buyer REST API:
    - GET: List company's requirements (requires READ permission)
    - POST: Create new requirement (requires EDIT permission)
    Enforces that the user belongs to a BUYER or BOTH company.
    """
    permission_classes = [IsAuthenticated, IsBuyerCompany, HasModulePermission]
    required_module = "requirements"

    def get(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")

        if rbac["is_super_admin"] and not company:
            requirements = Requirement.objects.all().select_related("company", "category")
        else:
            requirements = Requirement.objects.filter(company=company).select_related("category")

        serializer = RequirementSerializer(requirements, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        if not company:
            return Response(
                {"error": "No active company found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = RequirementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        req = serializer.save(
            company=company,
            created_by=request.user,
        )
        return Response(
            RequirementSerializer(req).data,
            status=status.HTTP_201_CREATED,
        )


class RequirementDetailAPIView(APIView):
    """
    Buyer REST API for single requirement:
    - GET: Retrieve requirement (READ)
    - PUT/PATCH: Update requirement (UPDATE)
    - DELETE: Delete requirement (DELETE)
    """
    permission_classes = [IsAuthenticated, IsBuyerCompany, HasModulePermission]
    required_module = "requirements"

    def get_object(self, request, pk):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        if rbac["is_super_admin"]:
            return get_object_or_404(Requirement, pk=pk)
        return get_object_or_404(Requirement, pk=pk, company=company)

    def get(self, request, pk):
        req = self.get_object(request, pk)
        return Response(RequirementSerializer(req).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        req = self.get_object(request, pk)
        serializer = RequirementSerializer(req, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        req = self.get_object(request, pk)
        serializer = RequirementSerializer(req, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        req = self.get_object(request, pk)
        req.delete()
        return Response(
            {"message": "Requirement deleted successfully."},
            status=status.HTTP_204_NO_CONTENT,
        )
