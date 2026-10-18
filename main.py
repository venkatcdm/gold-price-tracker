def send_email(
    subject: str,
    plain_body: str,
    html_body: str,
) -> None:
    """Send the report through Gmail SMTP."""
    username = os.environ.get("MAIL_USERNAME", "").strip()
    app_password = os.environ.get("MAIL_APP_PASSWORD", "").replace(" ", "")
    recipient = os.environ.get("RECIPIENT_EMAIL", "").strip()

    missing = [
        name
        for name, value in (
            ("MAIL_USERNAME", username),
            ("MAIL_APP_PASSWORD", app_password),
            ("RECIPIENT_EMAIL", recipient),
        )
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing GitHub Actions secret(s): " + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    smtp_host = (
        os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
        or "smtp.gmail.com"
    )
    smtp_port = int(os.environ.get("SMTP_PORT", "465").strip() or "465")
    use_ssl = (
        os.environ.get("SMTP_USE_SSL", "true").strip().lower()
        not in {"0", "false", "no"}
    )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                server.login(username, app_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(username, app_password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            "Gmail SMTP authentication failed. Verify MAIL_USERNAME is the full Gmail address "
            "and MAIL_APP_PASSWORD is a valid Google App Password generated after enabling "
            "2-Step Verification."
        ) from exc
    except (
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        OSError,
    ) as exc:
        raise RuntimeError(
            f"Could not connect to SMTP server {smtp_host}:{smtp_port}. "
            "Check the Gmail SMTP settings and the GitHub Actions secrets."
        ) from exc
