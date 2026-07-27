from django.urls import path

from viewer import views

urlpatterns = [
    path("", views.home, name="home"),  # type: ignore[arg-type]
    path(
        "d/<str:dataset>/",
        views.dataset_redirect,  # type: ignore[arg-type]
        name="dataset",
    ),
    path(
        "go/",
        views.route_go,  # type: ignore[arg-type]
        name="route_go",
    ),
    path(
        "d/<str:dataset>/<str:route>/<str:page>/",
        views.page,  # type: ignore[arg-type]
        name="page",
    ),
    path(
        "img/<str:dataset>/<str:page>.png",
        views.page_img,  # type: ignore[arg-type]
        name="page_img",
    ),
]
