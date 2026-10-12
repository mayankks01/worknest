from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard),
    path("projects/", views.projects),
    path("projects/<uuid:project_id>/", views.project_detail),
    path("projects/<uuid:project_id>/archive/", views.archive),
    path("projects/<uuid:project_id>/restore/", views.archive, {"restore": True}),
    path("projects/<uuid:project_id>/members/", views.members),
    path("projects/<uuid:project_id>/members/<uuid:member_id>/", views.member_remove),
    path("projects/<uuid:project_id>/tasks/", views.tasks),
    path("tasks/<uuid:task_id>/", views.task_detail),
]
