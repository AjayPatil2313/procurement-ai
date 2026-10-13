from django.urls import path
from apps.notifications.admin_views import (
    admin_email_overview_view,
    admin_email_templates_view,
    admin_email_template_edit_view,
    admin_email_template_preview_api,
    admin_email_template_test_send_view,
    admin_email_triggers_view,
    admin_email_trigger_toggle_view,
    admin_email_smtp_view,
    admin_email_smtp_test_view,
    admin_email_logs_view,
    admin_email_log_detail_api,
    admin_email_log_retry_view,
    admin_email_logs_retry_all_view,
    admin_email_alerts_view,
)
from apps.notifications.user_views import (
    user_notification_preferences_view,
    public_unsubscribe_view,
)

urlpatterns = [
    # Super Admin Email Operations & Governance
    path("admin-panel/email/", admin_email_overview_view, name="admin-panel-email-overview"),
    path("admin-panel/email/dashboard/", admin_email_overview_view, name="admin-panel-email-dashboard"),
    path("admin-panel/email/templates/", admin_email_templates_view, name="admin-panel-email-templates"),
    path("admin-panel/email/templates/<int:pk>/edit/", admin_email_template_edit_view, name="admin-panel-email-template-edit"),
    path("admin-panel/email/templates/<int:pk>/preview/", admin_email_template_preview_api, name="admin-panel-email-template-preview"),
    path("admin-panel/email/templates/<int:pk>/test-send/", admin_email_template_test_send_view, name="admin-panel-email-template-test-send"),
    path("admin-panel/email/triggers/", admin_email_triggers_view, name="admin-panel-email-triggers"),
    path("admin-panel/email/triggers/<int:pk>/toggle/", admin_email_trigger_toggle_view, name="admin-panel-email-trigger-toggle"),
    path("admin-panel/email/smtp/", admin_email_smtp_view, name="admin-panel-email-smtp"),
    path("admin-panel/email/smtp/test/", admin_email_smtp_test_view, name="admin-panel-email-smtp-test"),
    path("admin-panel/email/logs/", admin_email_logs_view, name="admin-panel-email-logs"),
    path("admin-panel/email/logs/retry-all-failed/", admin_email_logs_retry_all_view, name="admin-panel-email-logs-retry-all"),
    path("admin-panel/email/logs/<int:pk>/detail/", admin_email_log_detail_api, name="admin-panel-email-log-detail"),
    path("admin-panel/email/logs/<int:pk>/retry/", admin_email_log_retry_view, name="admin-panel-email-log-retry"),
    path("admin-panel/email/alerts/", admin_email_alerts_view, name="admin-panel-email-alerts"),

    # Tenant User Preferences & 1-Click Unsubscribe (RFC 8058)
    path("company/notification-preferences/", user_notification_preferences_view, name="user-notification-preferences"),
    path("notifications/unsubscribe/", public_unsubscribe_view, name="notifications-unsubscribe"),
]
