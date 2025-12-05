"""Email notification functions."""

import os
import logging
import smtplib
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, List

from config import EMAIL_CONFIG

logger = logging.getLogger(__name__)


def send_email_notification(
    subject: str,
    message: str,
    attachments: Optional[List[str]] = None
) -> None:
    """Send email notification about pipeline results."""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG['username']
        msg['To'] = EMAIL_CONFIG['recipient']
        msg['Subject'] = subject

        msg.attach(MIMEText(message, 'plain'))

        # Attach files if provided
        if attachments:
            for path in attachments:
                try:
                    if not path or not os.path.exists(path):
                        continue
                    ctype, _ = mimetypes.guess_type(path)
                    maintype, subtype = (ctype.split('/', 1) if ctype else ('application', 'octet-stream'))
                    with open(path, 'rb') as f:
                        part = MIMEBase(maintype, subtype)
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(path)}"')
                    msg.attach(part)
                except (OSError, IOError, ValueError) as e:
                    logger.warning("Failed to attach file %s: %s", path, e)

        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'], timeout=15)
        server.starttls()
        server.login(EMAIL_CONFIG['username'], EMAIL_CONFIG['password'])
        server.send_message(msg)
        server.quit()

        logger.info("Email notification sent successfully")

    except (smtplib.SMTPException, OSError) as e:
        logger.error("Failed to send email notification: %s", e)
