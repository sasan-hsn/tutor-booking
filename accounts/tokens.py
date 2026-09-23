import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

logger = logging.getLogger(__name__)
User = get_user_model()


class EmailVerificationTokenGenerator:
    """
    Stateless email verification token generator using Django's TimestampSigner.
    Tokens are signed with a salt derived from the user's password hash, ensuring
    any credential change immediately invalidates outstanding verification links.
    """
    SALT_PREFIX = 'accounts.email-verification'
    TOKEN_TTL = 60 * 60 * 24  # 24 hours (86,400 seconds)

    def _get_salt(self, user) -> str:
        return f"{self.SALT_PREFIX}:{user.password}"

    def make_token(self, user, email: str | None = None) -> str:
        """
        Generate a stateless signed token encoding user.id and target email.
        Token format: <base64_payload>:<timestamp>:<signature>
        """
        target_email = (email or user.email or '').strip().lower()
        payload = f"{user.pk}:{target_email}"
        encoded_payload = urlsafe_base64_encode(force_bytes(payload))
        signer = TimestampSigner(salt=self._get_salt(user))
        return signer.sign(encoded_payload)

    def check_token(self, token: str) -> tuple[User | None, str]:
        """
        Validate token and return (user, status).
        Status codes:
        - 'valid': Signature matches, within TTL, password unchanged, email matches.
        - 'expired': Signature was authentic but link is older than TOKEN_TTL.
        - 'invalid': Malformed, bad signature, nonexistent user, or password/email changed.
        """
        if not token or not isinstance(token, str):
            return None, 'invalid'

        parts = token.split(':')
        if len(parts) != 3:
            return None, 'invalid'

        try:
            encoded_payload = parts[0]
            decoded_payload = force_str(urlsafe_base64_decode(encoded_payload))
            user_id_str, email = decoded_payload.split(':', 1)
            user_id = int(user_id_str)
        except Exception:
            return None, 'invalid'

        user = User.objects.filter(pk=user_id).first()
        if not user:
            return None, 'invalid'

        signer = TimestampSigner(salt=self._get_salt(user))

        try:
            verified_payload = signer.unsign(token, max_age=self.TOKEN_TTL)
        except SignatureExpired:
            return user, 'expired'
        except BadSignature:
            return None, 'invalid'

        try:
            decoded_verified = force_str(urlsafe_base64_decode(verified_payload))
            verified_id_str, verified_email = decoded_verified.split(':', 1)
            if int(verified_id_str) != user.pk or verified_email.lower() != (user.email or '').lower():
                return None, 'invalid'
        except Exception:
            return None, 'invalid'

        return user, 'valid'


email_verification_token_generator = EmailVerificationTokenGenerator()
