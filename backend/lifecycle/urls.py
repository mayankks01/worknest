from django.urls import path

from . import views

urlpatterns = [
    path("search/", views.search),
    path("exports/", views.exports),
    path("exports/<uuid:export_id>/", views.export_detail),
    path("exports/<uuid:export_id>/download/", views.download),
    path("deletion/", views.deletion),
]
