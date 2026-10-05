from django.urls import path
from .views import (
    LeadListAPIView,
    InquiryListCreateAPIView,
)

urlpatterns = [
    path("", LeadListAPIView.as_view(), name="api-leads-list"),
    path("inquiries/", InquiryListCreateAPIView.as_view(), name="api-inquiries-list"),
]
