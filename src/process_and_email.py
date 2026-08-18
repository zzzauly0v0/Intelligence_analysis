#!/usr/bin/env python3
"""
Email delivery for the monitor digest (Gmail SMTP).

Summarization used to live here too; it moved to summarizer.py so that adding or
switching a model is a one-line table edit instead of a new class. The recipient
lists then moved to recipients.py / config/recipients.json, for the same reason:
adding a person shouldn't mean editing code. What's left here is getting the mail
out through a flaky SMTP path.
"""

import os
import smtplib
import socket
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from dotenv import load_dotenv
from contextlib import contextmanager

from recipients import load_recipients


@contextmanager
def _force_ipv4():
    """Temporarily make socket.getaddrinfo return only IPv4 results.

    On this WSL2 host IPv6 has no working route, and Python's
    socket.create_connection tries the AAAA (IPv6) address Gmail advertises
    first; the immediate 'Network is unreachable' from that attempt surfaces as
    the connection error instead of falling through to the reachable IPv4
    address (curl/bash succeed because they skip the dead IPv6 and retry IPv4).
    Restricting getaddrinfo to AF_INET for the duration of the SMTP connect
    sidesteps this without touching global state permanently.
    """
    orig = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_only
    try:
        yield
    finally:
        socket.getaddrinfo = orig


class EmailSender:
    """Resolves the recipient list and sends the digest through Gmail SMTP.

    Scraping lives in fetch_monitor_sites.py, orchestration in run_monitor.py,
    summarization in summarizer.py. (The legacy standalone NHC pipeline that used
    to live here was retired after 卫健委 became a regular monitored site.)
    """

    def __init__(self):
        # Both lists come from config/recipients.json via recipients.py, which
        # owns the rules: fail loudly on a missing/empty list, and apply the
        # MONITOR_TEST_RECIPIENTS override to EVERY list (a test send must not
        # leak the regulatory digest to the real RA list).
        # Regulatory news (卫健委, future GRAS/EFSA sources) has its own, smaller
        # list on purpose — it's read by the RA team, not the whole
        # competitor-intelligence distribution.
        self.email_recipients = load_recipients("competitor")
        self.regulatory_recipients = load_recipients("regulatory")

        # Load environment variables
        load_dotenv()

        # Gmail SMTP settings (sending goes directly through Gmail, no local postfix)
        self.gmail_addr = os.getenv("GMAIL_ADDR")
        self.gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
        self.sender_name = os.getenv("SENDER_NAME", "竞品情报监测")
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))

    def send_email(self, subject, body, html_body=None, max_attempts=4,
                   recipients=None):
        """
        Send email directly via Gmail SMTP (no local postfix/mailx needed).

        `body` is the plain-text version (always required, used as fallback).
        If `html_body` is given, the email is sent as multipart/alternative so
        clients that support HTML render the styled version while others fall
        back to plain text.

        `recipients` overrides self.email_recipients for this send — used to mail
        the regulatory digest to its own list.

        The Gmail SMTP connection on this host is flaky — a connect/greeting can
        time out even when the network is otherwise fine, then succeed moments
        later. So transient network/SMTP errors are retried up to `max_attempts`
        times with a short backoff. Authentication failures are NOT retried
        (a bad app password won't fix itself) and abort immediately.
        """
        if not self.gmail_addr or not self.gmail_app_password:
            print("Error sending email: GMAIL_ADDR / GMAIL_APP_PASSWORD not set in .env")
            return False

        to_addrs = recipients if recipients is not None else self.email_recipients
        if not to_addrs:
            print("Error sending email: recipient list is empty")
            return False

        # Build the message once; only the send is retried.
        # "alternative" tells the client both parts are the same content in
        # different formats; it picks the richest it can render (HTML).
        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr((str(Header(self.sender_name, "utf-8")), self.gmail_addr))
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = str(Header(subject, "utf-8"))

        # Order matters: attach plain first, HTML last (last = preferred).
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        raw = msg.as_string()

        for attempt in range(1, max_attempts + 1):
            try:
                with _force_ipv4():
                    with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=60) as server:
                        server.login(self.gmail_addr, self.gmail_app_password)
                        server.sendmail(self.gmail_addr, to_addrs, raw)

                print(f"Email sent successfully to {', '.join(to_addrs)}")
                return True

            except smtplib.SMTPAuthenticationError as e:
                # Credentials are wrong — retrying can't help.
                print(f"Error sending email: authentication failed, not retrying: {e}")
                return False

            except Exception as e:
                if attempt < max_attempts:
                    wait = attempt * 8  # linear backoff: 8s, 16s, 24s...
                    print(f"Send attempt {attempt}/{max_attempts} failed: {e} "
                          f"— retrying in {wait}s")
                    time.sleep(wait)
                else:
                    print(f"Send attempt {attempt}/{max_attempts} failed: {e} "
                          f"— giving up")

        return False


# Backwards-compatible alias: the class was called ArticleProcessor while it also
# did summarization. Kept so any outside script / cron snippet still imports fine.
ArticleProcessor = EmailSender
