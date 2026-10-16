import logging
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q
from django.utils import timezone
from django.utils.timesince import timesince
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dashboard.models import Notification

logger = logging.getLogger(__name__)


def serialize_notification(n):
    """
    Standard serializer dictionary for in-app Notification models.
    """
    return {
        "id": n.id,
        "priority": n.priority,
        "event_key": n.event_key or "",
        "notification_type": n.notification_type,
        "title": n.title,
        "message": n.message,
        "link": n.clean_link or "",
        "is_read": n.is_read,
        "is_archived": n.is_archived,
        "icon": n.icon or "fa-bell",
        "color": n.color or "blue",
        "created_at": n.created_at.isoformat(),
        "created_at_formatted": n.created_at.strftime("%b %d, %Y %H:%M"),
        "time_ago": f"{timesince(n.created_at)} ago",
    }


class NotificationListAPIView(APIView):
    """
    GET /api/notifications/
    Returns a paginated list of notifications for the authenticated user,
    with filtering by status (all, unread, read, archived) and priority.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        qs = Notification.objects.filter(user=user)

        # Status filter
        status_filter = request.GET.get("status", "all").strip().lower()
        if status_filter == "unread":
            qs = qs.filter(is_read=False, is_archived=False)
        elif status_filter == "read":
            qs = qs.filter(is_read=True, is_archived=False)
        elif status_filter == "archived":
            qs = qs.filter(is_archived=True)
        else:
            # "all" defaults to unarchived
            qs = qs.filter(is_archived=False)

        # Priority filter
        priority_filter = request.GET.get("priority", "").strip().upper()
        if priority_filter in dict(Notification.Priority.choices):
            qs = qs.filter(priority=priority_filter)
        elif priority_filter == "IMPORTANT":
            qs = qs.filter(priority__in=[Notification.Priority.WARNING, Notification.Priority.CRITICAL])

        # Search query
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(message__icontains=q) | Q(event_key__icontains=q))

        # Overall user unread count
        total_unread = Notification.objects.filter(user=user, is_read=False, is_archived=False).count()
        critical_count = Notification.objects.filter(
            user=user,
            is_read=False,
            is_archived=False,
            priority__in=[Notification.Priority.WARNING, Notification.Priority.CRITICAL],
        ).count()

        # Pagination
        per_page = 15
        try:
            per_page = max(5, min(100, int(request.GET.get("page_size", per_page))))
        except (ValueError, TypeError):
            pass

        paginator = Paginator(qs, per_page)
        page_num = request.GET.get("page", 1)
        try:
            page_obj = paginator.page(page_num)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        results = [serialize_notification(n) for n in page_obj.object_list]

        return Response({
            "success": True,
            "total_count": paginator.count,
            "num_pages": paginator.num_pages,
            "current_page": page_obj.number,
            "unread_count": total_unread,
            "critical_count": critical_count,
            "results": results,
        })


class NotificationUnreadCountAPIView(APIView):
    """
    GET /api/notifications/unread-count/
    Lightweight endpoint for real-time polling (e.g. 30s) by the navbar bell badge.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        unread_count = Notification.objects.filter(user=user, is_read=False, is_archived=False).count()
        critical_count = Notification.objects.filter(
            user=user,
            is_read=False,
            is_archived=False,
            priority__in=[Notification.Priority.WARNING, Notification.Priority.CRITICAL],
        ).count()
        return Response({
            "success": True,
            "unread_count": unread_count,
            "critical_count": critical_count,
        })


class NotificationMarkReadAPIView(APIView):
    """
    POST /api/notifications/<int:pk>/mark-read/
    Marks a specific notification as read.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
        except Notification.DoesNotExist:
            return Response(
                {"success": False, "error": "Notification not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        notif.mark_as_read()
        unread_count = Notification.objects.filter(user=request.user, is_read=False, is_archived=False).count()

        return Response({
            "success": True,
            "id": notif.id,
            "is_read": True,
            "unread_count": unread_count,
            "link": notif.clean_link,
        })


class NotificationMarkUnreadAPIView(APIView):
    """
    POST /api/notifications/<int:pk>/mark-unread/
    Marks a specific notification as unread.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
        except Notification.DoesNotExist:
            return Response(
                {"success": False, "error": "Notification not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        notif.mark_as_unread()
        unread_count = Notification.objects.filter(user=request.user, is_read=False, is_archived=False).count()

        return Response({
            "success": True,
            "id": notif.id,
            "is_read": False,
            "unread_count": unread_count,
        })


class NotificationMarkAllReadAPIView(APIView):
    """
    POST /api/notifications/mark-all-read/
    Marks all notifications for the authenticated user as read.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        updated = Notification.objects.filter(user=request.user, is_read=False, is_archived=False).update(is_read=True)
        return Response({
            "success": True,
            "updated_count": updated,
            "unread_count": 0,
        })


class NotificationDeleteArchiveAPIView(APIView):
    """
    POST /api/notifications/<int:pk>/delete/
    Soft-deletes (archives) or deletes the notification for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
        except Notification.DoesNotExist:
            return Response(
                {"success": False, "error": "Notification not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Archive notification
        notif.archive()
        unread_count = Notification.objects.filter(user=request.user, is_read=False, is_archived=False).count()

        return Response({
            "success": True,
            "id": notif.id,
            "is_archived": True,
            "unread_count": unread_count,
        })


class PushConfigAPIView(APIView):
    """
    GET /api/notifications/push-config/
    Returns public Firebase configuration and VAPID public key.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.notifications.services.fcm_service import get_firebase_web_config
        return Response(get_firebase_web_config())


class DeviceRegisterAPIView(APIView):
    """
    POST /api/notifications/devices/register/
    Registers an FCM device push token for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = request.data.get("token", "").strip()
        if not token:
            return Response(
                {"success": False, "error": "Token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        device_type = request.data.get("device_type", "OTHER")
        device_name = request.data.get("device_name", "")
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        from apps.notifications.services.fcm_service import register_device_token
        try:
            device = register_device_token(
                user=request.user,
                token=token,
                device_type=device_type,
                device_name=device_name,
                user_agent=user_agent,
            )
            return Response({
                "success": True,
                "message": "Device token registered successfully.",
                "device_id": device.id,
                "is_active": device.is_active,
            }, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class DeviceUnregisterAPIView(APIView):
    """
    POST /api/notifications/devices/unregister/
    Deactivates an FCM device token for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = request.data.get("token", "").strip()
        if not token:
            return Response(
                {"success": False, "error": "Token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.notifications.services.fcm_service import unregister_device_token
        ok = unregister_device_token(request.user, token)
        return Response({
            "success": True,
            "message": "Device token unregistered." if ok else "Token not found.",
        })


class TestPushNotificationAPIView(APIView):
    """
    POST /api/notifications/test-push/
    Dispatches a test push notification to the authenticated user's registered devices.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        title = request.data.get("title", "TradeConnect Notification").strip()
        body = request.data.get("body", "This is a verified test push notification from TradeConnect.").strip()
        url = request.data.get("url", "/notifications/inbox/").strip()

        from apps.notifications.services.fcm_service import send_user_push
        res = send_user_push(
            user_or_users=request.user,
            title=title,
            body=body,
            link=url,
        )
        return Response(res)

