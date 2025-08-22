import json
import logging

import boto3
session = boto3.Session(profile_name='dev-env')

from botocore.exceptions import ClientError
from botocore.config import Config
config = Config(read_timeout=1000)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

AWS_REGION = "us-west-2"
MAX_TOKENS = 8192
TEMPERATURE = 0.1

MODELS = [
    "openai.gpt-oss-120b-1:0"
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
]


# Not all models allow system prompt
ALLOW_SYS = [
    "openai.gpt-oss-120b-1:0"
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
]


# https://aws.amazon.com/blogs/machine-learning/structured-data-response-with-amazon-bedrock-prompt-engineering-and-tool-use/
schema = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Citator Output Schema",
  "description": "Schema for Citator output",
  "type": "object",
  "properties": {
    "extracted_items": {
      "type": "array",
      "description": "A list of extracted items.",
      "items": {
        "type": "object",
        "properties": {
          "uniqueID": {
            "type": "string",
            "description": "Unique identifier for the Target Case.",
            "maxLength": 10
          },
          "targetName": {
            "type": "string",
            "description": "Case name for the Target Case.",
            "maxLength": 100
          },
          "targetHistory": {
            "type": "string",
            "description": "The case history type for the Target Case.",
            "enum": [
                "Direct History",
                "Citing Reference",
                "Related Reference"
            ]
          },
          "targetTreatment": {
            "type": "string",
            "description": "The treatment classification of the Target Case.",
            "enum": [
                "Reversed by",
                "Reversed and remanded by",
                "Vacated by",
                "Vacated and remanded by",
                "Affirmed in part; Reversed in part by",
                "Remanded by",
                "Dismissed by",
                "Affirmed by",
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
                "Dismissed as recognized by",
                "Affirmed as recognized by",
                "Overruled as recognized by",
                "Abrogated as recognized by",
                "Questioned as recognized by",
                "Disapproved as recognized by",
                "Limited as recognized by",
                "Criticized as recognized by",
                "Distinguished as recognized by",
                "Declined to follow as recognized by",
                "Cited as recognized by",
            ]
          },
          "quote": {
            "type": "string",
            "description": "The verbatim quote from the opinion of the Citing Case that supports the treatment classification.",
            "maxLength": 200
          },
          "rationale": {
            "type": "string",
            "description": "A brief explanation justifying the treatment classification based on the quote.",
            "maxLength": 200
          }
        },
        "required": [
          "uniqueID",
          "targetName",
          "targetHistory",
          "targetTreatment",
          "quote",
          "rationale"
        ]
      }
    }
  },
  "required": ["extracted_items"]
}

tool_list = [
    {
        "toolSpec": {
            "name": "analyze_target_case_treatment",
            "description": "Analyze the treatment Citing Case applied towards the Target Case(s).",
            "inputSchema": {"json": schema}
        }
    }
]


def converse_completion(
    model_id,
    system_prompt,
    citing_opinion,
    target_opinions,
    temperature=TEMPERATURE,
    max_tokens=MAX_TOKENS,
):

    try:
        assert model_id in MODELS

        bedrock_client = session.client(
            service_name="bedrock-runtime", region_name=AWS_REGION, config=config
        )

        content=[{"text": f"{system_prompt}"},
                 {"text": f"<opinion> {citing_opinion} </opinion>"},
                 {"text": f"<targets> {json.dumps(target_opinions)} </targets>"},
                ]

        if model_id in ALLOW_SYS:
            response = bedrock_client.converse(
                modelId=model_id,
                system=[content[0]],
                messages=[
                    {
                        "role": "user",
                        "content": content[1:],
                    }
                ],
                
                inferenceConfig={"temperature": temperature, "maxTokens": max_tokens},
                toolConfig={"tools": tool_list, "toolChoice": {"tool": {"name": "analyze_target_case_treatment"}}}
            )
        else:
            response = bedrock_client.converse(
                modelId=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                inferenceConfig={"temperature": temperature, "maxTokens": max_tokens},
                toolConfig={"tools": tool_list, "toolChoice": {"tool": {"name": "analyze_target_case_treatment"}}}
            )

        raw_results = response["output"]["message"]["content"][0]["toolUse"]["input"]["extracted_items"]

        completion = {
            "model": model_id,
            "input_tokens": response["usage"]["inputTokens"],
            "output_tokens": response["usage"]["outputTokens"],
            "raw_results": raw_results,
        }

        return completion

    except AssertionError as err:
        logger.error(f"Assertion error: {err}")
        return dict()

    except (ClientError, Exception) as e:
        logging.warning(f"Model completion failed: {e}")
        return dict()
