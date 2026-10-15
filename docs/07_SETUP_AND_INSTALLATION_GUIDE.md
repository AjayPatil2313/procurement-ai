# Chapter 7: Setup & Installation Guide

---

## 7.1 System Requirements & Prerequisites

Before setting up **TradeConnect**, ensure your workstation satisfies the following software prerequisites:

| Component | Minimum Version | Recommended Version | Notes |
| :--- | :--- | :--- | :--- |
| **Operating System** | Windows 10/11, Ubuntu 22.04 LTS, or macOS 13+ | Windows 11 64-bit | Native PowerShell commands provided below |
| **Python** | Python 3.11+ | Python 3.12 or 3.14 | Ensure Python is added to system `PATH` |
| **Database** | MySQL 8.0+ | MySQL 8.0.35+ | Must support `utf8mb4` character set |
| **Git** | Git 2.40+ | Latest Git for Windows | Required for source control |

---

## 7.2 Windows PowerShell Installation Walkthrough

### Step 1: Clone the Repository
Open PowerShell and navigate to your desired development directory:
```powershell
cd "C:\Users\<YourUsername>\Projects"
git clone <repository_url> procurement_ai
cd procurement_ai
```

### Step 2: Create & Activate Virtual Environment
```powershell
# Create isolated Python virtual environment
python -m venv venv

# Activate virtual environment in PowerShell
.\venv\Scripts\Activate.ps1

# Verify Python executable is inside venv
where.exe python
```

> [!NOTE]
> If PowerShell displays an `Execution_Policies` script restriction error, run:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### Step 3: Install Python Dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## 7.3 Environment Configuration (`.env`)

Create a `.env` file in the project root (`procurement_ai/.env`). Use the template below with safe development placeholders:

```ini
# ============================================================
# SECURITY & CORE CONFIGURATION
# ============================================================
SECRET_KEY=django-insecure-local-dev-key-change-in-production-!@#$
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0

# ============================================================
# MYSQL DATABASE CONFIGURATION
# ============================================================
DB_NAME=procurement_ai_db
DB_USER=root
DB_PASSWORD=YourLocalMySQLPasswordHere
DB_HOST=127.0.0.1
DB_PORT=3306
DB_CONN_MAX_AGE=60

# ============================================================
# AI COGNITIVE & FAILOVER GATEWAY (API KEYS)
# ============================================================
# Primary AI Engine (Google Gemini 2.5 Flash)
GEMINI_API_KEY=your_gemini_api_key_placeholder

# Cloud Failover Engine (OpenAI gpt-4o-mini Backup)
OPENAI_API_KEY=your_openai_api_key_placeholder

# ============================================================
# TRANSACTIONAL SMTP EMAIL CONFIGURATION
# ============================================================
# Options: django.core.mail.backends.console.EmailBackend (Development)
#          django.core.mail.backends.smtp.EmailBackend (Live SMTP)
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your_email@gmail.com
EMAIL_HOST_PASSWORD=your_gmail_app_password
DEFAULT_FROM_EMAIL=TradeConnect <noreply@tradeconnect.com>
EMAIL_TIMEOUT=10

# ============================================================
# PAYMENT GATEWAY INTEGRATIONS (SANDBOX DUMMY KEYS)
# ============================================================
RAZORPAY_KEY_ID=rzp_test_placeholder
RAZORPAY_KEY_SECRET=rzp_secret_placeholder
STRIPE_PUBLIC_KEY=pk_test_placeholder
STRIPE_SECRET_KEY=sk_test_placeholder
```

---

## 7.4 Database Setup & Migrations

### Step 1: Create the MySQL Database
Log in to your local MySQL CLI or MySQL Workbench:
```sql
CREATE DATABASE procurement_ai_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Step 2: Execute Django Migrations
Apply database schema definitions in correct dependency order:
```powershell
python manage.py migrate
```

### Step 3: Seed Standard Notification Templates & Trigger Rules
Execute the automated seeding command to install all 21 standardized templates and rules:
```powershell
python manage.py seed_email_templates
```
*Expected Output:*
```
Successfully seeded: 37 templates created, 0 updated. 37 rules created, 0 updated.
```

### Step 4: Create Super Administrator Account
```powershell
python manage.py createsuperuser --email admin@tradeconnect.internal --first_name Super --last_name Admin
```

---

## 7.5 Launching the Development Server

Start the Django development server:
```powershell
python manage.py runserver
```

Open your browser and navigate to:
- **Main Portal**: `http://127.0.0.1:8000/` (Redirects to `/login/`)
- **Super Admin Console**: `http://127.0.0.1:8000/admin-panel/`
- **Django Built-in Admin**: `http://127.0.0.1:8000/admin/`
- **Centralized Email Triggers**: `http://127.0.0.1:8000/admin-panel/email/triggers/`
