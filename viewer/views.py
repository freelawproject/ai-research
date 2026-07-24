from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse


async def home(request: HttpRequest) -> HttpResponse:
    """Landing page: which datasets are present under the data folder."""
    datasets = []
    root = settings.DATASETS_ROOT
    if root.is_dir():
        for path in sorted(p for p in root.iterdir() if p.is_dir()):
            engines = sorted(
                p.name for p in (path / "engines").glob("*") if p.is_dir()
            )
            datasets.append(
                {
                    "name": path.name,
                    "n_golden": len(list((path / "golden").glob("*.json"))),
                    "engines": engines,
                }
            )
    return TemplateResponse(
        request, "viewer/home.html", {"datasets": datasets}
    )
