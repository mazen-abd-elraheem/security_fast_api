"""
Migration: Add total_outside_seconds column to attendance_logs.
Tracks accumulated time a guard spent outside the geofence during their shift.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "securetrack.db")


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if column already exists
    cursor.execute("PRAGMA table_info(attendance_logs)")
    columns = [row[1] for row in cursor.fetchall()]

    if "total_outside_seconds" not in columns:
        cursor.execute(
            "ALTER TABLE attendance_logs ADD COLUMN total_outside_seconds FLOAT NOT NULL DEFAULT 0"
        )
        conn.commit()
        print("[OK] Added 'total_outside_seconds' column to attendance_logs")
    else:
        print("[INFO] Column 'total_outside_seconds' already exists - skipping")

    conn.close()


if __name__ == "__main__":
    migrate()
