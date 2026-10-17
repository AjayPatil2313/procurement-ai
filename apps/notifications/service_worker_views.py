import json
from django.http import HttpResponse
from apps.notifications.services.fcm_service import get_firebase_web_config


def firebase_service_worker_view(request):
    """
    Serves the Firebase Cloud Messaging Service Worker from the root origin scope
    with the 'Service-Worker-Allowed: /' response header.
    Embeds public Firebase configuration directly to ensure the Service Worker
    initializes reliably even when started in the background without an active window.
    """
    web_config = get_firebase_web_config()
    config_json = json.dumps(web_config)

    sw_script = f"""// TradeConnect B2B Platform - Firebase Cloud Messaging Service Worker
importScripts('https://www.gstatic.com/firebasejs/10.9.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.9.0/firebase-messaging-compat.js');

self.addEventListener('install', (event) => {{
    self.skipWaiting();
}});

self.addEventListener('activate', (event) => {{
    event.waitUntil(self.clients.claim());
}});

// 1. Top-Level Firebase Initialization
const fcmConfig = {config_json};
let messaging = null;

if (typeof firebase !== 'undefined' && fcmConfig && fcmConfig.apiKey) {{
    try {{
        if (!firebase.apps.length) {{
            firebase.initializeApp(fcmConfig);
        }}
        messaging = firebase.messaging();
        messaging.onBackgroundMessage((payload) => {{
            const notifTitle = payload.notification?.title || payload.data?.title || 'TradeConnect Platform Alert';
            const isCritical = payload.data?.priority === 'CRITICAL' || payload.data?.priority === 'WARNING';
            const notifOptions = {{
                body: payload.notification?.body || payload.data?.body || 'You have an important update.',
                icon: payload.notification?.icon || '/static/images/logo.png',
                badge: '/static/images/favicon.ico',
                tag: payload.data?.tag || payload.data?.event_key || ('tradeconnect-' + Date.now()),
                requireInteraction: isCritical,
                actions: [
                    {{ action: 'view', title: 'Open', icon: '/static/images/favicon.ico' }},
                    {{ action: 'dismiss', title: 'Dismiss' }}
                ],
                data: {{
                    url: payload.data?.url || payload.data?.click_action || '/dashboard/'
                }}
            }};
            self.registration.showNotification(notifTitle, notifOptions);
        }});
    }} catch (err) {{
        console.warn('[firebase-messaging-sw.js] Background messaging setup warning:', err);
    }}
}}

// 2. Client message listener (for dynamic config updates)
self.addEventListener('message', (event) => {{
    if (event.data && event.data.type === 'INIT_FIREBASE_SW' && event.data.config) {{
        try {{
            if (!firebase.apps.length) {{
                firebase.initializeApp(event.data.config);
            }}
        }} catch (e) {{
            console.warn('[firebase-messaging-sw.js] Message event init:', e);
        }}
    }}
}});

// 3. Fallback Native Push Event Listener (Reliability Shield)
self.addEventListener('push', (event) => {{
    if (!event.data) return;

    let payload = {{}};
    try {{
        payload = event.data.json();
    }} catch (err) {{
        payload = {{ data: {{ title: 'TradeConnect Alert', body: event.data.text() }} }};
    }}

    // If Firebase Web SDK already displayed the notification via webpush payload, skip duplicate
    if (payload.notification && payload.notification.title) {{
        // SDK handles standard notification payloads natively
        return;
    }}

    const title = payload.data?.title || 'TradeConnect Alert';
    const body = payload.data?.body || 'New operational update received.';
    const clickUrl = payload.data?.url || payload.data?.click_action || '/dashboard/';

    const options = {{
        body: body,
        icon: '/static/images/logo.png',
        badge: '/static/images/favicon.ico',
        tag: payload.data?.tag || ('tc-' + Date.now()),
        data: {{ url: clickUrl }}
    }};

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
}});

// 4. Handle push notification click: focus existing window or open new
self.addEventListener('notificationclick', (event) => {{
    event.notification.close();

    if (event.action === 'dismiss') {{
        return;
    }}

    const targetUrl = event.notification.data?.url || '/dashboard/';

    event.waitUntil(
        clients.matchAll({{ type: 'window', includeUncontrolled: true }}).then((windowClients) => {{
            // 1. If a window is already at this exact URL, focus it
            for (let i = 0; i < windowClients.length; i++) {{
                const client = windowClients[i];
                if (client.url && client.url.includes(targetUrl) && 'focus' in client) {{
                    return client.focus();
                }}
            }}

            // 2. If a window is idle at the dashboard, navigate and focus it
            for (let i = 0; i < windowClients.length; i++) {{
                const client = windowClients[i];
                const clientPath = new URL(client.url).pathname;
                if ((clientPath === '/dashboard/' || clientPath === '/') && 'focus' in client) {{
                    client.navigate(targetUrl);
                    return client.focus();
                }}
            }}

            // 3. Otherwise, open a fresh window
            if (clients.openWindow) {{
                return clients.openWindow(targetUrl);
            }}
        }})
    );
}});
"""
    response = HttpResponse(sw_script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
