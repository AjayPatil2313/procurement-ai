# Chapter 10: Production Deployment & Operations Guide

---

## 10.1 Production Architecture

For production environments, TradeConnect deploys behind a high-concurrency reverse proxy paired with a WSGI application server and a dedicated MySQL database cluster:

```mermaid
flowchart TD
    subgraph Internet["Public Internet"]
        Client["Enterprise Client (HTTPS / Port 443)"]
    end

    subgraph ReverseProxy["Edge / Reverse Proxy Tier"]
        Nginx["Nginx Reverse Proxy / Load Balancer<br>(SSL Termination & Static Assets)"]
    end

    subgraph AppTier["Application Server Tier"]
        Gunicorn["Gunicorn WSGI Master<br>(4-8 Worker Processes)"]
        CeleryWorker["Celery / Daemon Worker<br>(Outbox Retries & Schedulers)"]
    end

    subgraph DataTier["Data & Cache Tier"]
        MySQLPrimary[("MySQL 8.0 Primary Node")]
        RedisNode[("Redis (Session & Broker)")]
    end

    Client -->|HTTPS| Nginx
    Nginx -->|Proxy Pass (127.0.0.1:8000)| Gunicorn
    Nginx -->|Direct Static Serving| StaticFiles["/static/ & /media/"]
    Gunicorn --> MySQLPrimary
    Gunicorn --> RedisNode
    CeleryWorker --> RedisNode
    CeleryWorker --> MySQLPrimary
```

---

## 10.2 Production Configuration Checklist

Before deploying to production, modify the environment variables in `.env`:

```ini
# 1. Disable Debug Mode
DEBUG=False

# 2. Set Domain Names
ALLOWED_HOSTS=tradeconnect.com,api.tradeconnect.com,www.tradeconnect.com

# 3. Cryptographically Strong Secret Key
SECRET_KEY=generate_a_64_character_random_high_entropy_key_here

# 4. Enforce HTTPS & Security Cookies
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True

# 5. Production Database Connection Pool
DB_HOST=mysql-prod-cluster.internal
DB_CONN_MAX_AGE=120
```

---

## 10.3 Step-by-Step Linux / Ubuntu Deployment

### Step 1: Provision Dependencies & Virtualenv
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv mysql-client libmysqlclient-dev nginx
cd /var/www/procurement_ai
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt gunicorn
```

### Step 2: Static Assets & Migrations
```bash
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_email_templates
```

### Step 3: Configure Systemd Service for Gunicorn
Create `/etc/systemd/system/tradeconnect.service`:
```ini
[Unit]
Description=TradeConnect Gunicorn Application Daemon
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/procurement_ai
ExecStart=/var/www/procurement_ai/venv/bin/gunicorn \
          --workers 4 \
          --bind 127.0.0.1:8000 \
          --timeout 60 \
          config.wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tradeconnect.service
sudo systemctl status tradeconnect.service
```

### Step 4: Configure Nginx Virtual Host
Create `/etc/nginx/sites-available/tradeconnect.conf`:
```nginx
server {
    listen 80;
    server_name tradeconnect.com www.tradeconnect.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name tradeconnect.com www.tradeconnect.com;

    ssl_certificate /etc/letsencrypt/live/tradeconnect.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tradeconnect.com/privkey.pem;

    client_max_body_size 25M;

    location /static/ {
        alias /var/www/procurement_ai/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    location /media/ {
        alias /var/www/procurement_ai/media/;
        expires 7d;
    }

    location / {
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

Enable the site and test configuration:
```bash
sudo ln -s /etc/nginx/sites-available/tradeconnect.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 10.4 Background Scheduled Tasks (Cron / Celery)

To process outbox email retries and check for approaching subscription expiries:
```bash
# Edit crontab for www-data
crontab -e -u www-data
```
Add the automated maintenance entries:
```cron
# Retry failed email notification attempts every 5 minutes
*/5 * * * * /var/www/procurement_ai/venv/bin/python /var/www/procurement_ai/manage.py retry_failed_notifications >> /var/log/tradeconnect_email.log 2>&1

# Check for expiring subscriptions and dispatch alerts daily at 02:00 UTC
0 2 * * * /var/www/procurement_ai/venv/bin/python /var/www/procurement_ai/manage.py send_subscription_reminders >> /var/log/tradeconnect_reminders.log 2>&1
```
