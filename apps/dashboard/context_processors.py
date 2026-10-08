from apps.dashboard.models import Notification


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
