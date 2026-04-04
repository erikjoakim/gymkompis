import json
from functools import lru_cache
from pathlib import Path


PROMPT_EXAMPLES_PATH = Path(__file__).resolve().parent / "example_prompts.json"


@lru_cache(maxsize=1)
def load_program_prompt_examples() -> list[dict]:
    with PROMPT_EXAMPLES_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        return []
    examples = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not title or not prompt:
            continue
        examples.append(
            {
                "title": title,
                "summary": str(item.get("summary", "")).strip(),
                "prompt": prompt,
            }
        )
    return examples
