import os
import logging

from google import genai
from google.genai import types

from pydantic import BaseModel, Field, constr
from typing import List, Literal, Optional


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

DEFAULT_MODEL_ID = "gemini-3.1-pro-preview"
TEMPERATURE = 0.1

MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
]


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
    direction: Literal["Direct History", "Citing Reference", "Related Reference"] = Field(
        ...,
        description="The direction of the Cited Case in relation to the Citing Case.",
    )
    isFlagged: bool = Field(
        ...,
        description="Whether the Cited Case is flagged for treatment beyond a simple citation.",
    )
    quote: Optional[constr(max_length=500)] = Field(
        None,
        description="The verbatim passage from the opinion that most directly supports the flagging determination. Set to null if no suitable passage exists.",
    )
    rationale: constr(max_length=300) = Field(
        ...,
        description="A 1-2 sentence explanation of why the case is or is not flagged, connecting the quote to the determination.",
    )



class CitatorOutput(BaseModel):
    citedCases: List[CitedCase] = Field(
        ...,
        description="A list of Cited Cases identified in the opinion.",
    )


def gemini_completion(
    system_prompt,
    opinion_text,
    model_id=DEFAULT_MODEL_ID,
    temperature=TEMPERATURE,
    response_schema=CitatorOutput,
):
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=f"<opinion>\n{opinion_text}\n</opinion>",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=response_schema,
                thinking_config=types.ThinkingConfig(thinking_level="low"),
            ),
        )

        parsed = response.parsed
        cited_cases = [case.model_dump() for case in parsed.citedCases] if parsed else []
        usage = response.usage_metadata

        return {
            "model": model_id,
            "stop_reason": str(response.candidates[0].finish_reason) if response.candidates else "unknown",
            "input_tokens": usage.prompt_token_count if usage else 0,
            "output_tokens": usage.candidates_token_count if usage else 0,
            "cached_tokens": usage.cached_content_token_count if usage and usage.cached_content_token_count else 0,
            "cited_cases": cited_cases,
        }

    except Exception as e:
        logging.warning(f"gemini_completion failed: {e}")
        return {}
