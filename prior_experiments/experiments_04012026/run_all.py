import logging

from utils.bedrock_label_utils import run as bedrock_run
from utils.gemini_label_utils import run as gemini_run

logging.basicConfig(level=logging.INFO)

MODELS = {
    "sonnet-4-6": {
        "runner": bedrock_run,
        "model_id": "us.anthropic.claude-sonnet-4-6",
    },
    "haiku-4-5": {
        "runner": bedrock_run,
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    },
    "kimi-k2.5": {
        "runner": bedrock_run,
        "model_id": "moonshotai.kimi-k2.5",
    },
    "gemini-3-flash": {
        "runner": gemini_run,
        "model_id": "gemini-3-flash-preview",
    },
    "gemini-3.1-pro": {
        "runner": gemini_run,
        "model_id": "gemini-3.1-pro-preview",
    },
}


def run_all(txt_name="example.txt"):
    for name, config in MODELS.items():
        logging.info(f"\n{'='*60}")
        logging.info(f"Running model: {name} ({config['model_id']})")
        logging.info(f"{'='*60}")
        try:
            config["runner"](txt_name=txt_name, model_id=config["model_id"])
            logging.info(f"Completed: {name}")
        except Exception as e:
            logging.error(f"Failed: {name}: {e}")


if __name__ == "__main__":
    run_all()
