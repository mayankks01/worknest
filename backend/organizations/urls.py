from django.urls import include, path

from . import team, views

urlpatterns = [
    path("<uuid:org_id>/", include("projects.urls")),
    path("", views.collection),
    path("<uuid:org_id>/", views.detail),
    path("<uuid:org_id>/members/", team.members),
    path("<uuid:org_id>/members/<uuid:member_id>/", team.member_detail),
    path("<uuid:org_id>/invitations/", team.invitations),
    path(
        "<uuid:org_id>/invitations/<uuid:invitation_id>/resend/",
        team.invitation_action,
        {"action": "resend"},
    ),
    path(
        "<uuid:org_id>/invitations/<uuid:invitation_id>/revoke/",
        team.invitation_action,
        {"action": "revoke"},
    ),
    path("<uuid:org_id>/transfer-ownership/", team.transfer),
]
