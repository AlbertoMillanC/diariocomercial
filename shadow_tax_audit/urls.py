from django.urls import path
from . import views

urlpatterns = [
    path("ingest/", views.webhook_pos_ingest, name="shadow_pos_ingest"),
]
