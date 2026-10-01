from django.core import signing


VERIFICATION_SALT = "email-verification"


def generate_email_verification_token(user):
    data = {
        "user_id": user.id,
        "email": user.email,
    }

    return signing.dumps(data, salt=VERIFICATION_SALT)


def verify_email_verification_token(token, max_age=60 * 60 * 24):
    """
    Token valid for 24 hours.
    """
    return signing.loads(
        token,
        salt=VERIFICATION_SALT,
        max_age=max_age,
    )

def generate_password_reset_token(user):
    data = {
        "user_id": user.id,
        "email": user.email,
    }

    return signing.dumps(
        data,
        salt="password-reset",
    )


def verify_password_reset_token(token, max_age=60 * 60):
    """
    Password reset token valid for 1 hour.
    """
    return signing.loads(
        token,
        salt="password-reset",
        max_age=max_age,
    )
