import logging
from textwrap import dedent

from openai import OpenAI

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_KEY = "<API KEY HERE>"
MODELS = ["gpt-4.1-2025-04-14"]
MAX_TOKENS = 8192
TEMPERATURE = 0.1
RESPONSE_FORMAT = {"type": "json_object"}

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
):
    try:
        assert model_id in MODELS
        client = OpenAI(api_key=api_key)

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
            "output_tokens": response.usage.completion_tokens,
            "raw_results": raw_results,
        }

        return completion

    except AssertionError as err:
        logger.error(f"Assertion error: {err}")
        return dict()

    except Exception as e:
        logging.warning(f"gpt_completion failed: {e}")
        return dict()
