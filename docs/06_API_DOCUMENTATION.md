# Chapter 6: API Documentation & Integration Specification

---

## 6.1 API Overview & Conventions

TradeConnect provides a comprehensive **RESTful API** powered by **Django REST Framework (DRF)**. All API endpoints follow standard HTTP semantics, return JSON payloads, and use standard error formatting.

### Base URLs
- Local Development: `http://127.0.0.1:8000/api/`
- Production Gateway: `https://api.tradeconnect.com/api/`

### Standard Request Headers
```http
Content-Type: application/json
Accept: application/json
Authorization: Bearer <your_jwt_access_token>
```

### Standard Response Codes
- `200 OK`: Request succeeded.
- `201 Created`: Resource successfully created.
- `400 Bad Request`: Input validation failed (returns field-level errors).
- `401 Unauthorized`: Token missing, expired, or invalid.
- `403 Forbidden`: Authenticated user lacks permission or company is suspended.
- `404 Not Found`: Target resource does not exist or belongs to another tenant.
- `429 Too Many Requests`: Rate limit exceeded.
- `500 Internal Server Error`: Server-side unhandled exception.

---

## 6.2 Authentication & Token Endpoints (`/api/auth/`)

### 1. User Registration
- **Endpoint**: `POST /api/auth/register/`
- **Access**: Public
- **Request Body**:
  ```json
  {
    "email": "procurement.lead@enterprise.com",
    "password": "StrongPassword123!",
    "first_name": "Vikram",
    "last_name": "Sharma",
    "company_name": "Apex Engineering Corp",
    "company_type": "BUYER"
  }
  ```
- **Response (201 Created)**:
  ```json
  {
    "status": "success",
    "message": "Registration successful. Please verify your email.",
    "user_id": 42,
    "email": "procurement.lead@enterprise.com"
  }
  ```

### 2. Obtain JWT Token Pair (Login)
- **Endpoint**: `POST /api/auth/login/`
- **Access**: Public
- **Request Body**:
  ```json
  {
    "email": "procurement.lead@enterprise.com",
    "password": "StrongPassword123!"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "user": {
      "id": 42,
      "email": "procurement.lead@enterprise.com",
      "name": "Vikram Sharma",
      "company_id": 18,
      "company_name": "Apex Engineering Corp",
      "role": "ADMIN"
    }
  }
  ```

### 3. Refresh Access Token
- **Endpoint**: `POST /api/auth/token/refresh/`
- **Request Body**: `{"refresh": "<refresh_token>"}`
- **Response (200 OK)**: `{"access": "<new_access_token>"}`

---

## 6.3 Procurement Requirements Endpoints (`/api/requirements/`)

### 1. List Company Requirements
- **Endpoint**: `GET /api/requirements/`
- **Access**: Authenticated (Buyer / Both)
- **Query Parameters**:
  - `status`: Filter by status (`OPEN`, `IN_NEGOTIATION`, `FULFILLED`)
  - `q`: Search keyword across title and specifications
  - `page`: Page index (default: 1)
- **Response (200 OK)**:
  ```json
  {
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 105,
        "title": "Industrial Hydraulic Pump Assemblies",
        "specifications": "500 Bar operating pressure, ISO 4401 compliant mounting",
        "target_price": "45000.00",
        "target_quantity": 50,
        "deadline": "2026-11-30",
        "status": "OPEN",
        "created_at": "2026-10-15T10:00:00Z"
      }
    ]
  }
  ```

### 2. Create New Requirement
- **Endpoint**: `POST /api/requirements/`
- **Request Body**:
  ```json
  {
    "title": "Forged Stainless Steel Flanges (ANSI B16.5)",
    "specifications": "Grade 316L, 150# Class, 4-inch nominal bore",
    "target_price": "3200.00",
    "target_quantity": 200,
    "deadline": "2026-12-15"
  }
  ```

---

## 6.4 Catalog Products Endpoints (`/api/catalog/`)

### 1. List Products
- **Endpoint**: `GET /api/catalog/products/`
- **Access**: Authenticated (Seller / Both)
- **Response (200 OK)**:
  ```json
  {
    "count": 24,
    "results": [
      {
        "id": 88,
        "title": "Precision CNC Machined Shafts",
        "category": "Mechanical Components",
        "price": "1850.00",
        "moq": 100,
        "is_available": true
      }
    ]
  }
  ```

---

## 6.5 Inquiries & RFQ Endpoints (`/api/leads/`)

### 1. Dispatch RFQ Inquiry
- **Endpoint**: `POST /api/leads/inquiries/`
- **Request Body**:
  ```json
  {
    "requirement_id": 105,
    "seller_company_id": 34,
    "initial_message": "Please review specifications and provide FOB Mumbai quotation.",
    "target_price": "44000.00"
  }
  ```
- **Response (201 Created)**:
  ```json
  {
    "status": "success",
    "inquiry_id": 312,
    "status": "PENDING",
    "dispatched_at": "2026-10-15T10:30:00Z"
  }
  ```

---

## 6.6 Credit Wallet Endpoints (`/api/billing/`)

### 1. View Company Wallet Balance
- **Endpoint**: `GET /api/billing/wallet/`
- **Response (200 OK)**:
  ```json
  {
    "company_id": 18,
    "total_available_credits": 450,
    "subscription_credits": 300,
    "purchased_credits": 150,
    "reserved_credits": 0,
    "plan_name": "Enterprise Pro Growth",
    "renewal_date": "2026-11-15"
  }
  ```

### 2. Super Admin Wallet Adjustment API
- **Endpoint**: `POST /api/admin/billing/adjustments/`
- **Access**: Super Admin Only (`is_superuser=True`)
- **Request Body**:
  ```json
  {
    "company_id": 18,
    "amount": 250,
    "adjustment_type": "CREDIT",
    "reason": "Promotional bonus allocated by Super Admin"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "status": "success",
    "new_balance": 700,
    "transaction_id": "TXN-CR-8921"
  }
  ```
