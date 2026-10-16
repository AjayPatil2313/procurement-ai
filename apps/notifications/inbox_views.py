import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.dashboard.models import Notification

logger = logging.getLogger(__name__)


@login_required
def notifications_inbox_view(request):
    """
    Dedicated In-App System Notification Center:
    Displays user notifications with real-time status tabs (All, Unread, Read, Important, Archived),
    priority indicator badges, direct CTA click-through links, and batch actions.
    """
    user = request.user
    tab = request.GET.get("tab", "all").strip().lower()
    q = request.GET.get("q", "").strip()

    base_qs = Notification.objects.filter(user=user)

    # Compute metric counts across tabs
    total_unarchived = base_qs.filter(is_archived=False).count()
    unread_count = base_qs.filter(is_read=False, is_archived=False).count()
    read_count = base_qs.filter(is_read=True, is_archived=False).count()
    important_count = base_qs.filter(
        priority__in=[Notification.Priority.WARNING, Notification.Priority.CRITICAL],
        is_archived=False,
    ).count()
    archived_count = base_qs.filter(is_archived=True).count()

    # Filter by selected tab
    if tab == "unread":
        filtered_qs = base_qs.filter(is_read=False, is_archived=False)
    elif tab == "read":
        filtered_qs = base_qs.filter(is_read=True, is_archived=False)
    elif tab == "important":
        filtered_qs = base_qs.filter(
            priority__in=[Notification.Priority.WARNING, Notification.Priority.CRITICAL],
            is_archived=False,
        )
    elif tab == "archived":
        filtered_qs = base_qs.filter(is_archived=True)
    else:
        tab = "all"
        filtered_qs = base_qs.filter(is_archived=False)

    # Apply search filter
    if q:
        filtered_qs = filtered_qs.filter(
            Q(title__icontains=q)
            | Q(message__icontains=q)
            | Q(event_key__icontains=q)
        )

    # Pagination
    per_page = 15
    paginator = Paginator(filtered_qs.order_by("-created_at"), per_page)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page_num)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    query_string = params.urlencode()

    context = {
        "page_title": "Notification Center",
        "active_tab": tab,
        "search_query": q,
        "notifications": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "total_unarchived": total_unarchived,
        "unread_count": unread_count,
        "read_count": read_count,
        "important_count": important_count,
        "archived_count": archived_count,
    }
    return render(request, "notifications/inbox.html", context)


@login_required
def notification_toggle_read_view(request, pk):
    """
    Toggles is_read status for a specific notification.
    """
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    if notif.is_read:
        notif.mark_as_unread()
        messages.info(request, "Notification marked as unread.")
    else:
        notif.mark_as_read()
        messages.success(request, "Notification marked as read.")

    redirect_url = request.META.get("HTTP_REFERER") or reverse("notifications-inbox")
    return redirect(redirect_url)


@login_required
def notification_archive_view(request, pk):
    """
    Archives a specific notification.
    """
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.archive()
    messages.success(request, "Notification moved to archive.")

    redirect_url = request.META.get("HTTP_REFERER") or reverse("notifications-inbox")
    return redirect(redirect_url)


@login_required
def notification_restore_view(request, pk):
    """
    Restores an archived notification back to inbox.
    """
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.restore()
    messages.success(request, "Notification restored to inbox.")

    redirect_url = request.META.get("HTTP_REFERER") or reverse("notifications-inbox")
    return redirect(redirect_url)


@login_required
@require_POST
def notifications_mark_all_read_web_view(request):
    """
    Web form POST to mark all notifications as read.
    """
    Notification.objects.filter(user=request.user, is_read=False, is_archived=False).update(is_read=True)
    messages.success(request, "All notifications have been marked as read.")
    return redirect(request.META.get("HTTP_REFERER") or reverse("notifications-inbox"))


@login_required
@require_POST
def notifications_bulk_action_view(request):
    """
    Bulk actions on user notifications (mark_read, mark_unread, archive, restore, delete).
    Strictly scoped to request.user to preserve multi-tenant isolation.
    """
    action = request.POST.get("action", "").strip().lower()
    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        # Check if sent as comma-separated string
        raw_ids = request.POST.get("ids", "").strip()
        if raw_ids:
            selected_ids = [i.strip() for i in raw_ids.split(",") if i.strip().isdigit()]

    if not selected_ids:
        messages.warning(request, "No notifications were selected for batch operation.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("notifications-inbox"))

    # Strictly filter by authenticated user
    qs = Notification.objects.filter(user=request.user, id__in=selected_ids)
    count = qs.count()

    if action == "mark_read":
        qs.update(is_read=True)
        messages.success(request, f"{count} notification{'s' if count != 1 else ''} marked as read.")
    elif action == "mark_unread":
        qs.update(is_read=False)
        messages.info(request, f"{count} notification{'s' if count != 1 else ''} marked as unread.")
    elif action == "archive":
        qs.update(is_archived=True)
        messages.success(request, f"{count} notification{'s' if count != 1 else ''} moved to archive.")
    elif action == "restore":
        qs.update(is_archived=False)
        messages.success(request, f"{count} notification{'s' if count != 1 else ''} restored to inbox.")
    elif action == "delete":
        qs.delete()
        messages.success(request, f"{count} notification{'s' if count != 1 else ''} deleted.")
    else:
        messages.error(request, f"Unrecognized batch action: {action}")

    return redirect(request.META.get("HTTP_REFERER") or reverse("notifications-inbox"))
