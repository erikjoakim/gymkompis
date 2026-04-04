import json


def extract_json_object(text: str):
    candidate = (text or "").strip()
    if not candidate:
        raise json.JSONDecodeError("Empty response", candidate, 0)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    if "```" in candidate:
        parts = candidate.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if not cleaned:
                continue
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                continue

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(candidate[start : end + 1])

    raise json.JSONDecodeError("Could not extract JSON object", candidate, 0)


def extract_response_text(response) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)

    return "\n".join(chunks).strip()
