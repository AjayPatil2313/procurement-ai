from django.urls import path

from .views import (
    RegisterAPIView,
    MeAPIView,
    VerifyEmailAPIView,
    ForgotPasswordAPIView,
    ResetPasswordAPIView,
)

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)



urlpatterns = [

    path(
        "register/",
        RegisterAPIView.as_view(),
        name="api-register"
    ),

    path(
        "login/",
        TokenObtainPairView.as_view(),
        name="api-login"
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
    path(
        "forgot-password/",
        ForgotPasswordAPIView.as_view(),
        name="api-forgot-password",
    ),

    path(
        "reset-password/",
        ResetPasswordAPIView.as_view(),
        name="api-reset-password",
    ),
]



