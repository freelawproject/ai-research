import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

session = boto3.Session(profile_name="dev-env")
config = Config(read_timeout=1000)

AWS_REGION = "us-west-2"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
TEMPERATURE = 0.1

MODELS = [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
]

# Not all models support a separate system prompt
ALLOW_SYS = [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
]

# https://aws.amazon.com/blogs/machine-learning/structured-data-response-with-amazon-bedrock-prompt-engineering-and-tool-use/
schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Citator Output Schema",
    "description": "Schema for Citator output",
    "type": "object",
    "properties": {
        "numCitedCases": {
            "type": "integer",
            "description": "The total number of Cited Cases identified in the opinion.",
        },
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
                    "citedName": {
                        "type": ["string", "null"],
                        "description": "Case name for the Cited Case. Set to null if the Cited Case is not mentioned by name.",
                        "maxLength": 100,
                    },
                    "actingCase": {
                        "type": "string",
                        "description": "The case that applies the treatment to the Cited Case. Set to 'Citing Case' if the Acting Case is the Citing Case itself. Set to the name or citation of the Acting Case if it is a different case. Set to 'Implicit' if the Acting Case is not explicitly named.",
                        "maxLength": 100,
                    },
                    "caseHistory": {
                        "type": "string",
                        "description": "The Case History type of the Cited Case in relation to the Citing Case.",
                        "enum": ["Direct History", "Citing Reference", "Related Reference"],
                    },
                    "treatment": {
                        "type": ["string", "null"],
                        "description": "The treatment classification applied to the Cited Case. Set to null if no treatment can be determined at all.",
                        "enum": [
                            "Reversed by",
                            "Reversed and remanded by",
                            "Vacated by",
                            "Vacated and remanded by",
                            "Affirmed in part; Reversed in part by",
                            "Remanded by",
                            "Cert. granted by",
                            "Dismissed by",
                            "Affirmed by",
                            "Cert. denied by",
                            "Overruled by",
                            "Abrogated by",
                            "Questioned by",
                            "Disapproved by",
                            "Limited by",
                            "Criticized by",
                            "Distinguished by",
                            "Declined to follow by",
                            "Cited by",
                            "Reversed as recognized by",
                            "Reversed and remanded as recognized by",
                            "Vacated as recognized by",
                            "Vacated and remanded as recognized by",
                            "Affirmed in part; Reversed in part as recognized by",
                            "Remanded as recognized by",
                            "Cert. granted as recognized by",
                            "Dismissed as recognized by",
                            "Affirmed as recognized by",
                            "Cert. denied as recognized by",
                            "Overruled as recognized by",
                            "Abrogated as recognized by",
                            "Questioned as recognized by",
                            "Disapproved as recognized by",
                            "Limited as recognized by",
                            "Criticized as recognized by",
                            "Distinguished as recognized by",
                            "Declined to follow as recognized by",
                            "Cited as recognized by",
                            "Ambiguous: Stop",
                            "Ambiguous: Warning",
                            "Ambiguous: Caution",
                            "Ambiguous: Neutral",
                            None,
                        ],
                    },
                    "opinionType": {
                        "type": "string",
                        "description": "The opinion section from which the treatment is derived.",
                        "enum": [
                            "Lead",
                            "Plurality",
                            "Concurrence",
                            "Dissent",
                            "Lead (Footnote)",
                            "Plurality (Footnote)",
                            "Concurrence (Footnote)",
                            "Dissent (Footnote)",
                        ],
                    },
                    "quote": {
                        "type": ["string", "null"],
                        "description": "The verbatim passage from the opinion that most directly supports the treatment classification. Set to null if no suitable passage exists.",
                        "maxLength": 500,
                    },
                    "rationale": {
                        "type": "string",
                        "description": "A 1-2 sentence explanation connecting the quote to the specific treatment label. If treatment is null, explains why no treatment could be determined.",
                        "maxLength": 300,
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence in the treatment classification, between 0.0 and 1.0. Must be 0.0 if treatment is null.",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": [
                    "mainCitationString",
                    "citedName",
                    "actingCase",
                    "caseHistory",
                    "treatment",
                    "opinionType",
                    "quote",
                    "rationale",
                    "confidence",
                ],
            },
        },
    },
    "required": ["numCitedCases", "citedCases"],
}

tool_list = [
    {
        "toolSpec": {
            "name": "analyze_cited_case_treatment",
            "description": "Analyze the treatment Citing Case applied towards the Cited Case(s).",
            "inputSchema": {"json": schema},
        }
    },
    {"cachePoint": {"type": "default"}},
]


def converse_completion(
    system_prompt,
    opinion_text,
    model_id=DEFAULT_MODEL_ID,
    temperature=TEMPERATURE,
):
    try:
        assert model_id in MODELS, f"Unsupported model: {model_id}"

        bedrock_client = session.client(
            service_name="bedrock-runtime", region_name=AWS_REGION, config=config
        )

        user_content = [{"text": f"<opinion>\n{opinion_text}\n</opinion>"},
                        {"cachePoint": {"type": "default"}}]

        if model_id in ALLOW_SYS:
            response = bedrock_client.converse(
                modelId=model_id,
                system=[{"text": system_prompt},{"cachePoint": {"type": "default"}}],
                messages=[{"role": "user", "content": user_content}],
                inferenceConfig={"temperature": temperature},
                toolConfig={
                    "tools": tool_list,
                    "toolChoice": {"tool": {"name": "analyze_cited_case_treatment"}},
                },
            )
        else:
            response = bedrock_client.converse(
                modelId=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": system_prompt}] + user_content,
                    }
                ],
                inferenceConfig={"temperature": temperature},
                toolConfig={
                    "tools": tool_list,
                    "toolChoice": {"tool": {"name": "analyze_cited_case_treatment"}},
                },
            )

        content = response["output"]["message"]["content"]
        stop_reason = response.get("stopReason", "unknown")
        usage = response.get("usage", {})

        tool_input = {}
        for block in content:
            if "toolUse" in block:
                tool_input = block["toolUse"].get("input", {})
                break

        return {
            "model": model_id,
            "stop_reason": stop_reason,
            "latency_ms": response.get("metrics", {}).get("latencyMs", 0),
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cache_read_input_tokens": usage.get("cacheReadInputTokens", 0),
            "cache_write_input_tokens": usage.get("cacheWriteInputTokens", 0),
            "num_cited_cases": tool_input.get("numCitedCases", 0),
            "cited_cases": tool_input.get("citedCases", []),
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
