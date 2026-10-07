from django.urls import path
from .views import (
    LeadListAPIView,
    InquiryListCreateAPIView,
    InquiryDetailAPIView,
)

urlpatterns = [
    path("", LeadListAPIView.as_view(), name="api-leads-list"),
    path("inquiries/", InquiryListCreateAPIView.as_view(), name="api-inquiries-list"),
    path("inquiries/<int:pk>/", InquiryDetailAPIView.as_view(), name="api-inquiry-detail"),
]
