from django.urls import path
from .views import (
    dashboard_view,
    DashboardAPIView,
    switch_company_view,
    demo_login_as_view,
    global_search_view,
    notifications_list_api,
    mark_notification_read_view,
    mark_all_notifications_read_view,
    clear_all_notifications_view,
)

urlpatterns = [
    path("", dashboard_view, name="dashboard"),
    path("api/dashboard/", DashboardAPIView.as_view(), name="api-dashboard"),
    path("switch-company/", switch_company_view, name="switch-company"),
    path("demo-role/<str:role_name>/", demo_login_as_view, name="demo-login-as"),
    path("demo-login/<str:role_name>/", demo_login_as_view, name="demo-login-as-alias"),
    path("search/", global_search_view, name="global-search"),

    # Real-Time Notification Center Endpoints
    path("notifications/", notifications_list_api, name="notifications-list-api"),
    path("notifications/<int:pk>/read/", mark_notification_read_view, name="notification-mark-read"),
    path("notifications/read-all/", mark_all_notifications_read_view, name="notifications-mark-all-read"),
    path("notifications/clear-all/", clear_all_notifications_view, name="notifications-clear-all"),
]

