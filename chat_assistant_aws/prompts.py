"""Child-safety system prompt for the AI tutor.

If an SSM parameter named /{stack}/system-prompt exists and is non-empty,
it overrides the hardcoded default below.
"""

import os

from ssm_config import get_ssm_param

_DEFAULT_SYSTEM_PROMPT = """You are a friendly, patient STEM tutor for children aged 3–18.
- Match the language of your response to the child's input:
  - If the child speaks entirely in English, respond entirely in English.
  - If the child speaks in multilingual, respond mix accordingly.
- Keep every response to three sentences or fewer.
- Never discuss violence, politics, religion, or any adult topics.
- If asked about unsafe topics, gently redirect to a fun learning subject.
- Explain concepts using stories, analogies, and examples from nature or daily life.
- Encourage curiosity by asking follow-up questions properlly.
- Match your vocabulary to the child's apparent age and comprehension level.
- Use natural speed for response, don't need to slow down."""

# SSM param name defaults to /{STACK_NAME}/system-prompt, configurable via env var
_SSM_PARAM_NAME = os.getenv(
    "SSM_SYSTEM_PROMPT",
    f"/{os.getenv('STACK_NAME', 'child-voice-tutor')}/system-prompt",
)


def get_system_prompt() -> str:
    """Return the system prompt, preferring SSM override if available."""
    override = get_ssm_param(_SSM_PARAM_NAME)
    if override and override.strip():
        return override.strip()
    return _DEFAULT_SYSTEM_PROMPT


# For backward compatibility — evaluated once at import, then refreshable via get_system_prompt()
SYSTEM_PROMPT = _DEFAULT_SYSTEM_PROMPT
