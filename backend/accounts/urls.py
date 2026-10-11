from django.urls import path

from . import views

urlpatterns = [
    path("session/", views.session),
    path("me/", views.me),
    path("register/", views.register),
    path("login/", views.sign_in),
    path("logout/", views.sign_out),
    path("verify-email/", views.verify_email),
    path("resend-verification/", views.resend),
    path("password-reset/", views.reset_request),
    path("password-reset/confirm/", views.reset_confirm),
]
