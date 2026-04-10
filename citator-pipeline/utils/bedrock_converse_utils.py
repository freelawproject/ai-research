import json
import logging
import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from json_repair import repair_json

logger = logging.getLogger(__name__)

session = boto3.Session(profile_name="dev-env")
config = Config(read_timeout=1000)

AWS_REGION = "us-west-2"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
TEMPERATURE = 0.1

# Model capabilities for Converse API
MODEL_CAPS = {
    "us.anthropic.claude-sonnet-4-6": {
        "system_prompt": True,
        "tool_use": True,
    },
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "system_prompt": True,
        "tool_use": True,
    },
    "google.gemma-3-27b-it": {
        "system_prompt": False,
        "tool_use": False,
    },
    "us.meta.llama4-maverick-17b-instruct-v1:0": {
        "system_prompt": True,
        "tool_use": False,
    },
    "us.meta.llama3-3-70b-instruct-v1:0": {
        "system_prompt": True,
        "tool_use": False,
    },
    "openai.gpt-oss-120b-1:0": {
        "system_prompt": True,
        "tool_use": False,
    },
    "deepseek.v3.2": {
        "system_prompt": True,
        "tool_use": False,
    },
    "us.deepseek.r1-v1:0": {
        "system_prompt": True,
        "tool_use": False,
    },
    "moonshotai.kimi-k2.5": {
        "system_prompt": False,
        "tool_use": False,
    },
    "qwen.qwen3-next-80b-a3b": {
        "system_prompt": False,
        "tool_use": False,
    },
    "qwen.qwen3-235b-a22b-2507-v1:0": {
        "system_prompt": False,
        "tool_use": False,
    },
}

MODELS = list(MODEL_CAPS.keys())

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Citator Output Schema",
    "description": "Schema for Citator output",
    "type": "object",
    "properties": {
        "citedCases": {
            "type": "array",
            "description": "A list of Cited Cases identified in the opinion.",
            "items": {
                "type": "object",
                "properties": {
                    "mainCitationString": {
                        "type": ["string", "null"],
                        "description": "Citation string identifier for the Cited Case in the format 'reporter-volume reporter reporter-page'. Set to null if the Cited Case is not cited by its Full Citation.",
                        "maxLength": 20,
                    },
                    "caseName": {
                        "type": ["string", "null"],
                        "description": "Case name for the Cited Case. Set to null if the Cited Case is not mentioned by name.",
                        "maxLength": 100,
                    },
                    "actingCase": {
                        "type": "string",
                        "description": "The case that applies treatment to the Cited Case. Set to 'Citing Case' if the Acting Case is the Citing Case itself. Set to the name or citation of the Acting Case if it is a different case. Set to 'Implicit' if the Acting Case is not explicitly named.",
                        "maxLength": 100,
                    },
                    "caseHistory": {
                        "type": "string",
                        "description": "The case history type of the Cited Case.",
                        "enum": ["Direct History", "Citing Reference", "Related Reference"],
                    },
                    "treatment": {
                        "type": ["string", "null"],
                        "description": "The treatment applied to the Cited Case. Must be from the defined treatment lists only.",
                        "maxLength": 100,
                    },
                    "opinionType": {
                        "type": "string",
                        "description": "The opinion type in which the Cited Case is referenced.",
                        "maxLength": 50,
                    },
                    "quote": {
                        "type": ["string", "null"],
                        "description": "The verbatim passage from the opinion that supports the assigned treatment. Must contain enough context for a reviewer to identify and verify the treatment.",
                        "maxLength": 500,
                    },
                    "rationale": {
                        "type": "string",
                        "description": "A 1-2 sentence explanation of why the quoted passage supports the assigned treatment.",
                        "maxLength": 300,
                    },
                },
                "required": [
                    "mainCitationString",
                    "caseName",
                    "actingCase",
                    "caseHistory",
                    "treatment",
                    "opinionType",
                    "quote",
                    "rationale",
                ],
            },
        },
    },
    "required": ["citedCases"],
}

tool_spec = {
    "toolSpec": {
        "name": "analyze_cited_case_treatment",
        "description": "Analyze the treatment Citing Case applied towards the Cited Case(s).",
        "inputSchema": {"json": schema},
    }
}

# ── Two-stage pipeline schemas ──

haiku_extraction_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Haiku Extraction Output Schema",
    "description": "Schema for citation extraction with section identification",
    "type": "object",
    "properties": {
        "citedCases": {
            "type": "array",
            "description": "A list of unique Cited Cases identified in the opinion.",
            "items": {
                "type": "object",
                "properties": {
                    "mainCitationString": {
                        "type": ["string", "null"],
                        "description": "Citation string in 'reporter-volume reporter reporter-page' format. Null if not cited by Full Citation.",
                        "maxLength": 20,
                    },
                    "caseName": {
                        "type": ["string", "null"],
                        "description": "Case name. Null if not mentioned by name.",
                        "maxLength": 100,
                    },
                    "section_ids": {
                        "type": "array",
                        "description": "List of section IDs where any reference to this Cited Case appears.",
                        "items": {"type": "string", "maxLength": 10},
                    },
                },
                "required": ["mainCitationString", "caseName", "section_ids"],
            },
        },
    },
    "required": ["citedCases"],
}

haiku_extraction_tool_spec = {
    "toolSpec": {
        "name": "extract_cited_cases",
        "description": "Extract all cited cases from the opinion and report which sections they appear in.",
        "inputSchema": {"json": haiku_extraction_schema},
    }
}

kimi_classification_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Kimi Classification Output Schema",
    "description": "Schema for treatment classification of cited cases",
    "type": "object",
    "properties": {
        "citedCases": {
            "type": "array",
            "description": "One classification per Cited Case, in the same order as provided.",
            "items": {
                "type": "object",
                "properties": {
                    "citedCaseId": {
                        "type": "integer",
                        "description": "The 1-based index of the Cited Case from the input.",
                    },
                    "actingCase": {
                        "type": "string",
                        "description": "The case that applies treatment. 'Citing Case' if the Citing Case itself, or the name/citation of the Acting Case, or 'Implicit'.",
                        "maxLength": 100,
                    },
                    "caseHistory": {
                        "type": "string",
                        "description": "The case history type.",
                        "enum": ["Direct History", "Citing Reference", "Related Reference"],
                    },
                    "treatment": {
                        "type": ["string", "null"],
                        "description": "The treatment applied to the Cited Case. Must be from the defined treatment lists only.",
                        "maxLength": 100,
                    },
                    "opinionType": {
                        "type": "string",
                        "description": "The opinion type in which the Cited Case is referenced.",
                        "maxLength": 50,
                    },
                    "quote": {
                        "type": ["string", "null"],
                        "description": "Verbatim passage from the opinion supporting the treatment.",
                        "maxLength": 500,
                    },
                    "rationale": {
                        "type": "string",
                        "description": "1-2 sentence explanation of the treatment.",
                        "maxLength": 300,
                    },
                },
                "required": [
                    "citedCaseId",
                    "actingCase",
                    "caseHistory",
                    "treatment",
                    "opinionType",
                    "quote",
                    "rationale",
                ],
            },
        },
    },
    "required": ["citedCases"],
}

kimi_classification_tool_spec = {
    "toolSpec": {
        "name": "classify_cited_case_treatment",
        "description": "Classify the treatment applied to each cited case based on the opinion excerpt.",
        "inputSchema": {"json": kimi_classification_schema},
    }
}


def _get_caps(model_id):
    return MODEL_CAPS.get(model_id, {
        "system_prompt": False,
        "tool_use": False,
    })


def _parse_json_response(text):
    """Extract and parse JSON from a text response."""
    # Try ```json ... ``` blocks first
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return repair_json(match.group(1), return_objects=True)
    # Try raw JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return repair_json(match.group(0), return_objects=True)
    return {}


def converse_completion(
    system_prompt,
    opinion_text,
    model_id=DEFAULT_MODEL_ID,
    temperature=TEMPERATURE,
    tool_spec_override=None,
):
    """Call Bedrock Converse API.

    Args:
        tool_spec_override: If provided, use this tool spec instead of the
            default citator tool. The response is returned in cited_cases
            as the raw tool input dict (not wrapped in a list).
    """
    try:
        caps = _get_caps(model_id)
        assert model_id in MODEL_CAPS, f"Unsupported model: {model_id}"

        bedrock_client = session.client(
            service_name="bedrock-runtime", region_name=AWS_REGION, config=config
        )

        # Build user content
        active_tool = tool_spec_override or tool_spec
        active_tool_name = active_tool["toolSpec"]["name"]

        if tool_spec_override:
            # For re-evaluation calls, opinion_text is the user prompt (no XML wrapping)
            user_text = opinion_text
        else:
            user_text = f"<opinion>\n{opinion_text}\n</opinion>"

        if not caps["tool_use"]:
            tool_schema = active_tool["toolSpec"]["inputSchema"]["json"]
            user_text += (
                "\n\nReturn your response as a JSON object conforming to this schema:\n"
                f"{json.dumps(tool_schema, indent=2)}"
            )
        user_content = [{"text": user_text}]

        # Build system content
        if caps["system_prompt"]:
            system_content = [{"text": system_prompt}]
        else:
            # Prepend system prompt to user content
            user_content = [{"text": system_prompt}] + user_content
            system_content = None

        # Build tool config
        if caps["tool_use"]:
            tool_config = {
                "tools": [active_tool],
                "toolChoice": {"tool": {"name": active_tool_name}},
            }
        else:
            tool_config = None

        # Assemble request kwargs
        kwargs = {
            "modelId": model_id,
            "messages": [{"role": "user", "content": user_content}],
            "inferenceConfig": {"temperature": temperature},
        }
        if system_content:
            kwargs["system"] = system_content
        if tool_config:
            kwargs["toolConfig"] = tool_config

        response = bedrock_client.converse(**kwargs)

        content = response["output"]["message"]["content"]
        stop_reason = response.get("stopReason", "unknown")
        usage = response.get("usage", {})

        # Extract result from tool use or text response
        result = {}
        if caps["tool_use"]:
            for block in content:
                if "toolUse" in block:
                    result = block["toolUse"].get("input", {})
                    break
        else:
            for block in content:
                if "text" in block:
                    result = _parse_json_response(block["text"])
                    break

        # For overridden tools, return the full result object;
        # for the default citator tool, extract citedCases for backwards compatibility
        if tool_spec_override:
            cited_cases = result
        else:
            cited_cases = result.get("citedCases", [])

        return {
            "model": model_id,
            "stop_reason": stop_reason,
            "latency_ms": response.get("metrics", {}).get("latencyMs", 0),
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cited_cases": cited_cases,
        }

    except AssertionError as e:
        logger.error(f"Assertion error: {e}")
        return {}

    except ClientError as e:
        logger.warning(f"Bedrock API error: {e}")
        return {}

    except Exception as e:
        logger.warning(f"Model completion failed: {e}")
        return {}
