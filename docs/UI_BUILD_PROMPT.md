# UI Build Prompt: Multi-Screen Operational UI/UX for Narayani Outreach System

Build a complete, multi-screen operator UI for the Narayani B2B Cold Outreach System.
Follow every rule, schema definition, and safety constraint detailed below.

---

## 1. Purpose & Target User

- **User**: A single outreach operations manager running B2B cold email campaigns.
- **Core Jobs**:
  1. **Monitor**: Track real-time send pacing, daily cap utilization, queue flow, and deliverability health.
  2. **Review & Triage**: Inspect and resolve drafts held by validators (copywriting holds, vetoes) and positive prospect replies paused for human takeover.
  3. **Safety & Governance**: Enforce compliance invariants (hard allowlist, permanent suppression, content-free audit trail, read-only system flags).

---

## 2. Screens & Exact Data Mappings

All data comes from existing PostgreSQL tables and FastAPI backend endpoints.

### Screen 1: Overview (Dashboard)
- **Goal**: High-level system pulse and immediate action alerts.
- **Fields**:
  - *Sent Today vs Daily Cap*: `daily_sends.sent_today` / `daily_sends.daily_cap` (`app/pipeline/cap_enforcer.py`, `dashboard.py`)
  - *Active Holds Today*: `vetoes_and_holds_today.validator_holds` + `contacts_held` (`audit_log`, `contacts`)
  - *Total Suppressions*: `suppressions.total` (`suppression` table)
  - *Queue Flow Chart*: Counts grouped by status (`eligible`, `rendering`, `sending`, `sent`, `held`, `dead_letter`, `cancelled`) (`outbound_queue.status`)
  - *Verification Status Chart*: Counts grouped by `verification_status` (`valid`, `invalid`, `unknown`) (`contacts.verification_status`)
  - *Suppression Breakdown Table*: Grouped counts by `suppression.reason`
- **Data Source**: `GET /api/dashboard` (Existing)

### Screen 2: Needs Review (Human Review Queue)
- **Goal**: Resolve items paused by safety gates before sending or continuing automation.
- **Sections**:
  1. *Draft Holds & Vetoes* (`outbound_queue` joined with `contacts` where `q.status = 'held'`):
     - Fields: Queue ID (`q.id`), Contact (`c.contact_name`), Company (`c.company_name`), Email (`c.email`), Subject (`q.rendered_subject`), Draft Body (`q.rendered_body`), Attempts (`q.attempts`), Reason (`q.hold_reason`), Updated (`q.updated_at`).
  2. *Inbound Replies & Sales Takeovers* (`contacts` where `c.status = 'held'`):
     - Fields: Contact ID (`c.id`), Name (`c.contact_name`), Company (`c.company_name`), Email (`c.email`), Segment (`c.segment`), Notes/Reply summary (`c.notes`, `c.product_interest`), Updated (`c.updated_at`).
- **Data Source**: `GET /api/dashboard/needs-review` (Existing) + `GET /api/review/drafts/{id}` (Gap)

### Screen 3: Contacts Directory
- **Goal**: Search, filter, and inspect verified business prospects.
- **Fields**: Contact ID (`contacts.id`), Name (`contact_name`), Company (`company_name`), Domain (`company_domain`), Email (`email`), Segment (`segment`), Verification Status (`verification_status`), Contact Status (`status`: `pending`, `contacted`, `held`, `suppressed`), Added Date (`created_at`).
- **Data Source**: `GET /api/contacts` (Gap: needs pagination, search, filters)

### Screen 4: Outbound Queue
- **Goal**: Monitor the message pipeline, lease allocations, and crash recovery.
- **Fields**: Queue ID (`outbound_queue.id`), Prospect (`contact_id` -> name/company), Step (`campaign_step`), Queue Status (`status`), Attempts (`attempts`), Locked By (`locked_by`), Lease Expires (`lease_expires_at`), Due At (`due_at`), Updated At (`updated_at`).
- **Data Source**: `GET /api/queue` (Gap: needs pagination, filter by status)

### Screen 5: Suppression Registry
- **Goal**: Verify irreversible opt-outs and deliverability blocks.
- **Fields**: ID (`suppression.id`), SHA-256 Email Hash (`email_hash` displayed as truncated 12-char hex), Linked Contact ID (`contact_id`), Reason (`reason`: `unsubscribe_request`, `hard_bounce`, etc.), Origin (`created_by`), Date (`created_at`).
- **Data Source**: `GET /api/suppression` (Gap)

### Screen 6: Audit Log (7-Year Ledger)
- **Goal**: Full legal and technical traceability without storing personal data.
- **Fields**: Event ID (`audit_log.id`), Timestamp (`timestamp` UTC), Actor (`actor`), Action (`action`), Object (`object_type` + `:` + `object_id`), Transition (`prev_state` -> `new_state`), Reason (`reason` enum from `AuditReason`), Rulebook/Prompt Version (`rulebook_version`, `prompt_version`).
- **Data Source**: `GET /api/audit-log` (Gap: needs pagination and filters)

### Screen 7: System & Safety Config
- **Goal**: Read-only transparency over operating mode, provider status, and safety limits.
- **Fields**: Operating Mode (`TEST_MODE`, `LIVE_EMAIL_SENDING`), SMTP Host & User (`smtp_host`, `smtp_gmail`), Allowlist Size (`len(SEED_ALLOWLIST)` = 17), Daily Ceiling (`target_daily_sends` = 1000), Active Segments count (`segments` table), Seed Sending Enabled flag.
- **Data Source**: `GET /api/system/config` (Gap)

---

## 3. Gaps: Endpoints to Add Before UI Rendering

The following read-only FastAPI endpoints must be added to `app/api/routers/`:

1. `GET /api/contacts`: Query params: `page=1`, `page_size=25`, `status`, `segment`, `search`. Returns `{ items: list[ContactOut], total: int, page: int, page_size: int }`.
2. `GET /api/queue`: Query params: `page=1`, `page_size=25`, `status`. Returns `{ items: list[QueueItemOut], total: int, page: int, page_size: int }`. Joined with contact name & company.
3. `GET /api/suppression`: Query params: `page=1`, `page_size=25`. Returns `{ items: list[SuppressionOut], total: int, page: int, page_size: int }`.
4. `GET /api/audit-log`: Query params: `page=1`, `page_size=50`, `actor`, `action`, `reason`. Returns `{ items: list[AuditLogOut], total: int, page: int, page_size: int }`.
5. `GET /api/system/config`: Returns `{ test_mode: bool, live_sending: bool, seed_sending_enabled: bool, smtp_host: str, smtp_user: str, allowlist_count: int, daily_cap: int }`.

### DB Storage Reality Flags:
- **Suppression Table**: Contains **no** plaintext email column (stores only `email_hash` per §14.8b). Do not attempt to query `suppression.email`.
- **Audit Log Table**: Strictly **content-free** per §14.8b. Contains no prospect names, emails, subjects, or message bodies.
- **Tracking**: The database stores **no** open-tracking pixels or click-tracking redirects (§11.4).

---

## 4. UX & Design Rules

- **Visual Theme**: Clean system dashboard (Slate/Zinc palette, monospace for hashes/IDs, system fonts).
- **Badges & Labels**:
  - Convert technical enums to friendly labels: `validator_hold` -> "Validator Hold", `unsubscribe_request` -> "Unsubscribe Request".
  - Color-code statuses: `valid`/`sent`/`eligible` = Green; `rendering`/`sending` = Blue; `held` = Amber; `dead_letter`/`suppressed`/`cancelled` = Red.
- **Environment Banners**: Top navigation bar must prominently display a persistent badge:
  - If `LIVE_EMAIL_SENDING=false`: Amber badge `"TEST MODE — LIVE SENDING DISABLED"`.
  - If `LIVE_EMAIL_SENDING=true`: Red pulse badge `"LIVE SENDING ACTIVE"`.
- **States**: Every screen must have explicit Loading skeleton/spinner, Empty state message, and Error state with retry button.
- **Paging & Refresh**: Standard 25/50 item pagination controls; auto-refresh toggle (30s) on Overview and Needs Review.

---

## 5. Inviolable Safety Rules

1. **No Direct Send Button**: The UI must **never** contain a button that directly calls SMTP or bypasses the Stage 3/5 queue pipeline.
2. **Privacy Integrity**: Never show un-hashed email addresses on the Suppression or Audit Log pages.
3. **Immutability of Mode Flags**: `TEST_MODE`, `LIVE_EMAIL_SENDING`, and `SEED_ALLOWLIST` are hardcoded/env-governed and must **never** be editable via any UI control.
4. **Human-Only Un-suppression**: Un-suppression requires an operator name and reason, writing an explicit `AuditReason.UNSUPPRESSION` row to `audit_log`. Un-suppression is unreachable by any agent.
5. **Validator Gate on Edited Drafts**: If a human edits a held draft in Phase 2, the draft must pass the deterministic Rulebook and Grounding validators before returning to `eligible` status.

---

## 6. Build Phases

### Phase 1: Read-Only Multi-Screen Interface (First Milestone)
- Build navigation sidebar/header with routes: `/ui/overview`, `/ui/review`, `/ui/contacts`, `/ui/queue`, `/ui/suppression`, `/ui/audit`, `/ui/config`.
- Implement missing read-only endpoints in `app/api/routers/`.
- Render all 7 screens with complete tables, filters, search, Chart.js graphs, and responsive layout.

### Phase 2: Human Operator Actions (Second Milestone)
- **Edit & Approve Draft**: Modal to edit subject/body, run validators, and requeue (`POST /api/review/{queue_id}/approve`).
- **Retry A2 Draft**: Clear `hold_reason` and return queue item to `eligible` (`POST /api/review/{queue_id}/retry`).
- **Dismiss Draft**: Move queue item to `cancelled` with audit log (`POST /api/review/{queue_id}/dismiss`).
- **Mark Reply Handled**: Return contact from `held` to `contacted` (`POST /api/review/contacts/{id}/handled`).
- **Un-suppress Contact**: Dialog with operator name & reason, deletes suppression row, writes audit log (`POST /api/suppression/{id}/unsuppress`).
- **CSV Ingestion**: Upload screen calling `import_prospects_csv` with verification summary.

---

## 7. Technical Constraints

- **Stack**: FastAPI backend + plain HTML/CSS + Vanilla JavaScript (or lightweight HTMX).
- **Libraries**: Chart.js loaded from CDN (`https://cdn.jsdelivr.net/npm/chart.js`). No React, Vue, Webpack, Vite, or npm build steps.
- **No LLM in UI**: Zero LLM calls in UI routers or frontend scripts. All AI logic remains in `app/workers/`.
- **Pipeline Integrity**: Zero modifications to pipeline gates, database schemas, or models in `app/db/models.py`.

---

## 8. Acceptance Checklist

- [ ] All 7 screens render cleanly and load data via backend REST APIs.
- [ ] Navigation switching works seamlessly without full-page crashes.
- [ ] Needs Review screen clearly separates Draft Holds from Inbound Prospect Replies.
- [ ] Attempt count and specific hold reason display accurately for every held draft.
- [ ] Suppression table displays `email_hash` without leaking plaintext emails.
- [ ] Audit log table displays all historical events with action and reason enums.
- [ ] Every operator mutation in Phase 2 records exactly one `AuditLog` row with the operator's actor name.
- [ ] Manually edited drafts are rejected if they contain exclamation marks or commercial terms.
- [ ] Environment banner accurately reflects `TEST_MODE` and `LIVE_EMAIL_SENDING`.

---

## 9. Open Questions

1. **Single-Page App vs Jinja2 Templates**: Should the multi-screen navigation be implemented as a Single-Page Application (single shell HTML switching views via JS fetch) like the current `dashboard.html`, or multi-route server-rendered Jinja2 templates?
2. **Draft Editing Validator Behavior**: When an operator edits a held draft in Phase 2, if the edit fails a validator (e.g. contains an exclamation mark or commercial price), should the UI block saving entirely with an inline error, or allow saving as a draft while keeping it in `held` status?
3. **Linked Email Resolution in Suppression View**: Since `suppression` only stores `email_hash`, when a linked `contact_id` exists, is it permitted for the UI to display the contact's company name or masked email (e.g. `a***@gmail.com`) for operator context, or must it remain 100% hash-only?
