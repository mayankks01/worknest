import json
from datetime import timedelta
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from redis.exceptions import RedisError

from .models import AccountToken, User, normalize_email
from .services import issue_token, valid_token
from .throttle import check_rate

GENERIC_EMAIL = (
    "If this address is eligible, an email will arrive shortly. "
    "Check your inbox or try again later."
)


class InputError(Exception):
    def __init__(self, details):
        self.details = details


def error(request, code, message, status=400, details=None):
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": request.request_id,
                "details": details or {},
            }
        },
        status=status,
    )


def csrf_failure(request, reason=""):
    return error(request, "CSRF_FAILED", "Your security token expired. Refresh and try again.", 403)


def endpoint(method, fields=(), scope=None, *, text_only=True):
    def decorate(view):
        @wraps(view)
        @sensitive_post_parameters()
        def wrapped(request, *args, **kwargs):
            if request.method not in method.split(", "):
                response = error(request, "METHOD_NOT_ALLOWED", "Method not allowed.", 405)
                response["Allow"] = method
                return response
            try:
                data = {}
                if request.method in ("POST", "PATCH") or (
                    not text_only and request.method == "DELETE"
                ):
                    if request.content_type != "application/json":
                        return error(request, "UNSUPPORTED_MEDIA_TYPE", "Send a JSON body.", 415)
                    data = json.loads(request.body)
                    if not isinstance(data, dict) or any(key not in fields for key in data):
                        raise InputError({"body": ["Unexpected fields or invalid JSON object."]})
                    if text_only and any(not isinstance(value, str) for value in data.values()):
                        raise InputError({"body": ["All fields must be text."]})
                    if scope:
                        email = normalize_email(data.get("email", ""))[:254]
                        wait = check_rate(request, scope, email)
                        if wait:
                            response = error(
                                request, "RATE_LIMITED", "Too many attempts. Try again later.", 429
                            )
                            response["Retry-After"] = str(wait)
                            return response
                return view(request, data, *args, **kwargs)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return error(request, "VALIDATION_ERROR", "Send a valid JSON object.")
            except RequestDataTooBig:
                return error(request, "PAYLOAD_TOO_LARGE", "Request is too large.", 413)
            except InputError as exc:
                return error(
                    request,
                    "VALIDATION_ERROR",
                    "Please check the highlighted fields.",
                    details=exc.details,
                )
            except (DatabaseError, RedisError, OSError):
                response = error(
                    request,
                    "DEPENDENCY_UNAVAILABLE",
                    "A required service is unavailable. Try again shortly.",
                    503,
                )
                response["Retry-After"] = "5"
                return response

        return wrapped

    return decorate


def email_input(data):
    email = normalize_email(data.get("email", ""))
    try:
        validate_email(email)
        if len(email) > 254:
            raise ValidationError("Email is too long.")
    except ValidationError:
        raise InputError({"email": ["Enter a valid email address."]}) from None
    return email


def password_input(data, user):
    password = data.get("password", "")
    if len(password) > 128:
        raise InputError({"password": ["Use at most 128 characters."]})
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise InputError({"password": exc.messages}) from None
    return password


def user_data(user):
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "verified_at": user.verified_at.isoformat() if user.verified_at else None,
    }


@endpoint("GET")
def session(request, data):
    return JsonResponse(
        {
            "csrf_token": get_token(request),
            "user": user_data(request.user) if request.user.is_authenticated else None,
        }
    )


@endpoint("POST", ("email", "password", "display_name"), "register")
def register(request, data):
    email = email_input(data)
    name = data.get("display_name", "").strip()
    if not 1 <= len(name) <= 80:
        raise InputError({"display_name": ["Use between 1 and 80 characters."]})
    user = User(email=email, display_name=name)
    password = password_input(data, user)
    # Hash for both existing and new users; avoid cheap duplicate-account timing.
    user.set_password(password)
    try:
        with transaction.atomic():
            user.save(force_insert=True)
            issue_token(user, "verify")
    except IntegrityError:
        if not User.objects.filter(email=email).exists():
            raise
    return JsonResponse({"message": GENERIC_EMAIL}, status=202)


@endpoint("POST", ("email", "password"), "login")
def sign_in(request, data):
    email = email_input(data)
    password = data.get("password", "")
    if not password or len(password) > 128:
        return error(request, "INVALID_CREDENTIALS", "Email or password is incorrect.", 401)
    user = authenticate(request, username=email, password=password)
    if user is None:
        return error(request, "INVALID_CREDENTIALS", "Email or password is incorrect.", 401)
    login(request, user)
    request.session.cycle_key()
    request.session.set_expiry(timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE))
    return JsonResponse({"user": user_data(user), "csrf_token": get_token(request)})


@endpoint("POST")
def sign_out(request, data):
    logout(request)
    return JsonResponse({"message": "You are signed out.", "csrf_token": get_token(request)})


@endpoint("GET")
def me(request, data):
    if not request.user.is_authenticated:
        return error(request, "UNAUTHENTICATED", "Sign in to continue.", 401)
    return JsonResponse({"user": user_data(request.user)})


def request_email(data, purpose):
    email = email_input(data)
    with transaction.atomic():
        user = User.objects.select_for_update().filter(email=email, is_active=True).first()
        if user and (purpose == "reset" or not user.verified_at):
            issue_token(user, purpose)
    return JsonResponse({"message": GENERIC_EMAIL}, status=202)


@endpoint("POST", ("email",), "resend")
def resend(request, data):
    return request_email(data, "verify")


@endpoint("POST", ("email",), "reset")
def reset_request(request, data):
    return request_email(data, "reset")


def consume(request, data, purpose):
    try:
        token_id, secret = data.get("token", "").split(".", 1)
        token_id = UUID(token_id)
        if len(secret) != 64:
            raise ValueError
    except ValueError:
        return error(
            request, "INVALID_TOKEN", "This link is invalid or expired. Request a new one."
        )
    # Always lock user before token, matching issue_token and serializing races.
    with transaction.atomic():
        candidate = AccountToken.objects.filter(pk=token_id).values("user_id").first()
        if candidate:
            user = User.objects.select_for_update().get(pk=candidate["user_id"])
            token = AccountToken.objects.select_for_update().get(pk=token_id)
        if not candidate or not valid_token(token, user, secret, purpose):
            return error(
                request, "INVALID_TOKEN", "This link is invalid or expired. Request a new one."
            )
        if purpose == "reset":
            user.set_password(password_input(data, user))
            user.save(update_fields=["password"])
            # All issued links are invalidated by the password change. Session hashes
            # reject every old session on its next request, including other browsers.
            AccountToken.objects.filter(user=user, used_at=None).update(used_at=timezone.now())
        else:
            user.verified_at = timezone.now()
            user.save(update_fields=["verified_at"])
            token.used_at = timezone.now()
            token.save(update_fields=["used_at"])
    if purpose == "reset":
        logout(request)
    return JsonResponse(
        {
            "message": "Password updated. Sign in again."
            if purpose == "reset"
            else "Email verified. You can now sign in.",
            "csrf_token": get_token(request),
        }
    )


@endpoint("POST", ("token",), "consume")
def verify_email(request, data):
    return consume(request, data, "verify")


@endpoint("POST", ("token", "password"), "consume")
def reset_confirm(request, data):
    return consume(request, data, "reset")
