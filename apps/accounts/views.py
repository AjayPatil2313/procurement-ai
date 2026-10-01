from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RegisterSerializer


class RegisterAPIView(APIView):

    permission_classes = [AllowAny]

    def post(self, request):

        serializer = RegisterSerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        return Response(
            {
                "message": "Registration successful.",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "is_email_verified": user.is_email_verified,
                }
            },
            status=status.HTTP_201_CREATED
        )


from rest_framework.permissions import IsAuthenticated


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
