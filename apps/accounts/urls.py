from django.urls import path

from .views import (
    RegisterAPIView,
    MeAPIView,
    VerifyEmailAPIView,
)

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .views import RegisterAPIView


urlpatterns = [

    path(
        "register/",
        RegisterAPIView.as_view(),
        name="register"
    ),


    path(
        "login/",
        TokenObtainPairView.as_view(),
        name="login"
    ),

    path(
        "token/refresh/",
        TokenRefreshView.as_view(),
        name="token-refresh"
    ),

    path(
    "me/",
    MeAPIView.as_view(),
    name="me"
    ),
    path(
    "verify-email/",
    VerifyEmailAPIView.as_view(),
    name="verify-email",
    ),
]



