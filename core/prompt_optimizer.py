"""
prompt_optimizer.py — AI-Powered System Prompt Improvement
============================================================
Uses Claude to analyze human feedback patterns and suggest specific
improvements to the QA test case generation system prompt.

The suggestions are returned as text for manual review — the QA analyst
decides which suggestions to apply to config/system_prompt.md.
This prevents automatic changes to the system prompt without human oversight.
"""

import logging
import os

import anthropic
from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Model for prompt optimization (can use a faster/cheaper model here) ────────
OPTIMIZER_MODEL = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")
OPTIMIZER_MAX_TOKENS = 1000


def optimize_prompt(feedback_summary: str) -> str:
    """
    Call Claude with a feedback summary and ask for specific improvements
    to the QA test case generation system prompt.

    The function returns Claude's suggestions as plain text. The QA analyst
    should review these suggestions and manually apply relevant ones to
    config/system_prompt.md.

    Args:
        feedback_summary (str): Text summary from FeedbackStore.get_patterns_summary().
                                 Should contain acceptance rates, module breakdowns,
                                 and patterns of rejections/modifications.

    Returns:
        str: Claude's suggestions for improving the system prompt.
             Returns an error message string if the API call fails.
    """
    load_dotenv()

    from core.ai_provider import resolved_key
    api_key = resolved_key() or None
    if not api_key:
        return "Error: no AI provider configured (ANTHROPIC_API_KEY, or QAX_AI_PROVIDER=openai). Cannot call optimization API."

    # Check if the feedback summary indicates insufficient data
    if "Not enough data yet" in feedback_summary:
        return (
            "Cannot optimize yet — " + feedback_summary + "\n\n"
            "Record at least 20 accept/reject decisions first using:\n"
            "  python main.py feedback --accept TC-TRK-0001\n"
            "  python main.py feedback --reject TC-TRK-0001"
        )

    optimization_prompt = f"""You are a prompt engineer specializing in AI-powered QA test generation systems.

Below is a feedback analysis from a QA team reviewing AI-generated test cases for the target project:

---
{feedback_summary}
---

Based on this feedback data, suggest exactly 3 specific improvements to add to the AI system prompt
that generates these test cases.

Requirements for your suggestions:
1. Each suggestion must be a concrete, actionable rule or instruction
2. Each suggestion must be directly tied to a pattern visible in the feedback data
3. Format each suggestion as a numbered instruction ready to paste into a system prompt
4. Be specific — "generate more preconditions" is bad;
   "Always include at least 3 preconditions covering: user role, data state, and browser/session state" is good
5. Focus on the modules with the lowest acceptance rates

Return ONLY the 3 numbered suggestions. No introduction, no conclusion.
"""

    try:
        from core.ai_provider import make_ai_client
        client = make_ai_client(api_key=api_key)

        logger.info("Calling Claude for prompt optimization suggestions...")
        response = client.messages.create(
            model=OPTIMIZER_MODEL,
            max_tokens=OPTIMIZER_MAX_TOKENS,
            messages=[
                {"role": "user", "content": optimization_prompt}
            ]
        )

        suggestions = response.content[0].text.strip()
        logger.info("Optimization suggestions received (%d chars)", len(suggestions))
        return suggestions

    except anthropic.APIError as e:
        error_msg = f"Claude API error during optimization: {str(e)}"
        logger.error(error_msg)
        return error_msg

    except Exception as e:
        error_msg = f"Unexpected error during optimization: {str(e)}"
        logger.error(error_msg)
        return error_msg
