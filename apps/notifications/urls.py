from django.urls import path
from apps.notifications.admin_views import (
    admin_email_overview_view,
    admin_email_layout_view,
    admin_email_templates_view,
    admin_email_template_edit_view,
    admin_email_template_preview_api,
    admin_email_template_test_send_view,
    admin_email_triggers_view,
    admin_email_trigger_toggle_view,
    admin_email_trigger_edit_view,
    admin_email_trigger_manual_send_view,
    admin_email_trigger_preview_api,
    admin_email_trigger_test_send_api,
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
from apps.notifications.inbox_views import (
    notifications_inbox_view,
    notification_toggle_read_view,
    notification_archive_view,
    notification_restore_view,
    notifications_mark_all_read_web_view,
)
from apps.notifications.api_views import (
    NotificationListAPIView,
    NotificationUnreadCountAPIView,
    NotificationMarkReadAPIView,
    NotificationMarkUnreadAPIView,
    NotificationMarkAllReadAPIView,
    NotificationDeleteArchiveAPIView,
)

urlpatterns = [
    # Super Admin Email Operations & Governance
    path("admin-panel/email/", admin_email_overview_view, name="admin-panel-email-overview"),
    path("admin-panel/email/dashboard/", admin_email_overview_view, name="admin-panel-email-dashboard"),
    path("admin-panel/email/layout/", admin_email_layout_view, name="admin-panel-email-layout"),
    path("admin-panel/email/templates/", admin_email_templates_view, name="admin-panel-email-templates"),
    path("admin-panel/email/templates/<int:pk>/edit/", admin_email_template_edit_view, name="admin-panel-email-template-edit"),
    path("admin-panel/email/templates/<int:pk>/preview/", admin_email_template_preview_api, name="admin-panel-email-template-preview"),
    path("admin-panel/email/templates/<int:pk>/test-send/", admin_email_template_test_send_view, name="admin-panel-email-template-test-send"),
    path("admin-panel/email/triggers/", admin_email_triggers_view, name="admin-panel-email-triggers"),
    path("admin-panel/email/triggers/<int:pk>/toggle/", admin_email_trigger_toggle_view, name="admin-panel-email-trigger-toggle"),
    path("admin-panel/email/triggers/<int:pk>/edit/", admin_email_trigger_edit_view, name="admin-panel-email-trigger-edit"),
    path("admin-panel/email/triggers/<int:pk>/manual-send/", admin_email_trigger_manual_send_view, name="admin-panel-email-trigger-manual-send"),
    path("admin-panel/email/triggers/<int:pk>/preview/", admin_email_trigger_preview_api, name="admin-panel-email-trigger-preview"),
    path("admin-panel/email/triggers/<int:pk>/test-send/", admin_email_trigger_test_send_api, name="admin-panel-email-trigger-test-send"),
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

    # In-App Notification Center Web Views
    path("notifications/inbox/", notifications_inbox_view, name="notifications-inbox"),
    path("notifications/<int:pk>/toggle-read/", notification_toggle_read_view, name="notification-toggle-read"),
    path("notifications/<int:pk>/archive/", notification_archive_view, name="notification-archive"),
    path("notifications/<int:pk>/restore/", notification_restore_view, name="notification-restore"),
    path("notifications/mark-all-read/", notifications_mark_all_read_web_view, name="notifications-mark-all-read-web"),

    # REST APIs for Real-Time Polling & Actions
    path("api/notifications/", NotificationListAPIView.as_view(), name="api-notifications-list"),
    path("api/notifications/unread-count/", NotificationUnreadCountAPIView.as_view(), name="api-notifications-unread-count"),
    path("api/notifications/<int:pk>/mark-read/", NotificationMarkReadAPIView.as_view(), name="api-notification-mark-read"),
    path("api/notifications/<int:pk>/mark-unread/", NotificationMarkUnreadAPIView.as_view(), name="api-notification-mark-unread"),
    path("api/notifications/mark-all-read/", NotificationMarkAllReadAPIView.as_view(), name="api-notifications-mark-all-read"),
    path("api/notifications/<int:pk>/delete/", NotificationDeleteArchiveAPIView.as_view(), name="api-notification-delete"),
    path("api/notifications/<int:pk>/archive/", NotificationDeleteArchiveAPIView.as_view(), name="api-notification-archive"),
]
