import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

AWS_REGION = "us-west-2"
MAX_TOKENS = 1024
TEMPERATURE = 0.1

MODELS = ["anthropic.claude-3-5-haiku-20241022-v1:0",
          "anthropic.claude-3-5-sonnet-20241022-v2:0",
          "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
          "cohere.command-light-text-v14",
          "cohere.command-text-v14",
          "cohere.command-r-v1:0",
          "cohere.command-r-plus-v1:0",
          "meta.llama3-1-405b-instruct-v1:0",
          "us.meta.llama3-2-1b-instruct-v1:0",
          "us.meta.llama3-2-3b-instruct-v1:0",
          "us.meta.llama3-3-70b-instruct-v1:0",
          "mistral.mistral-7b-instruct-v0:2",
          "mistral.mixtral-8x7b-instruct-v0:1",
          "mistral.mistral-large-2407-v1:0",
          "us.amazon.nova-micro-v1:0",
          "us.amazon.nova-lite-v1:0",
          "us.amazon.nova-pro-v1:0",
          ]


# Not all models allow system prompt
ALLOW_SYS = ["anthropic.claude-3-5-haiku-20241022-v1:0",
              "anthropic.claude-3-5-sonnet-20241022-v2:0",
              "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
              "cohere.command-r-v1:0",
              "cohere.command-r-plus-v1:0",
              "meta.llama3-1-405b-instruct-v1:0",
              "us.meta.llama3-2-1b-instruct-v1:0",
              "us.meta.llama3-2-3b-instruct-v1:0",
              "us.meta.llama3-3-70b-instruct-v1:0",
              "mistral.mistral-large-2407-v1:0",
              "us.amazon.nova-micro-v1:0",
              "us.amazon.nova-lite-v1:0",
              "us.amazon.nova-pro-v1:0",
              ]

def converse_completion(
    model_id,
    system_prompt,
    user_message,
    temperature=TEMPERATURE,
    max_tokens=MAX_TOKENS,
):

    try:
        assert model_id in MODELS

        bedrock_client = boto3.client(
            service_name="bedrock-runtime", region_name=AWS_REGION
        )

        if model_id in ALLOW_SYS:
            response = bedrock_client.converse(
                modelId=model_id,
                system=[{"text": f"{system_prompt}"}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_message}],
                    }
                ],
                inferenceConfig={"temperature": temperature, "maxTokens": max_tokens},
            )
        else:
            response = bedrock_client.converse(
                modelId=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": f"{system_prompt}. Passages: {user_message}"}],
                    }
                ],
                inferenceConfig={"temperature": temperature, "maxTokens": max_tokens},
            )

        raw_results = response["output"]["message"]["content"][0]["text"]

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
    
