from django.urls import path

from viewer import views

urlpatterns = [
    path("", views.home, name="home"),  # type: ignore[arg-type]
]
