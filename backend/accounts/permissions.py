from rest_framework.permissions import BasePermission


class HasVerifiedEmail(BasePermission):
    """Required in addition to tenant permissions for future create/join actions."""

    message = "Verify your email before creating or joining an organization."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.verified_at
        )
