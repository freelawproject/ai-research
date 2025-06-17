import logging
from textwrap import dedent

from anthropic import AnthropicBedrock

from instructions import baseline_instructions, claude_instructions_v213

AWS_REGION = "us-west-2"
MAX_TOKENS = 512
TEMPERATURE = 0.1

#CLAUDE_MODEL = "anthropic.claude-3-5-haiku-20241022-v1:0"
CLAUDE_MODEL = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
CLAUDE_INSTRUCT = claude_instructions_v213


def format_prompt(prompt):
    return dedent(prompt).replace(" \n", "\n").strip()


def claude_completion(
    user_message,
    system_prompt=CLAUDE_INSTRUCT,
    aws_region=AWS_REGION,
    model=CLAUDE_MODEL,
    max_tokens=MAX_TOKENS,
    temperature=TEMPERATURE,
):

    client = AnthropicBedrock(aws_region=aws_region)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=f"{format_prompt(system_prompt)}",
            messages=[{"role": "user", "content": f"{user_message}"}],
        )
        raw_results = response.content[0].text

        completion = {
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "raw_results": raw_results,
        }

        return completion
    except Exception as e:
        logging.warning(f"claude_completion failed: {e}")
        return dict()


