from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.inbox),
    path("notifications/preferences/", views.preferences),
    path("notifications/<uuid:notification_id>/", views.mark_read),
    path("audit/", views.audit),
    path("operations/", views.operations),
    path("operations/<str:kind>/<uuid:job_id>/retry/", views.retry),
]
