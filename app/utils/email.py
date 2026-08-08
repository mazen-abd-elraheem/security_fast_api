"""
SecureTrack Platform — Email Utility
Simple email notification helper using SMTP.
Falls back to logging if SMTP is not configured.
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger(__name__)


def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    from_email: Optional[str] = None,
) -> bool:
    """
    Send an email notification.

    In development mode (no SMTP configured), logs the email instead of sending.
    Returns True if the email was sent/logged successfully.
    """
    try:
        from app.core.config import settings

        smtp_host = getattr(settings, "SMTP_HOST", None)
        smtp_port = getattr(settings, "SMTP_PORT", 587)
        smtp_user = getattr(settings, "SMTP_USER", None)
        smtp_pass = getattr(settings, "SMTP_PASSWORD", None)

        sender = from_email or smtp_user or "noreply@securetrack.com"

        if not smtp_host or not smtp_user:
            # Development fallback — just log it
            logger.info(
                f"📧 [EMAIL] To: {to_email} | Subject: {subject}\n"
                f"Body (HTML): {body_html[:200]}..."
            )
            return True

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(sender, to_email, msg.as_string())

        logger.info(f"✅ Email sent to {to_email}: {subject}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to send email to {to_email}: {e}")
        return False


def send_approval_email(to_email: str, user_name: str, assigned_role: str) -> bool:
    """Send account approval notification."""
    subject = "SecureTrack — Your Account Has Been Approved"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #1565C0;">Account Approved ✅</h2>
        <p>Hello <strong>{user_name}</strong>,</p>
        <p>Your SecureTrack account has been <strong>approved</strong> by an administrator.</p>
        <p>You have been assigned the role: <strong>{assigned_role.replace('_', ' ').upper()}</strong></p>
        <p>You can now log in to the SecureTrack platform.</p>
        <hr style="border: 1px solid #eee;">
        <p style="font-size: 12px; color: #999;">SecureTrack Security Platform</p>
    </body>
    </html>
    """
    return send_email(to_email, subject, body)


def send_rejection_email(to_email: str, user_name: str, reason: str = "") -> bool:
    """Send account rejection notification."""
    subject = "SecureTrack — Account Registration Update"
    reason_text = f"<p><strong>Reason:</strong> {reason}</p>" if reason else ""
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #C62828;">Registration Not Approved</h2>
        <p>Hello <strong>{user_name}</strong>,</p>
        <p>We regret to inform you that your SecureTrack account registration has not been approved at this time.</p>
        {reason_text}
        <p>If you believe this is an error, please contact your administrator.</p>
        <hr style="border: 1px solid #eee;">
        <p style="font-size: 12px; color: #999;">SecureTrack Security Platform</p>
    </body>
    </html>
    """
    return send_email(to_email, subject, body)
