#!/usr/bin/env python3
"""Send a centcom alert email over SMTP (standard library only).

Shared by dns-failover.py, alert-watch.py and backup.sh.

    alerts.py "subject" < body.txt

Settings come from .env: SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = TLS),
SMTP_USERNAME, SMTP_PASSWORD, DNS_ALERT_FROM and DNS_ALERT_TO (comma
separated). The DNS_ names predate the other alerts; every alert uses them.
Exits 0 when the message was sent and 1 when it was skipped or failed.
"""
import email.message
import importlib.util
from pathlib import Path
import smtplib
import sys

ROOT = Path(__file__).resolve().parent.parent


def recipients(env):
    return [a.strip() for a in env.get("DNS_ALERT_TO", "").split(",") if a.strip()]


def configured(env):
    return bool(env.get("DNS_ALERT_FROM") and recipients(env) and env.get("SMTP_HOST"))


def send_email(env, subject, body):
    sender, to = env.get("DNS_ALERT_FROM"), recipients(env)
    host, user, password = env.get("SMTP_HOST"), env.get("SMTP_USERNAME"), env.get("SMTP_PASSWORD")
    port = int(env.get("SMTP_PORT") or 587)
    if not configured(env):
        print("email skipped: set DNS_ALERT_FROM, DNS_ALERT_TO, SMTP_HOST (and SMTP_USERNAME/SMTP_PASSWORD)", file=sys.stderr)
        return False
    message = email.message.EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, ", ".join(to), subject
    message.set_content(body)
    try:
        if port == 465:
            client = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            client = smtplib.SMTP(host, port, timeout=20)
            client.starttls()
        with client:
            if user:
                client.login(user, password or "")
            client.send_message(message)
        return True
    except (smtplib.SMTPException, OSError) as error:
        print(f"email failed: {error}", file=sys.stderr)
    return False


def settings():
    spec = importlib.util.spec_from_file_location("centcom_services", ROOT / "scripts" / "services.py")
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    return registry.read_env(ROOT / ".env", environment={})


def main(argv):
    if len(argv) != 2:
        print("usage: alerts.py SUBJECT < body.txt", file=sys.stderr)
        return 2
    return 0 if send_email(settings(), argv[1], sys.stdin.read()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
