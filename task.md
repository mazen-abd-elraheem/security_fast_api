# Phase 3 — Task List

## Guard Dashboard & Schedule Removal
- [x] Move permission gate from `guard_dashboard_screen.dart` into `guard_shell.dart`
- [x] Remove unused `GuardDashboardScreen` import from `app.dart`
- [x] Delete `guard_dashboard_screen.dart`
- [x] Delete `guard_schedule_screen.dart`
- [x] Remove orphaned `GuardScheduleItem` + `guardScheduleProvider` from `guard_providers.dart`

## Hardcoded String Cleanup
- [x] Fix `admin_dashboard_screen.dart` — CSV export messages → `t(ref, ...)`

## Deprecated `withOpacity()` Fix (31 occurrences → 0)
- [x] `guard_report_incident_screen.dart` (5)
- [x] `tactical_screen.dart` (3)
- [x] `create_site_screen.dart` (6)
- [x] `edit_site_screen.dart` (6)
- [x] `admin_roster_screen.dart` (2)
- [x] `admin_guard_photos_screen.dart` (3)
- [x] `admin_sites_screen.dart` (1)
- [x] `guard_checkin_screen.dart` (1)
- [x] `guard_camera_screen.dart` (1)
- [x] `login_screen.dart` (2)
- [x] `st_clock.dart` (1)

## Guard Check-in Screen Consistency
- [x] Replace raw `AppBar` with `STAppBar` + translation key
- [x] Remove unused `go_router` import
