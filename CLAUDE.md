# SecureTrack Platform — Codebase Reference

## Overview
SecureTrack is a security guard management platform with two codebases:
- **Backend**: FastAPI + SQLAlchemy (Python) — `c:\Users\L0Q\Desktop\security fast api project\`
- **Frontend**: Flutter + Riverpod (Dart) — `c:\Users\L0Q\Desktop\securetrack_app\`

The platform manages security guards across sites with attendance tracking, route supervision, payroll, and incident reporting.

---

## User Roles

| Role | Description | Default Landing |
|------|-------------|-----------------|
| `admin` | Full platform management, payroll, deductions, approvals | `/admin/dashboard` |
| `supervisor` | Visits multiple sites, records attendance, manages routes | `/tactical` |
| `leader` | Always on-site at one location, records attendance, manages cash advances | `/leader/attendance` |
| `guard` | Stationed at a site, takes photos, reports incidents | `/guard/camera` |
| `outdoor` | Mobile guard, GPS-tracked check-in/out | `/outdoor/checkin` |

Roles are defined in `app/enums.py` as `UserRole` enum.

---

## Backend Architecture

### Tech Stack
- **Framework**: FastAPI
- **ORM**: SQLAlchemy (sync sessions)
- **Database**: SQLite (dev) / PostgreSQL (prod)
- **Auth**: JWT tokens via `python-jose`
- **Validation**: Pydantic v2

### Directory Structure
```
app/
├── api/
│   ├── deps.py              # Auth dependencies (get_current_user, require_role)
│   └── v1/                  # All API route modules
│       ├── auth.py           # Login, register, token refresh
│       ├── users.py          # User CRUD
│       ├── sites.py          # Site management
│       ├── shifts.py         # Shift definitions
│       ├── roster.py         # Guard-to-shift assignments
│       ├── routes.py         # Supervisor route assignments
│       ├── visits.py         # Supervisor site visits
│       ├── attendance.py     # Attendance recording (supervisor + leader + guard auto-checkin)
│       ├── incidents.py      # Incident reporting
│       ├── dashboard.py      # Admin dashboard stats
│       ├── notifications.py  # Push notifications
│       ├── tracking.py       # GPS tracking pings
│       ├── workforce.py      # Workforce analytics
│       ├── payroll.py        # Payroll calculations
│       ├── deduction_rules.py # Salary deduction rules
│       ├── fake_attendance.py # Fake attendance detection (GPS vs recorded + leader vs supervisor)
│       ├── cash_advance.py   # Cash advance workflow (leader → supervisor → admin)
│       ├── guard_photos.py   # Guard photo management
│       ├── outdoor.py        # Outdoor guard check-in/out
│       ├── uniforms.py       # Uniform tracking
│       └── admin.py          # Admin-specific user management
├── core/
│   ├── config.py             # Settings via pydantic-settings
│   ├── database.py           # SQLAlchemy engine, session, Base
│   ├── security.py           # JWT creation/verification, password hashing
│   └── exceptions.py         # Custom exception classes
├── models/                   # SQLAlchemy models
│   ├── user.py               # User model (all roles)
│   ├── site.py               # Site with geofence (lat, lng, radius)
│   ├── shift.py              # Shift times per site
│   ├── guard_roster.py       # Guard-shift-date assignments
│   ├── supervisor_route.py   # Supervisor-site-date assignments
│   ├── supervisor_visit.py   # Supervisor check-in/out at sites
│   ├── attendance_log.py     # Attendance records
│   ├── incident.py           # Incident reports
│   ├── cash_advance.py       # Cash advance requests
│   ├── gps_tracking_ping.py  # GPS pings from outdoor guards
│   ├── deduction_rule.py     # Payroll deduction rules
│   ├── uniform_item.py       # Uniform tracking
│   └── ...                   # Other models
├── schemas/                  # Pydantic schemas (input/output)
│   ├── user.py               # UserCreate, UserResponse, etc.
│   ├── attendance.py         # AttendanceRecord, BulkAttendanceRequest
│   └── ...
├── services/                 # Business logic layer
│   ├── user_service.py
│   ├── attendance_service.py
│   ├── roster_service.py
│   └── ...
├── enums.py                  # UserRole, CashAdvanceStatus, SiteStatus, etc.
└── main.py                   # FastAPI app, CORS, router registration
```

### Key Patterns
- **Auth**: `require_role(UserRole.ADMIN, UserRole.SUPERVISOR)` — dependency injection for role-based access
- **DB Sessions**: `db: Session = Depends(get_db)` — per-request sessions
- **Table creation**: `Base.metadata.create_all(bind=engine)` in `main.py` on startup
- **UUID PKs**: All models use `String(36)` UUIDs as primary keys

### API Prefix
All routes are mounted at `/api/v1/<module>` in `main.py`.

---

## Frontend Architecture

### Tech Stack
- **Framework**: Flutter 3.x
- **State Management**: Riverpod (NotifierProvider pattern)
- **Navigation**: GoRouter with ShellRoute for role-based shells
- **HTTP Client**: Dio (via `apiClientProvider`)
- **Storage**: `flutter_secure_storage` for JWT tokens
- **Localization**: Custom map-based i18n (EN + AR/Egyptian)

### Directory Structure
```
lib/
├── app.dart                  # GoRouter configuration, all routes
├── main.dart                 # App entry point
├── core/
│   ├── constants.dart        # API base URL, app constants
│   ├── theme/
│   │   ├── app_colors.dart   # STColors — design system color tokens
│   │   ├── app_typography.dart # STTypography — text styles
│   │   └── app_theme.dart    # Material ThemeData
│   ├── network/
│   │   └── api_client.dart   # Dio instance with JWT interceptor (apiClientProvider)
│   ├── services/
│   │   └── locale_service.dart # Locale provider + t() / tProvider() helpers
│   ├── storage/
│   │   └── secure_storage.dart # JWT token persistence
│   └── l10n/
│       ├── translations_en.dart # English strings
│       └── translations_ar.dart # Arabic (Egyptian) strings
├── data/
│   └── providers/            # Riverpod state providers
│       ├── auth_provider.dart
│       ├── admin_providers.dart
│       ├── supervisor_attendance_provider.dart
│       ├── cash_advance_provider.dart
│       ├── incidents_provider.dart
│       └── ...
├── screens/
│   ├── auth/                 # Login, Register, Language Select
│   ├── admin/                # Admin shell + all admin screens
│   ├── leader/               # Leader shell + attendance, cash advance, fake attendance
│   ├── attendance/           # Supervisor attendance screen
│   ├── guard/                # Guard shell + camera, incidents, uniform
│   ├── outdoor/              # Outdoor shell + check-in
│   ├── incidents/            # Shared incidents screen
│   ├── tactical/             # Supervisor tactical overview
│   ├── routes/               # Supervisor route management
│   ├── profile/              # Shared profile screen
│   └── settings/             # Settings screen
├── widgets/
│   ├── st_app_bar.dart       # Custom app bar
│   ├── st_components.dart    # STStatusCard, STKpiCard, STPrimaryButton, STTextField
│   ├── st_screen_title.dart  # Screen title widget
│   ├── admin_drawer.dart     # Admin navigation drawer
│   ├── supervisor_drawer.dart # Supervisor navigation drawer
│   ├── leader_drawer.dart    # Leader navigation drawer
│   ├── leader_bottom_nav.dart # Leader bottom navigation bar
│   └── ...
└── models/                   # (Optional) Dart data classes
```

### Key Patterns

#### Navigation (GoRouter)
- Each role has a `ShellRoute` with a shell widget (bottom nav + drawer)
- Shell keys: `_adminShellKey`, `_supervisorShellKey`, `_leaderShellKey`, `_guardShellKey`, `_outdoorShellKey`
- Login redirects based on `role` from auth response
- Standalone routes (outside shells) use `parentNavigatorKey: _rootNavigatorKey`

#### State Management (Riverpod)
- `NotifierProvider<T, State>` pattern for mutable state
- `FutureProvider` for one-shot async data
- Providers auto-fetch in `build()` via `Future.microtask()`
- API calls go through `ref.read(apiClientProvider)` (Dio instance)

#### Translations
- `t(ref, 'key')` in widgets — watches locale and returns translated string
- `tProvider(ref, 'key')` in providers
- Keys defined in `translations_en.dart` / `translations_ar.dart` as `Map<String, String>`

#### Design System
- Colors: `STColors.primary`, `.amber`, `.critical`, `.success`, `.info`, `.surface`, etc.
- Typography: `STTypography.headlineLgMobile`, `.titleMd`, `.bodySm`, `.labelCaps`, `.dataMono`, etc.
- Components: `STStatusCard` (card with colored left strip), `STKpiCard`, `STPrimaryButton`, `STTextField`

---

## Cash Advance Workflow

```
Leader creates request → status: "pending"
  ↓
Supervisor reviews → "supervisor_approved" or "supervisor_rejected"
  ↓ (if approved)
Admin reviews → "admin_approved" / "admin_rejected" / "admin_modified"
```

- Rejected requests appear back on the leader's "Rejected" tab
- Admin can modify the approved amount (different from requested)
- Model: `cash_advances` table with full audit trail

---

## Attendance System

1. **Supervisor attendance**: Supervisor visits sites on their route, records guard status (present/late/absent)
2. **Leader attendance**: Same as supervisor but leader is always on-site (no check-in required), uses `visit_id = 'manual'`
3. **Guard auto-checkin**: Guards check in via GPS proximity to site geofence
4. **Outdoor check-in**: Outdoor guards check in/out with GPS tracking

### Fake Attendance Detection
- **GPS vs Recorded** (`/fake-attendance/detect`): Compares supervisor-recorded "present" vs GPS pings (admin only)
- **Leader vs Supervisor** (`/fake-attendance/detect-leader-vs-supervisor`): Compares leader and supervisor attendance for same guards, flags discrepancies with names

---

## Running the Projects

### Backend
```bash
cd "security fast api project"
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd securetrack_app
flutter pub get
flutter run
```

### API Base URL
Configured in `lib/core/constants.dart` — typically `http://<IP>:8000/api/v1`

---

## Common Tasks

### Adding a new API endpoint
1. Create/modify route in `app/api/v1/<module>.py`
2. If new model needed: create in `app/models/`, register in `app/models/__init__.py`
3. Register router in `app/main.py`

### Adding a new Flutter screen
1. Create screen in `lib/screens/<role>/`
2. Create/update provider in `lib/data/providers/`
3. Add route in `lib/app.dart`
4. Add translations in `lib/core/l10n/translations_en.dart` + `translations_ar.dart`
5. Update drawer if needed (`lib/widgets/<role>_drawer.dart`)

### Adding a new role
1. Add to `UserRole` enum in `app/enums.py`
2. Create shell, drawer, bottom nav in Flutter
3. Add ShellRoute in `app.dart`
4. Add login redirect in `login_screen.dart`
5. Add role option in `register_screen.dart`
6. Update `require_role()` calls on endpoints the role needs access to
