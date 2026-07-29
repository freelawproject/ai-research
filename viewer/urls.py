from django.urls import path

from viewer import align_views, views

urlpatterns = [
    path("", views.home, name="home"),  # type: ignore[arg-type]
    path(
        "align/",
        align_views.align_home,  # type: ignore[arg-type]
        name="align_home",
    ),
    path(
        "align/<str:dataset>/<str:page>/",
        align_views.align_page,  # type: ignore[arg-type]
        name="align_page",
    ),
    path(
        "align/img/<str:dataset>/<str:page>.png",
        align_views.align_page_img,  # type: ignore[arg-type]
        name="align_page_img",
    ),
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
        "review/<str:dataset>/<str:category>/",
        views.review,  # type: ignore[arg-type]
        name="review",
    ),
    path(
        "compare/<str:dataset>/<str:page>/",
        views.route_compare,  # type: ignore[arg-type]
        name="route_compare",
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
    path(
        "crop/<str:dataset>/<str:page>/<str:bbox>.png",
        views.crop_img,  # type: ignore[arg-type]
        name="crop_img",
    ),
    path(
        "pagecrop/<str:dataset>/<str:page>/<str:bbox>.png",
        views.page_crop,  # type: ignore[arg-type]
        name="page_crop",
    ),
]
