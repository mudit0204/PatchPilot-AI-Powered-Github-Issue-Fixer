"""
PatchPilot LLM Reasoner
Supports Gemini and Ollama providers for issue analysis and patch generation.
"""

import json
import os
import re
from typing import AsyncGenerator, Protocol
import httpx
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

CRITICAL OUTPUT FORMAT:
- Wrap your reasoning in <THOUGHT> tags.
- Wrap any actions in <ACTION> tags.
- Wrap the final unified diff in <PATCH> tags. The patch MUST be a valid
  unified diff that `git apply` can process.
- Wrap your human-readable summary in <EXPLANATION> tags.

PATCH FORMAT EXAMPLE:
<PATCH>
--- a/helloworld.java
+++ b/helloworld.java
@@ -1,5 +1,6 @@
 public class HelloWorld {
     public static void main(String[] args) {
-        System.out.print("Hello");
+        System.out.println("Hello World");
     }
 }
</PATCH>

ABSOLUTE RULES (violate these and the patch will fail):
1. ONLY modify files that appear in the repository file list provided below.
2. Use the EXACT filenames as they appear in the file list — do NOT add path prefixes like "src/", "src/main/java/", etc.
3. Do NOT hallucinate file paths or nested directories — stick ONLY to files in the provided list.
4. Every patch MUST include --- a/ and +++ b/ file headers and @@ hunk headers.
5. Never use placeholder lines such as "..." or "…" inside patches.
6. Do not produce no-op diffs. Every removed and added line must be real code changes.
7. Every patch MUST contain at least one hunk header (lines starting with @@).
8. Be precise and minimal — only change what's necessary to fix the issue.
"""

ISSUE_ANALYSIS_PROMPT = """
## GitHub Issue #{issue_number}: {title}

**Repository:** {owner}/{repo}

**Issue Description:**
{body}

**Available files to modify (use these EXACT names, no path prefixes):**
{file_list}

**File Context:**
{file_context}

Analyze this issue and produce a fix. Remember:
- ONLY use files from the "Available files" list above
- Use the EXACT filename as shown (no path prefixes)
- Do not make up or hallucinate file paths

Start with your reasoning inside <THOUGHT> tags, then produce the unified diff
patch inside <PATCH> tags using the exact filenames from the list above, and 
finally a short human-readable summary inside <EXPLANATION> tags.
"""


class Reasoner(Protocol):
    async def analyze_issue(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> AsyncGenerator[AgentStep, None]:
        ...


class BaseReasoner:
    def _build_prompt(self, issue: GitHubIssue, file_context: str, file_list: str = "") -> str:
        """Build the analysis prompt with file list and context"""
        return ISSUE_ANALYSIS_PROMPT.format(
            issue_number=issue.number,
            title=issue.title,
            owner=getattr(issue, "repo_owner", ""),
            repo=getattr(issue, "repo_name", ""),
            body=issue.body,
            file_list=file_list or "No specific files detected",
            file_context=file_context or "No file context available.",
        )

    def _extract_steps(self, text: str) -> list[AgentStep]:
        """Parse <THOUGHT>, <ACTION>, <PATCH>, <EXPLANATION> blocks from text."""
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

    @staticmethod
    def _extract_patch_text(text: str) -> str:
        """
        Extract a unified diff patch from model output.
        Tries multiple formats since models don't always follow instructions.
        """
        if not text:
            return ""

        # 1) Try <PATCH> tags first (preferred)
        match = re.search(r"<PATCH>(.*?)</PATCH>", text, re.DOTALL)
        if match:
            patch = match.group(1).strip()
            if patch:
                return patch

        # 2) Try ```diff or ```patch fenced code blocks
        match = re.search(r"```(?:diff|patch)\s*\n(.*?)```", text, re.DOTALL)
        if match:
            patch = match.group(1).strip()
            if patch:
                return patch

        # 3) Try any fenced code block that looks like a diff
        for block_match in re.finditer(r"```\w*\s*\n(.*?)```", text, re.DOTALL):
            block = block_match.group(1).strip()
            if ("--- " in block and "+++ " in block) or block.startswith("diff --git"):
                return block

        # 4) Try to find raw unified diff starting with diff --git
        match = re.search(
            r"(diff --git\s+[^\n]+\n(?:.+?\n)*?(?=(?:diff --git|\Z)))",
            text,
            re.MULTILINE,
        )
        if match:
            return match.group(1).strip()

        # 5) Try to find raw unified diff starting with --- a/
        match = re.search(
            r"(---\s+a/[^\n]+\n\+\+\+\s+b/[^\n]+\n@@[^\n]+@@(?:.+?\n)*?(?=(?:--- a/|\Z)))",
            text,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()

        # 6) Last resort: find any lines with +/- that look like a diff body
        lines = text.split("\n")
        diff_start = None
        diff_end = None
        for i, line in enumerate(lines):
            if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("@@ "):
                if diff_start is None:
                    diff_start = i
                diff_end = i
            elif diff_start is not None and (
                line.startswith("+") or line.startswith("-") or line.startswith(" ")
            ):
                diff_end = i
            elif diff_start is not None and diff_end is not None:
                # End of diff block
                break

        if diff_start is not None and diff_end is not None and diff_end > diff_start:
            patch = "\n".join(lines[diff_start : diff_end + 1]).strip()
            return re.sub(r"^\s*(\.\.\.|…+)\s*$", "", patch, flags=re.MULTILINE).strip()

        return ""

    @staticmethod
    def _extract_patch_from_steps(steps: list[AgentStep]) -> str:
        """Look through accumulated steps for a patch."""
        for step in steps:
            if step.step_type == StepType.PATCH and step.content:
                return step.content
        return ""

    @staticmethod
    def _is_usable_patch(patch_text: str) -> bool:
        """Basic quality gate for patch-only outputs."""
        if not patch_text:
            return False

        text = patch_text.replace("\r\n", "\n")
        if "--- " not in text or "+++ " not in text or "@@ " not in text or not re.search(r"^@@.*?@@", text, re.MULTILINE):
            return False

        if re.search(r"^\s*(\.\.\.|…+)\s*$", text, re.MULTILINE):
            return False

        removed = [line[1:] for line in text.split("\n") if line.startswith("-") and not line.startswith("--- ")]
        added = [line[1:] for line in text.split("\n") if line.startswith("+") and not line.startswith("+++ ")]

        if not removed and not added:
            return False

        if removed == added:
            return False

        return True


class GeminiReasoner(BaseReasoner):
    """Wraps Gemini API for issue analysis and patch generation."""

    def __init__(self):
        import google.generativeai as genai

        self._genai = genai
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
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
            generation_config=self._genai.GenerationConfig(
                temperature=0.2,       # Low temp for deterministic code fixes
                max_output_tokens=8192,
            ),
            stream=True,
        )

        full_text = ""
        async for chunk in response:
            text = chunk.text or ""
            full_text += text

        # Parse all structured tags from the complete response
        steps = self._extract_steps(full_text)

        if steps:
            for step in steps:
                if step.step_type == StepType.PATCH:
                    if self._is_usable_patch(step.content):
                        yield step
                    else:
                        yield AgentStep(
                            step_type=StepType.THOUGHT,
                            content="Discarded malformed/no-op patch from model output. Requesting stricter patch extraction.",
                        )
                else:
                    yield step
        else:
            # Model didn't use tags — try to extract a patch from raw text
            yield AgentStep(step_type=StepType.THOUGHT, content=full_text[:2000])

        # If no PATCH step was found among the parsed steps, try extraction
        has_patch = any(
            s.step_type == StepType.PATCH and self._is_usable_patch(s.content)
            for s in steps
        )
        if not has_patch:
            patch_text = self._extract_patch_text(full_text)
            if self._is_usable_patch(patch_text):
                yield AgentStep(step_type=StepType.PATCH, content=patch_text)

    async def generate_patch_only(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> str:
        """
        Non-streaming call — returns just the unified diff patch string.
        Used when we only need the patch without streaming UI.
        """
        base_prompt = (
            self._build_prompt(issue, file_context)
            + "\n\nReturn ONLY the unified diff patch inside <PATCH> tags. "
            "No explanation, no reasoning — just the patch."
            "\nHard requirements: no placeholder lines (\'...\' or \'…\'), include full hunks, and produce real code changes (not no-op)."
        )

        prompt = base_prompt
        for attempt in range(2):
            response = await self.model.generate_content_async(
                prompt,
                generation_config=self._genai.GenerationConfig(temperature=0.1, max_output_tokens=4096),
            )
            patch = self._extract_patch_text(response.text)
            if self._is_usable_patch(patch):
                return patch

            prompt = (
                base_prompt
                + "\nThe previous output was invalid or no-op. Regenerate a complete, valid unified diff with actual changed lines."
            )

        return ""


class OllamaReasoner(BaseReasoner):
    """Uses local Ollama model for issue analysis and patch generation."""

    def __init__(self):
        base_url = settings.OLLAMA_BASE_URL
        if os.path.exists("/.dockerenv") and settings.OLLAMA_BASE_URL_DOCKER:
            base_url = settings.OLLAMA_BASE_URL_DOCKER

        self.base_url = base_url.rstrip("/")
        self.model = settings.OLLAMA_MODEL
        self.timeout = settings.OLLAMA_TIMEOUT_SEC

    async def analyze_issue(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> AsyncGenerator[AgentStep, None]:
        prompt = self._build_prompt(issue, file_context)

        payload = {
            "model": self.model,
            "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
            "stream": True,
            "options": {"temperature": 0.2},
        }

        full_text = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        token = data.get("response", "")
                        if token:
                            full_text += token
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Ensure Ollama is running and the backend is using the correct host URL."
            ) from exc

        # Parse complete response for structured tags
        steps = self._extract_steps(full_text)

        if steps:
            for step in steps:
                if step.step_type == StepType.PATCH:
                    if self._is_usable_patch(step.content):
                        yield step
                    else:
                        yield AgentStep(
                            step_type=StepType.THOUGHT,
                            content="Discarded malformed/no-op patch from model output. Requesting stricter patch extraction.",
                        )
                else:
                    yield step
        else:
            yield AgentStep(step_type=StepType.THOUGHT, content=full_text[:2000])

        # Check for patch in parsed steps; if missing, try extraction
        has_patch = any(
            s.step_type == StepType.PATCH and self._is_usable_patch(s.content)
            for s in steps
        )
        if not has_patch:
            patch_text = self._extract_patch_text(full_text)
            if self._is_usable_patch(patch_text):
                yield AgentStep(step_type=StepType.PATCH, content=patch_text)

    async def generate_patch_only(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> str:
        base_prompt = (
            self._build_prompt(issue, file_context)
            + "\n\nReturn ONLY the unified diff patch inside <PATCH> tags. "
            "No explanation, no reasoning — just the patch."
            "\nHard requirements: no placeholder lines (‘...’ or ‘…’), include full hunks, and produce real code changes (not no-op)."
        )

        prompt = base_prompt
        for _ in range(2):
            payload = {
                "model": self.model,
                "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
                "stream": False,
                "options": {"temperature": 0.1},
            }

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                    response = await client.post(f"{self.base_url}/api/generate", json=payload)
                    response.raise_for_status()
                    text = response.json().get("response", "")
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                raise RuntimeError(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Ensure Ollama is running and the backend is using the correct host URL."
                ) from exc

            patch = self._extract_patch_text(text)
            if self._is_usable_patch(patch):
                return patch

            prompt = (
                base_prompt
                + "\nThe previous output was invalid or no-op. Regenerate a complete, valid unified diff with actual changed lines."
            )

        return ""


def create_reasoner() -> Reasoner:
    provider = (settings.LLM_PROVIDER or "ollama").strip().lower()

    if provider == "ollama":
        return OllamaReasoner()

    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is empty. Set LLM_PROVIDER=ollama or provide a valid Gemini key.")

    return GeminiReasoner()
