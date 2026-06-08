from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
import boto3

@dataclass(frozen=True)
class VerificationEmail:
    to_email: str
    code: str
    expires_minutes: int
    verification_url: str | None = None


class EmailSender:
    def send_verification_email(self, email: VerificationEmail) -> None:
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    def send_verification_email(self, email: VerificationEmail) -> None:
        logging.getLogger(__name__).warning(
            "Email verification for %s: code=%s url=%s expires_minutes=%s",
            email.to_email,
            email.code,
            email.verification_url,
            email.expires_minutes,
        )
        print(
            (
                f"SNYF email verification for {email.to_email}: "
                f"code={email.code} "
                f"url={email.verification_url or '-'} "
                f"expires_minutes={email.expires_minutes}"
            ),
            file=sys.stderr,
            flush=True,
        )


class SesEmailSender(EmailSender):
    def __init__(self, from_address: str) -> None:
        self.from_address = from_address

    def send_verification_email(self, email: VerificationEmail) -> None:


        subject = "Verify your Snyf email"
        lines = [
            "Welcome to Snyf.",
            "",
            f"Your verification code is {email.code}.",
            f"It expires in {email.expires_minutes} minutes.",
        ]
        if email.verification_url:
            lines.extend(["", f"Or verify with this link: {email.verification_url}"])
        boto3.client("ses").send_email(
            Source=self.from_address,
            Destination={"ToAddresses": [email.to_email]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": "\n".join(lines)}},
            },
        )


def create_email_sender() -> EmailSender:
    from_address = os.getenv("SNYF_EMAIL_FROM") or os.getenv("EMAIL_FROM_ADDRESS")
    if from_address:
        return SesEmailSender(from_address)
    return ConsoleEmailSender()
