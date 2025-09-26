import os
import json
import logging
from textwrap import dedent

from openai import OpenAI

from pydantic import BaseModel, Field, constr
from typing import List, Literal


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MAX_TOKENS = 1000
TEMPERATURE = 0.1


class DocketItem(BaseModel):
    unique_id: constr(max_length=10) = Field(
        ..., description="Unique identifier for the case."
    )
    cleaned_nums: List[constr(max_length=100)] = Field(
        ..., description="A list of cleaned and standardized docket numbers."
    )

    class Config:
        extra = "forbid"  # equivalent to additionalProperties=false


class CleanDocketNumber(BaseModel):
    docket_numbers: List[DocketItem] = Field(
        ..., description="A list of extracted items."
    )

    class Config:
        extra = "forbid"  # equivalent to additionalProperties=false


def format_prompt(prompt):
    return dedent(prompt).replace(" \n", "\n").strip()


def gpt_completion(
    user_message,
    system_prompt,
    model_id,
    max_tokens=MAX_TOKENS,
    temperature=TEMPERATURE,
    response_format=CleanDocketNumber,
):
    try:
        api_key = os.getenv("OPENAI_KEY")
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.parse(
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
            "cached_tokens": response.usage.prompt_tokens_details.cached_tokens,
            "output_tokens": response.usage.completion_tokens,
            "raw_results": raw_results,
        }

        return completion

    except Exception as e:
        logging.warning(f"gpt_completion failed: {e}")
        return dict()
