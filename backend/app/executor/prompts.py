"""Default system prompts per step type, used when a step leaves its prompt empty."""

from app.schemas import StepType

BASE_RULES = """You are an autonomous software engineering agent working inside a Git repository
mounted at /repo. You can only touch files in that repository and can only run
whitelisted commands. Work carefully, verify your changes, and finish with a short
summary of what you did. When you are done, stop calling tools and reply with the
summary as plain text."""

DEFAULT_PROMPTS: dict[StepType, str] = {
    StepType.plan: (
        "Analyse the task and the repository. Do NOT modify files. Produce a concise, "
        "numbered implementation plan listing the files to change and the tests to add."
    ),
    StepType.code: (
        "Implement the task in the repository. Read the relevant files first, make the "
        "minimal correct change, and keep the code style consistent."
    ),
    StepType.test: (
        "Run the project's test suite (or the most relevant subset). If tests fail, fix the "
        "code or tests, then re-run. Finish by stating clearly whether all tests PASS or FAIL."
    ),
    StepType.review: (
        "Review the current diff for correctness, security and style. Do not change files "
        "unless you find a clear bug. Reply with a short review and a verdict: APPROVE or REQUEST_CHANGES."
    ),
    StepType.shell: "",
    StepType.condition: "",
}


def system_prompt_for(step_type: StepType, custom: str) -> str:
    """Compose the base rules with the step's own or default prompt."""
    body = custom.strip() or DEFAULT_PROMPTS.get(step_type, "")
    return f"{BASE_RULES}\n\n{body}".strip()
