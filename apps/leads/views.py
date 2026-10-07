from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SavedItem, Inquiry
from .serializers import SavedItemSerializer, InquirySerializer
from .services.email_service import send_inquiry_email
from apps.companies.rbac import (
    IsSellerCompany,
    HasModulePermission,
    get_user_rbac_context,
)


class LeadListAPIView(APIView):
    """
    Seller REST API:
    - GET: List company's leads (requires READ permission)
    Enforces that the user belongs to a SELLER or BOTH company.
    """
    permission_classes = [IsAuthenticated, IsSellerCompany, HasModulePermission]
    required_module = "leads"

    def get(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")

        if rbac["is_super_admin"] and not company:
            leads = SavedItem.objects.all().select_related("search_result")
        else:
            leads = SavedItem.objects.filter(company=company).select_related("search_result")

        serializer = SavedItemSerializer(leads, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class InquiryListCreateAPIView(APIView):
    """
    Inquiries REST API (Available to Buyer and Seller):
    - GET: List inquiries
    - POST: Create and dispatch inquiry
    """
    permission_classes = [IsAuthenticated, HasModulePermission]
    required_module = "inquiries"

    def get(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")

        if rbac["is_super_admin"] and not company:
            inquiries = Inquiry.objects.all().select_related("search_result")
        else:
            inquiries = Inquiry.objects.filter(company=company).select_related("search_result")

        serializer = InquirySerializer(inquiries, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        if not company:
            return Response({"error": "Active company context required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = InquirySerializer(data=request.data)
        if serializer.is_valid():
            inquiry = serializer.save(company=company)
            send_inquiry_email(inquiry, user=request.user)
            return Response(InquirySerializer(inquiry).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class InquiryDetailAPIView(APIView):
    """
    Inquiry Detail REST API:
    - GET: Retrieve inquiry details
    - PATCH/PUT: Update inquiry status or message
    - DELETE: Remove inquiry
    """
    permission_classes = [IsAuthenticated, HasModulePermission]
    required_module = "inquiries"

    def get(self, request, pk):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        inquiry = get_object_or_404(Inquiry, pk=pk, company=company)
        serializer = InquirySerializer(inquiry)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        inquiry = get_object_or_404(Inquiry, pk=pk, company=company)
        serializer = InquirySerializer(inquiry, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        inquiry = get_object_or_404(Inquiry, pk=pk, company=company)
        inquiry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
