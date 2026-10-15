# Chapter 3: System Architecture

---

## 3.1 Five-Tier Architectural Model

TradeConnect is engineered around a modular, resilient **5-Tier Architecture** that enforces clear separation of concerns, high throughput, and enterprise-grade fault tolerance:

```mermaid
flowchart TD
    subgraph Tier1["Tier 1: Presentation & Web Interface"]
        Templates["Django Server-Side Templates"]
        Tailwind["Tailwind CSS 3.0 & Inter UI"]
        UIComp["Responsive Modals & Data Tables"]
    end

    subgraph Tier2["Tier 2: Application & API Gateway"]
        DjangoCore["Django 5.2 MVC Engine"]
        DRF["Django REST Framework 3.18.1"]
        JWTEngine["SimpleJWT Token Manager"]
        MiddlewareStack["Security, RBAC & Suspension Pipeline"]
    end

    subgraph Tier3["Tier 3: Cognitive AI & Cloud Failover Tier"]
        Gateway["AIFailoverGateway"]
        Gemini["Primary Engine: Google Gemini 2.5 Flash"]
        OpenAI["Failover Engine: OpenAI GPT-4o-mini"]
        Scraper["Fallback Web Crawler (Playwright / BS4)"]
    end

    subgraph Tier4["Tier 4: Persistence & Storage Tier"]
        MySQL[("MySQL 8.0 Database (InnoDB)")]
        Media["Local/Cloud Media File Storage"]
        Cache["LocMemCache (In-Memory Key-Value)"]
    end

    subgraph Tier5["Tier 5: Communication & Outbox Tier"]
        Engine["Notification Engine"]
        Outbox[("NotificationLog Outbox")]
        SMTP["Enterprise SMTP Server"]
    end

    Tier1 --> Tier2
    Tier2 --> Tier3
    Tier2 --> Tier4
    Tier2 --> Tier5
    Tier3 --> Tier4
    Tier5 --> SMTP
```

---

## 3.2 Tier Breakdown & Internal Mechanics

### 1. Presentation & Web Interface (Tier 1)
- **Engine**: Django server-rendered HTML templates utilizing template inheritance (`templates/base.html`).
- **Styling**: Tailwind CSS utility framework supplemented with FontAwesome 6 icons and Google Inter typography.
- **Interactivity**: Modal dialogs, dynamic filter pills, and client-side AJAX requests (without heavy single-page application framework overhead).

### 2. Application & API Gateway (Tier 2)
- **Django Core**: Dispatches incoming HTTP requests through WSGI/ASGI handlers, viewsets, and class-based/function-based views.
- **Django REST Framework**: Exposes authenticated REST endpoints with token authentication (`Bearer <jwt_token>`) and session fallbacks.
- **Middleware Pipeline**:
  ```mermaid
  sequenceDiagram
      autonumber
      actor User as Client Browser / API
      participant Sec as SecurityMiddleware
      participant Sess as SessionMiddleware
      participant Cors as CorsMiddleware
      participant Csrf as CsrfViewMiddleware
      participant Auth as AuthenticationMiddleware
      participant Susp as CompanySuspensionMiddleware
      participant View as Target View Handler

      User->>Sec: Incoming Request
      Sec->>Sess: Load Session Data
      Sess->>Cors: Check Allowed Origins
      Cors->>Csrf: Validate CSRF Token (POST/PUT/DELETE)
      Csrf->>Auth: Attach request.user (Session or JWT)
      Auth->>Susp: Verify Company Active Status
      alt Company is Suspended
          Susp-->>User: Auto-logout & 403 Forbidden redirect
      else Company is Active
          Susp->>View: Execute View Logic & Return Response
      end
  ```

### 3. Cognitive AI & Cloud Failover Tier (Tier 3)
The AI engine implements a **Circuit-Breaker Cloud Failover Architecture** to guarantee that user queries never fail due to upstream API rate limits or provider downtime:

```mermaid
sequenceDiagram
    autonumber
    actor App as TradeConnect AI Search Pipeline
    participant GW as AIFailoverGateway
    participant G as Google Gemini 2.5 Flash
    participant O as OpenAI GPT-4o-mini Backup
    participant DB as APILog & SearchResult

    App->>GW: generate(prompt, timeout=4.5s)
    GW->>G: POST /models/gemini-2.5-flash:generateContent
    alt Gemini Responds 200 OK
        G-->>GW: Return JSON Stream
        GW->>DB: Log Latency & Gemini Provider
        GW-->>App: Return Parsed Match Results
    else Gemini Times Out, Returns 429 Rate Limit, or 503
        G-->>GW: Error Response (e.g. 429 Quota Exceeded)
        GW->>GW: Engage Circuit Breaker & Failover Hook
        GW->>O: POST /chat/completions (gpt-4o-mini)
        O-->>GW: Return JSON Stream
        GW->>DB: Record Failover Event in APILog
        GW-->>App: Return Match Results with Failover Tag
    end
```

### 4. Persistence & Storage Tier (Tier 4)
- **RDBMS**: MySQL 8.0 with InnoDB transactional engine, foreign key constraints, and UTF8mb4 encoding supporting international trade characters and emojis.
- **Soft Deletion**: Business data models inherit `is_deleted` and `deleted_at` fields to protect historical financial and audit data from accidental hard deletions.
- **Media**: Securely isolated storage for product catalog photos and verification tax documents in `media/`.

### 5. Communication & Outbox Tier (Tier 5)
- **Transactional Outbox Pattern**: When an event occurs, an immutable record is committed to `NotificationLog` with status `PENDING` before delivery is attempted.
- **Asynchronous / Non-Blocking Dispatch**: Transmission runs on dedicated background daemon threads (`threading.Thread`) or Celery workers so user requests never experience latency spikes.
- **Exponential Backoff Retries**: Transient SMTP connection failures are rescheduled with bounded exponential delays (`retry_after`).
- **RFC 8058 Compliance**: Every outgoing operational email embeds verified `List-Unsubscribe` headers and cryptographic one-click unsubscribe links.

---

## 3.3 Multi-Tenant Isolation Model

TradeConnect utilizes a **Shared Database, Shared Schema, Tenant-Column Isolation** architecture:

```mermaid
flowchart TD
    subgraph IncomingRequest["Incoming Request Context"]
        Req["request.user"] --> CM["CompanyMember"]
        CM --> Comp["request.user.company (Tenant ID)"]
    end

    subgraph DataIsolationFilter["Data Isolation Filter"]
        Comp --> Filter["queryset.filter(company=request.user.company)"]
    end

    subgraph DatabaseTables["Tenant Data Isolation"]
        Filter --> T1["Requirements Table"]
        Filter --> T2["Catalog Products Table"]
        Filter --> T3["Inquiries Table"]
        Filter --> T4["Credit Wallet & Transactions"]
    end
```

### Isolation Guarantees:
1. **Model-Level Foreign Keys**: Every business entity links to `Company` (`ForeignKey(Company, on_delete=models.CASCADE)`).
2. **View-Level Scoping**: All search, list, detail, and export queries apply strict filtering against `request.user.company`.
3. **Cross-Company Access Denial**: Direct ID manipulation in URL routes (`/procurement/requirements/<pk>/`) is validated against tenant ownership; foreign tenant IDs trigger immediate `404 Not Found` or `403 Forbidden` responses.
4. **Super Admin Impersonation**: Only authenticated Super Admins can explicitly assume a tenant's session state, recorded in the audit log with the initiating admin's identity.
