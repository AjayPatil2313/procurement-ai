from django.http import HttpResponse


def firebase_service_worker_view(request):
    """
    Serves the Firebase Cloud Messaging Service Worker from the root origin scope
    with the 'Service-Worker-Allowed: /' response header.
    """
    sw_script = """// TradeConnect B2B Platform - Firebase Cloud Messaging Service Worker
importScripts('https://www.gstatic.com/firebasejs/10.9.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.9.0/firebase-messaging-compat.js');

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Initialize FCM messaging when configuration message received from client window
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'INIT_FIREBASE_SW' && event.data.config) {
        if (!firebase.apps.length) {
            firebase.initializeApp(event.data.config);
            const messaging = firebase.messaging();
            messaging.onBackgroundMessage((payload) => {
                const notifTitle = payload.notification?.title || payload.data?.title || 'TradeConnect Platform Alert';
                const notifOptions = {
                    body: payload.notification?.body || payload.data?.body || 'You have an important update.',
                    icon: payload.notification?.icon || '/static/images/logo.png',
                    badge: '/static/images/favicon.ico',
                    tag: payload.data?.tag || 'tradeconnect-alert',
                    data: {
                        url: payload.data?.url || payload.data?.click_action || '/dashboard/'
                    }
                };
                self.registration.showNotification(notifTitle, notifOptions);
            });
        }
    }
});

// Handle push notification click: focus open tab or open relative target URL
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = event.notification.data?.url || '/dashboard/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            for (let i = 0; i < windowClients.length; i++) {
                const client = windowClients[i];
                if ('focus' in client) {
                    client.navigate(targetUrl);
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
"""
    response = HttpResponse(sw_script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
