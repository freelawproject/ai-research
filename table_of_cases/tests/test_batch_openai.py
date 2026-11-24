import json

from toc.batch_openai import (
    custom_id,
    page_of,
    read_result_line,
    request_body,
    response_text,
)


def test_custom_id_round_trips():
    assert custom_id(41) == "page-0041"
    assert page_of(custom_id(1336)) == 1336


def test_request_body_carries_the_image_prompt_and_schema():
    body = request_body(
        "some-model", "table_of_cases_v3", "low", "original", b"abc", "image/jpeg"
    )
    image, text = body["input"][0]["content"]
    assert image == {
        "type": "input_image",
        "image_url": "data:image/jpeg;base64,YWJj",
        "detail": "original",
    }
    assert text["text"].startswith("You are transcribing")
    assert body["model"] == "some-model"
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True


def test_response_text_joins_the_output_text_parts():
    body = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": '{"a":'},
                    {"type": "output_text", "text": "1}"},
                ],
            },
        ]
    }
    assert response_text(body) == '{"a":1}'
    assert response_text({}) == ""


def line(custom, response, error=None):
    return json.dumps({"custom_id": custom, "response": response, "error": error})


def test_a_completed_result_line_gives_the_page_and_body():
    body = {"status": "completed", "output": []}
    result = read_result_line(line("page-0041", {"status_code": 200, "body": body}))
    assert result == (41, body, None)


def test_a_failed_request_line_has_no_body():
    page, body, problem = read_result_line(
        line("page-0042", None, {"code": "batch_expired", "message": "too late"})
    )
    assert (page, body) == (42, None)
    assert "batch_expired" in problem


def test_a_cut_off_response_keeps_its_body_and_reports_why():
    body = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }
    page, kept, problem = read_result_line(
        line("page-0043", {"status_code": 200, "body": body})
    )
    assert (page, kept) == (43, body)
    assert "max_output_tokens" in problem


def test_response_text_tolerates_missing_parts():
    assert response_text({"output": None}) == ""
    assert response_text({"output": [{"type": "message", "content": None}]}) == ""
