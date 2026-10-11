from django.urls import path

from . import views

urlpatterns = [
    path("", views.collection),
    path("<uuid:org_id>/", views.detail),
]
