#!/usr/bin/env python3

"""
Script to send failure notification emails for NHC article processing.

Called only by management/run_monitor_cron.sh, and only when run_monitor.py exits
non-zero. The body carries the last 50 log lines, which is why it goes to the
`alert` list rather than the digest distribution: raw English tracebacks are for
whoever fixes it, not for everyone who reads the digest.
"""

import smtplib
import sys
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recipients import load_recipients, RecipientConfigError  # noqa: E402

def send_failure_email(error_message, log_file_path=None):
    """Send failure notification directly via Gmail SMTP."""

    load_dotenv()
    gmail_addr = os.getenv("GMAIL_ADDR")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))

    subject = "❌ NHC Article Processing Failed"

    if not gmail_addr or not gmail_app_password:
        print("❌ GMAIL_ADDR / GMAIL_APP_PASSWORD not set in .env")
        return False
    try:
        recipients = load_recipients("alert")
    except RecipientConfigError as e:
        print(f"❌ 无法确定报错通知的收件人：{e}")
        return False

    # Create email body
    body = f"""
NHC Article Processing Failure Notification
=========================================

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Error: {error_message}

"""
    
    # Add log file content if available
    if log_file_path and os.path.exists(log_file_path):
        body += f"""
Recent Log Entries:
-------------------

"""
        try:
            # Get last 50 lines of the log file
            with open(log_file_path, 'r') as f:
                lines = f.readlines()
                recent_lines = lines[-50:] if len(lines) > 50 else lines
                body += ''.join(recent_lines)
        except Exception as e:
            body += f"Could not read log file: {e}\n"
    
    body += f"""
-------------------
This is an automated notification from the NHC Article Processing System.
Please check the system and logs for more details.
"""
    
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = formataddr((str(Header("NHC Article Monitor", "utf-8")), gmail_addr))
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = str(Header(subject, "utf-8"))

        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=60) as server:
            server.login(gmail_addr, gmail_app_password)
            server.sendmail(gmail_addr, recipients, msg.as_string())

        print(f"✅ Failure notification sent to {', '.join(recipients)}")
        return True

    except Exception as e:
        print(f"❌ Error sending failure notification: {e}")
        return False

def main():
    """Main function to handle command line usage"""
    if len(sys.argv) < 2:
        print("Usage: python3 send_failure_notification.py <error_message> [log_file_path]")
        sys.exit(1)
    
    error_message = sys.argv[1]
    log_file_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    success = send_failure_email(error_message, log_file_path)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
