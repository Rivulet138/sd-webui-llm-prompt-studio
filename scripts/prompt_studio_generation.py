"""Shared provider call/retry loop used by generation and transformations."""


def run_llm_generation(
    source, provider, endpoint, model, api_key, initial_system, build_retry_system,
    finalize_result, runtime_connection, request_temperature, timeout, max_tokens,
    send_temperature, cancel_event=None, preserve_inference_settings=False,
    gate_context=None, retry_limit=0, similarity_limit=0.82,
    retry_empty_result=False, credentials=None, call_llm=None,
    build_user_message=None, coerce_bool=None, fallback_request_kwargs=None,
    worst_content_similarity=None, max_response_retries=2,
):
    """Run one provider request with response retries and diversity gating.

    Application-specific policy is injected so this module remains independent
    from Gradio, database state, and the UI's prompt builders.
    """
    context = [str(item).strip() for item in (gate_context or []) if str(item).strip()]
    retry_limit = max(0, int(retry_limit or 0)) if context else 0
    similarity_limit = float(similarity_limit or 0.82)
    resolved_key = credentials.resolve(api_key, provider, endpoint)
    result = ""
    accepted = ""
    best_candidate = ""
    best_score = -1.0
    retry_reason = ""
    diversity_retries_used = 0
    attempt = 0
    system = initial_system
    while attempt < 1 + max(max_response_retries, retry_limit):
        attempt_system = system
        if retry_reason:
            attempt_system = build_retry_system(retry_reason, attempt)
        attempt_temperature = request_temperature
        if attempt and (retry_reason == "diversity" or not preserve_inference_settings):
            attempt_temperature = min(2.0, request_temperature + 0.15 * attempt)
        try:
            raw_result = call_llm(
                provider, endpoint, model, resolved_key, attempt_system,
                build_user_message(source), attempt_temperature,
                int(timeout or 90), int(max_tokens or 0), coerce_bool(send_temperature),
                max_retries=runtime_connection["retry_count"], cancel_event=cancel_event,
                thinking_enabled=runtime_connection.get("thinking_enabled"),
                thinking_budget=runtime_connection.get("thinking_budget", 0),
                reasoning_effort=runtime_connection.get("reasoning_effort", ""),
                top_p=runtime_connection.get("top_p"), top_k=runtime_connection.get("top_k"),
                **fallback_request_kwargs(runtime_connection),
            )
        except RuntimeError as error:
            if "response did not contain assistant text" not in str(error).lower() or attempt >= max_response_retries:
                raise
            retry_reason = "response"
            attempt += 1
            continue
        candidate = str(finalize_result(raw_result) or "").strip()
        system = attempt_system
        if not candidate and retry_empty_result:
            if attempt >= max_response_retries:
                result = candidate
                break
            retry_reason = "response"
            attempt += 1
            continue
        result = candidate
        if not context:
            break
        score = worst_content_similarity(candidate, context, source)
        if best_candidate == "" or score < best_score:
            best_candidate, best_score = candidate, score
        if score < similarity_limit:
            accepted = candidate
            break
        if attempt >= retry_limit:
            break
        retry_reason = "diversity"
        attempt += 1
        diversity_retries_used += 1
    if accepted:
        result = accepted
    elif best_candidate:
        result = best_candidate
    return result, system, diversity_retries_used
