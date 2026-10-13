from django.urls import path

from . import views


def attachment_collection(request, **kwargs):
    return (
        views.upload(request, **kwargs)
        if request.method == "POST"
        else views.attachments(request, **kwargs)
    )


urlpatterns = [
    path("tasks/<uuid:task_id>/comments/", views.comments),
    path("comments/<uuid:comment_id>/", views.comment_detail),
    path("tasks/<uuid:task_id>/attachments/", attachment_collection),
    path("attachments/<uuid:attachment_id>/", views.attachment_detail),
    path("attachments/<uuid:attachment_id>/download/", views.download),
]
