import logging
from textwrap import dedent

from openai import OpenAI

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_KEY = "<YOUR API KEY>"
REASONING = ["o1-mini-2024-09-12", "o3-mini-2025-01-31", "o1-2024-12-17"]
GPT = ["gpt-4o-mini-2024-07-18", "gpt-4o-2024-11-20"]
MODELS = REASONING + GPT
MAX_TOKENS = 1024
TEMPERATURE = 0.1
RESPONSE_FORMAT = {"type": "json_object"}
REASONING_MAX_TOKENS = 5000 + MAX_TOKENS

def format_prompt(prompt):
    return dedent(prompt).replace(" \n", "\n").strip()


def gpt_completion(
    model_id,
    system_prompt,
    user_message,
    api_key=API_KEY,
    max_tokens=MAX_TOKENS,
    temperature=TEMPERATURE,
    response_format=RESPONSE_FORMAT,
    reasoning_max_tokens=REASONING_MAX_TOKENS,
):
    try:
        assert model_id in MODELS
        client = OpenAI(api_key=api_key)

        if model_id in REASONING:
            response = client.chat.completions.create(
            model=model_id,
            max_completion_tokens=reasoning_max_tokens,
            messages=[
                {"role": "user", "content": f"{format_prompt(system_prompt)} \n {format_prompt(user_message)}"},
            ],
        )
            
        else:
            response = client.chat.completions.create(
                model=model_id,
                response_format=response_format,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": f"{format_prompt(system_prompt)}"},
                    {"role": "user", "content": f"{format_prompt(user_message)}"},
                ],
            )

        raw_results = response.choices[0].message.content

        completion = {
            "model": response.model,
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens + response.usage.completion_tokens_details.reasoning_tokens,
            "raw_results": raw_results,
        }

        return completion

    except AssertionError as err:
        logger.error(f"Assertion error: {err}")
        return dict()

    except Exception as e:
        logging.warning(f"gpt_completion failed: {e}")
        return dict()
