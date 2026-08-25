DEFAULT_ENTRY = {"input_per_1m": 0.0, "output_per_1m": 0.0}


def estimate_cost(pricing: dict[str, dict], model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float:
    entry = pricing.get(model) or pricing.get("default") or DEFAULT_ENTRY
    input_per_1m = entry.get("input_per_1m", 0.0)
    output_per_1m = entry.get("output_per_1m", 0.0)
    prompt = prompt_tokens or 0
    completion = completion_tokens or 0
    return (prompt / 1_000_000 * input_per_1m) + (completion / 1_000_000 * output_per_1m)
