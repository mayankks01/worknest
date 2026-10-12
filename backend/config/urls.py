from django.urls import include, path

from foundation.views import live, ready
from organizations.team import accept, preview

urlpatterns = [
    path("api/v1/invitations/preview/", preview),
    path("api/v1/invitations/accept/", accept),
    path("api/v1/orgs/", include("organizations.urls")),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/health/live/", live, name="health-live"),
    path("api/v1/health/ready/", ready, name="health-ready"),
]
