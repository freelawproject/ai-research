import os
import logging

from openai import OpenAI

from pydantic import BaseModel, Field, constr
from typing import List, Literal, Optional


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DEFAULT_MODEL_ID = "gpt-4.1-mini-2025-04-14"
TEMPERATURE = 0.1


class CitedCase(BaseModel):
    mainCitationString: Optional[constr(max_length=20)] = Field(
        None,
        description="Citation string identifier for the Cited Case in the format 'reporter-volume reporter reporter-page'. Set to null if the Cited Case is not cited by its Full Citation.",
    )
    caseName: Optional[constr(max_length=100)] = Field(
        None,
        description="Case name for the Cited Case. Set to null if the Cited Case is not mentioned by name.",
    )
    actingCase: constr(max_length=100) = Field(
        ...,
        description="The case that applies treatment to the Cited Case. Set to 'Citing Case' if the Acting Case is the Citing Case itself. Set to the name or citation of the Acting Case if it is a different case. Set to 'Implicit' if the Acting Case is not explicitly named.",
    )
    caseHistory: Literal["Direct History", "Citing Reference", "Related Reference"] = Field(
        ...,
        description="The case history type of the Cited Case.",
    )
    treatment: Optional[constr(max_length=100)] = Field(
        None,
        description="The treatment applied to the Cited Case.",
    )
    opinionType: constr(max_length=50) = Field(
        ...,
        description="The opinion type in which the Cited Case is referenced.",
    )
    quote: Optional[constr(max_length=500)] = Field(
        None,
        description="The verbatim passage from the opinion that most directly supports the assigned treatment. Set to null if no suitable passage exists.",
    )
    rationale: constr(max_length=300) = Field(
        ...,
        description="A 1-2 sentence explanation of why the quoted passage supports the assigned treatment.",
    )

    class Config:
        extra = "forbid"


class CitatorOutput(BaseModel):
    numCitedCases: int = Field(
        ...,
        description="The total number of unique Cited Cases identified in the opinion.",
    )
    citedCases: List[CitedCase] = Field(
        ...,
        description="A list of Cited Cases identified in the opinion.",
    )

    class Config:
        extra = "forbid"


def gpt_completion(
    system_prompt,
    opinion_text,
    model_id=DEFAULT_MODEL_ID,
    temperature=TEMPERATURE,
    response_format=CitatorOutput,
):
    try:
        api_key = os.getenv("OPENAI_KEY")
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.parse(
            model=model_id,
            response_format=response_format,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"<opinion>\n{opinion_text}\n</opinion>"},
            ],
        )

        parsed = response.choices[0].message.parsed
        cited_cases = [case.model_dump() for case in parsed.citedCases] if parsed else []

        return {
            "model": response.model,
            "stop_reason": response.choices[0].finish_reason,
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "cited_cases": cited_cases,
        }

    except Exception as e:
        logging.warning(f"gpt_completion failed: {e}")
        return {}
