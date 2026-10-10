from django.urls import path

from foundation.views import live, ready

urlpatterns = [
    path("api/v1/health/live/", live, name="health-live"),
    path("api/v1/health/ready/", ready, name="health-ready"),
]
