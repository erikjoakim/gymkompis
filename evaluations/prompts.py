import json


def build_evaluation_instructions() -> str:
    return (
        "You are GymKompis, an assistant that evaluates gym training logs. "
        "Return strict JSON only with no markdown, no prose, and no code fences. "
        "Use the requested evaluation_type exactly. "
        "Consider both rep-based and time-based work, including warmups, mobility, cardio intervals, and static holds. "
        "When evaluating session data, compare planned versus actual execution and comment on adherence, effort, and progression. "
        "When evaluating a period, summarize consistency, progression, recovery risk, and exercise trends."
    )


def build_evaluation_input(payload: dict, evaluation_type: str, schema_hint: dict) -> str:
    return json.dumps(
        {
            "task": "Evaluate training data",
            "evaluation_type": evaluation_type,
            "payload": payload,
            "schema_requirements": schema_hint,
        },
        ensure_ascii=False,
    )
