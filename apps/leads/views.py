from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SavedItem, Inquiry
from .serializers import SavedItemSerializer, InquirySerializer
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
    - POST: Create inquiry
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
