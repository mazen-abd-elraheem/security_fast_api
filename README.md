# 🛡️ SecureTrack — Security Field Force Management System

A centralized compliance and workforce management API built with **FastAPI** for security companies. Eliminates **ghost guards** and enforces **GPS-verified supervisor site visits**.

## 🎯 Core Features

- **GPS Geofenced Check-in**: Supervisors must be physically within a site's radius to check in
- **Guard Attendance Tracking**: Real-time presence verification by supervisors
- **Anti-Spoofing**: Device fingerprinting and location validation
- **Incident Reporting**: On-site security incident management with photo evidence
- **Live Dashboard**: Real-time site coverage status (green/yellow/red)
- **Offline Sync**: Cached data push for network dead zones
- **Role-Based Access**: 7 roles from Super Admin to Guard

## 🏗️ Tech Stack

| Component | Technology |
|-----------|-----------|
| **Backend** | FastAPI (Python 3.11+) |
| **Database** | MySQL 8.0 |
| **ORM** | SQLAlchemy 2.0 |
| **Auth** | JWT (python-jose) |
| **Validation** | Pydantic v2 |
| **Container** | Docker + docker-compose |
| **Testing** | pytest + httpx |

## 🚀 Quick Start

### 1. Docker (Recommended)
```bash
docker-compose up --build
```
API available at: `http://localhost:8000/docs`

### 2. Local Development
```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set environment variables
copy .env.example .env

# Start MySQL and run
uvicorn app.main:app --reload
```

### 3. Run Tests
```bash
pytest tests/ -v --tb=short
```

## 📡 API Overview

| Module | Prefix | Description |
|--------|--------|-------------|
| Auth | `/api/v1/auth` | Register, login, token refresh |
| Users | `/api/v1/users` | Profile management, admin CRUD |
| Sites | `/api/v1/sites` | Geofenced site management |
| Shifts | `/api/v1/sites/{id}/shifts` | Guard shift definitions |
| Roster | `/api/v1/roster` | Guard-to-shift scheduling |
| Routes | `/api/v1/routes` | Supervisor daily itineraries |
| **Visits** | `/api/v1/visits` | **GPS-verified check-in/out** |
| Attendance | `/api/v1/attendance` | Guard presence recording |
| Incidents | `/api/v1/incidents` | Security incident reports |
| Dashboard | `/api/v1/dashboard` | Live status & analytics |
| Notifications | `/api/v1/notifications` | Push notifications |
| Devices | `/api/v1/devices` | Device fingerprinting |
| Sync | `/api/v1/sync` | Offline data push |

## 👥 Roles

| Role | Access Level |
|------|-------------|
| `super_admin` | Full system access |
| `admin` | Manage sites, users, shifts, reports |
| `operations_manager` | Oversee supervisors, approve schedules |
| `regional_manager` | Manage within a region |
| `supervisor` | Field visits, attendance, incidents |
| `guard` | View own schedule and attendance |
| `client` | View contracted site reports |

## 📁 Project Structure

```
app/
├── api/v1/          # Route handlers (15 modules)
├── core/            # Config, DB, security, exceptions
├── models/          # SQLAlchemy models (12 tables)
├── schemas/         # Pydantic validation schemas
├── services/        # Business logic layer
database/
├── schema.sql       # Full MySQL schema
tests/               # pytest test suite (14 test files)
docker-compose.yml   # Docker orchestration
```

## 🔐 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `mysql+pymysql://...` | MySQL connection string |
| `SECRET_KEY` | `change-this` | JWT signing key |
| `DEFAULT_GEOFENCE_RADIUS_METERS` | `100` | Default site geofence |
| `OFFLINE_SYNC_MAX_AGE_HOURS` | `24` | Max offline data age |
| `MAX_TRUSTED_DEVICES_PER_USER` | `3` | Device fingerprint limit |

## 📄 License

Proprietary — SecureTrack Platform
