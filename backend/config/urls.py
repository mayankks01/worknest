from django.urls import include, path

from foundation.views import live, ready

urlpatterns = [
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/health/live/", live, name="health-live"),
    path("api/v1/health/ready/", ready, name="health-ready"),
]
