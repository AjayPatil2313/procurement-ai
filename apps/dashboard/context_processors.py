from apps.dashboard.models import Notification, PlatformSetting


def notifications_context(request):
    """
    Supplies real-time unread count and latest notifications to all templates.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "unread_notifications_count": 0,
            "recent_notifications": [],
        }

    user_notifications = Notification.objects.filter(user=request.user)
    unread_count = user_notifications.filter(is_read=False).count()
    recent = list(user_notifications[:8])

    return {
        "unread_notifications_count": unread_count,
        "recent_notifications": recent,
    }


def platform_settings_context(request):
    """
    Supplies global platform settings such as maintenance mode status and banner message.
    """
    try:
        maintenance_active = PlatformSetting.get_setting("maintenance_mode", "false").strip().lower() in ("true", "1", "yes")
        maintenance_msg = PlatformSetting.get_setting(
            "maintenance_message",
            "Platform maintenance and scheduled updates are currently in progress. Some operations may experience brief interruptions.",
        )
    except Exception:
        maintenance_active = False
        maintenance_msg = ""

    return {
        "platform_maintenance_active": maintenance_active,
        "platform_maintenance_message": maintenance_msg,
    }

