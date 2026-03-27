"""
PatchPilot LLM Reasoner
Uses Google Gemini API to analyze GitHub issues and generate fix strategies.
"""

import google.generativeai as genai
from typing import AsyncGenerator
from config import get_settings
from models import GitHubIssue, AgentStep, StepType

settings = get_settings()

# ── System prompt for the coding agent ───────────────────────────────────────

SYSTEM_PROMPT = """You are PatchPilot, an expert AI software engineer.
Your job is to analyze GitHub issues and produce precise, minimal code fixes.

When given a GitHub issue, you will:
1. Understand the bug or feature request completely.
2. Reason step-by-step about what files likely need to change.
3. Produce a UNIFIED DIFF patch that fixes the issue.
4. Explain your fix clearly.

Output format:
- Use <THOUGHT> tags for your reasoning steps.
- Use <ACTION> tags for file operations (read_file, search_code, etc.).
- Use <PATCH> tags for the final unified diff.
- Use <EXPLANATION> tags for a human-readable summary.

Be precise. Minimal changes. No hallucinated file paths.
"""

ISSUE_ANALYSIS_PROMPT = """
## GitHub Issue #{issue_number}: {title}

**Repository:** {owner}/{repo}

**Issue Description:**
{body}

**Repository files context:**
{file_context}

Analyze this issue and produce a fix. Think carefully before writing any patch.
Start with your reasoning, then produce the unified diff patch.
"""


class GeminiReasoner:
    """Wraps Gemini API for issue analysis and patch generation."""

    def __init__(self):
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
        )

    def _build_prompt(self, issue: GitHubIssue, file_context: str) -> str:
        return ISSUE_ANALYSIS_PROMPT.format(
            issue_number=issue.number,
            title=issue.title,
            owner="", # Placeholder - will be set by orchestrator
            repo="",  # Placeholder - will be set by orchestrator
            body=issue.body,
            file_context=file_context or "No file context available.",
        )

    async def analyze_issue(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Stream reasoning steps and patch from Gemini.
        Yields AgentStep objects as the model generates output.
        """
        prompt = self._build_prompt(issue, file_context)

        # Stream response from Gemini
        response = await self.model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.2,       # Low temp for deterministic code fixes
                max_output_tokens=8192,
            ),
            stream=True,
        )

        buffer = ""
        current_tag = None

        async for chunk in response:
            text = chunk.text or ""
            buffer += text

            # Parse structured tags from Gemini output
            for step in self._extract_steps(buffer):
                buffer = ""   # Clear once we've parsed a complete block
                yield step

        # Flush anything remaining
        if buffer.strip():
            yield AgentStep(
                step_type=StepType.THOUGHT,
                content=buffer.strip(),
            )

    def _extract_steps(self, text: str) -> list[AgentStep]:
        """Parse <THOUGHT>, <ACTION>, <PATCH>, <EXPLANATION> blocks from text."""
        import re
        steps = []

        tag_map = {
            "THOUGHT":     StepType.THOUGHT,
            "ACTION":      StepType.ACTION,
            "PATCH":       StepType.PATCH,
            "EXPLANATION": StepType.RESULT,
        }

        for tag, step_type in tag_map.items():
            pattern = rf"<{tag}>(.*?)</{tag}>"
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                steps.append(AgentStep(step_type=step_type, content=match.strip()))

        return steps

    async def generate_patch_only(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> str:
        """
        Non-streaming call — returns just the unified diff patch string.
        Used when we only need the patch without streaming UI.
        """
        prompt = self._build_prompt(issue, file_context) + "\n\nReturn ONLY the unified diff patch inside <PATCH> tags."

        response = await self.model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.1, max_output_tokens=4096),
        )

        import re
        match = re.search(r"<PATCH>(.*?)</PATCH>", response.text, re.DOTALL)
        return match.group(1).strip() if match else ""
