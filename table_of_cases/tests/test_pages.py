from toc.config import OUTPUTS_DIR
from toc.pages import engine_dir, parse_page_range


def test_parse_page_range():
    assert parse_page_range("20") == [20]
    assert parse_page_range("21-24") == [21, 22, 23, 24]


def test_engine_dir_names_model_prompt_version_and_scan():
    path = engine_dir("vendor/model:1", "table_of_cases_v1", "original")
    assert path == OUTPUTS_DIR / "vendor-model-1__v1__original"
