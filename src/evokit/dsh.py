"""DSH client limits are provider token limits, not serialized JSON sizes."""


def client_limits(*, context_tokens: int, output_tokens: int, reserve_tokens: int = 8192) -> dict:
    """Build a native DSH model's bounded context/output configuration.

    Args:
        context_tokens: Verified provider context window, including output.
        output_tokens: Explicit maximum completion allowance.
        reserve_tokens: Minimum input/tool headroom after the output allowance.

    Returns:
        dict: DSH contextWindow and maxTokens values. Host owners pin these
            alongside the relay model policy; bytes use a separate admission cap.

    Raises:
        ValueError: Limits leave insufficient context for an improver request.
    """
    if (
        any(type(v) is not int or v < 1 for v in (context_tokens, output_tokens, reserve_tokens))
        or output_tokens + reserve_tokens > context_tokens
    ):
        raise ValueError("Output allowance and input reserve exceed client context")
    return {"contextWindow": context_tokens, "maxTokens": output_tokens}
