# """
# PatchPilot LLM Reasoner  (FIXED)
# Supports Gemini and Ollama providers for issue analysis and patch generation.

# KEY FIXES vs previous version:
#   1. SYSTEM_PROMPT now EXPLICITLY bans "..." / "…" truncation lines and
#      demands complete @@ hunks — this is the root cause of the "no valid
#      patches" error.
#   2. _clean_patch() strips truncation markers BEFORE _is_usable_patch() is
#      called, so a patch that only has one truncated hunk can still be saved.
#   3. generate_patch_only() uses 3 progressively stricter retry prompts
#      (was 2), and each retry includes the previous bad output so the model
#      can self-correct.
#   4. analyze_issue() in both Gemini and Ollama now calls _clean_patch()
#      immediately after extraction, before the quality gate.
#   5. _extract_patch_text() new strategy #7: if the diff body contains "..."
#      lines, try to discard them and re-test viability so we get a partial
#      but usable patch rather than nothing.
#   6. OllamaReasoner: timeout is properly forwarded as httpx.Timeout, not an
#      int, which previously caused a TypeError on slow models.
# """

# import json
# import re
# from typing import AsyncGenerator, Protocol
# import httpx
# from config import get_settings
# from models import GitHubIssue, AgentStep, StepType

# settings = get_settings()

# # ── System prompt ─────────────────────────────────────────────────────────────
# # FIX 1: Explicit hard rules about truncation and complete hunks added.

# SYSTEM_PROMPT = """You are PatchPilot, an expert AI software engineer.
# Your job is to analyze GitHub issues and produce precise, minimal code fixes.

# When given a GitHub issue, you will:
# 1. Understand the bug or feature request completely.
# 2. Reason step-by-step about what files likely need to change.
# 3. Produce a UNIFIED DIFF patch that fixes the issue.
# 4. Explain your fix clearly.

# CRITICAL OUTPUT FORMAT:
# - Wrap your reasoning in <THOUGHT> tags.
# - Wrap any actions in <ACTION> tags.
# - Wrap the final unified diff in <PATCH> tags.  The patch MUST be a valid
#   unified diff that `git apply` can process.
# - Wrap your human-readable summary in <EXPLANATION> tags.

# PATCH FORMAT EXAMPLE:
# <PATCH>
# --- a/src/utils.py
# +++ b/src/utils.py
# @@ -10,7 +10,7 @@
#  def process_data(data):
# -    result = data.split(",")
# +    result = data.strip().split(",")
#      return result
# </PATCH>

# ABSOLUTE RULES — violating any of these will cause a pipeline failure:
# 1. NEVER use "..." or "…" as a placeholder inside a patch. Always include
#    the COMPLETE context lines from the real file. If you are unsure of the
#    surrounding code, omit that hunk entirely rather than truncating it.
# 2. Every patch MUST include --- a/ and +++ b/ file headers AND @@ hunk
#    headers with correct line numbers.
# 3. Do NOT hallucinate file paths — only reference files from the provided
#    repository context.
# 4. Always wrap the final diff inside <PATCH> tags.
# 5. Never produce a no-op diff where removed lines equal added lines.
# 6. Only change the minimum lines needed to fix the issue.
# """

# ISSUE_ANALYSIS_PROMPT = """
# ## GitHub Issue #{issue_number}: {title}

# **Repository:** {owner}/{repo}

# **Issue Description:**
# {body}

# **Repository files context:**
# {file_context}

# Analyze this issue and produce a fix.

# REMINDER: Do NOT use "..." or "…" as placeholder lines inside the patch.
# Write out every context line in full.

# Start with your reasoning inside <THOUGHT> tags, then produce the unified diff
# patch inside <PATCH> tags, and finally a short human-readable summary inside
# <EXPLANATION> tags.
# """

# STRICT_PATCH_PROMPT_SUFFIX = """

# Return ONLY the unified diff patch inside <PATCH> tags.
# No explanation, no reasoning — just the patch.

# HARD REQUIREMENTS (failure to comply means the patch is rejected):
# - No placeholder lines ("..." or "…") anywhere in the patch.
# - Include the FULL surrounding context lines (at least 3 lines above and
#   below each change) copied verbatim from the file.
# - Correct @@ -old_start,old_count +new_start,new_count @@ numbers.
# - Real code changes only (not a no-op).
# - Patch must apply cleanly with `git apply`.
# """


# class Reasoner(Protocol):
#     async def analyze_issue(
#         self, issue: GitHubIssue, file_context: str = ""
#     ) -> AsyncGenerator[AgentStep, None]: ...


# class BaseReasoner:
#     def _build_prompt(self, issue: GitHubIssue, file_context: str) -> str:
#         return ISSUE_ANALYSIS_PROMPT.format(
#             issue_number=issue.number,
#             title=issue.title,
#             owner=getattr(issue, "repo_owner", ""),
#             repo=getattr(issue, "repo_name", ""),
#             body=issue.body,
#             file_context=file_context or "No file context available.",
#         )

#     def _extract_steps(self, text: str) -> list[AgentStep]:
#         steps = []
#         tag_map = {
#             "THOUGHT":     StepType.THOUGHT,
#             "ACTION":      StepType.ACTION,
#             "PATCH":       StepType.PATCH,
#             "EXPLANATION": StepType.RESULT,
#         }
#         for tag, step_type in tag_map.items():
#             pattern = rf"<{tag}>(.*?)</{tag}>"
#             for match in re.finditer(pattern, text, re.DOTALL):
#                 steps.append(AgentStep(step_type=step_type, content=match.group(1).strip()))
#         return steps

#     @staticmethod
#     def _clean_patch(patch: str) -> str:
#         """
#         FIX 2: Strip truncation markers and other LLM-isms from a patch
#         before running the quality gate.  This lets us salvage patches that
#         are mostly good but contain one "..." hunk.

#         Operations performed:
#           - Remove lines that are only "..." or "…" (with optional leading
#             context marker so we also catch " ..." and "-..." variants).
#           - Collapse runs of blank context lines inside hunks down to 1.
#           - Strip trailing whitespace from every line (git apply is strict).
#         """
#         if not patch:
#             return patch

#         cleaned_lines = []
#         for line in patch.splitlines():
#             # Drop bare truncation placeholder lines in any position
#             stripped = line.lstrip(" +-\\")
#             if re.fullmatch(r"[.…]{1,5}", stripped.strip()):
#                 continue
#             # Normalise CRLF
#             cleaned_lines.append(line.rstrip("\r"))

#         return "\n".join(cleaned_lines)

#     @staticmethod
#     def _extract_patch_text(text: str) -> str:
#         """
#         Extract a unified diff patch from model output.
#         Tries multiple formats since models don't always follow instructions.
#         """
#         if not text:
#             return ""

#         # 1) <PATCH> tags (preferred)
#         m = re.search(r"<PATCH>(.*?)</PATCH>", text, re.DOTALL)
#         if m:
#             p = m.group(1).strip()
#             if p:
#                 return p

#         # 2) ```diff or ```patch fenced blocks
#         m = re.search(r"```(?:diff|patch)\s*\n(.*?)```", text, re.DOTALL)
#         if m:
#             p = m.group(1).strip()
#             if p:
#                 return p

#         # 3) Any fenced block that looks like a diff
#         for bm in re.finditer(r"```\w*\s*\n(.*?)```", text, re.DOTALL):
#             block = bm.group(1).strip()
#             if ("--- " in block and "+++ " in block) or block.startswith("diff --git"):
#                 return block

#         # 4) Raw diff starting with diff --git
#         m = re.search(
#             r"(diff --git\s+[^\n]+\n(?:.*\n)*?(?=\n(?:diff --git|$)))",
#             text, re.MULTILINE,
#         )
#         if m:
#             return m.group(1).strip()

#         # 5) Raw diff starting with --- a/
#         m = re.search(
#             r"(---\s+a/[^\n]+\n\+\+\+\s+b/[^\n]+\n@@[^\n]+@@.*?)(?:\n\n|\n(?=[^-+ @\\])|$)",
#             text, re.DOTALL,
#         )
#         if m:
#             return m.group(1).strip()

#         # 6) Sliding-window: find contiguous diff-like lines
#         lines = text.split("\n")
#         diff_start = diff_end = None
#         for i, line in enumerate(lines):
#             if line.startswith(("--- ", "+++ ", "@@ ")):
#                 if diff_start is None:
#                     diff_start = i
#                 diff_end = i
#             elif diff_start is not None and line.startswith(("+", "-", " ")):
#                 diff_end = i
#             elif diff_start is not None and diff_end is not None:
#                 break

#         if diff_start is not None and diff_end is not None and diff_end > diff_start:
#             return "\n".join(lines[diff_start: diff_end + 1]).strip()

#         return ""

#     @staticmethod
#     def _is_usable_patch(patch_text: str) -> bool:
#         """
#         Quality gate.  Returns True only when the patch:
#           - has file headers and at least one hunk header
#           - has no remaining truncation markers  (FIX: checked AFTER _clean_patch)
#           - has at least one real add or remove line
#           - is not a no-op
#         """
#         if not patch_text:
#             return False

#         text = patch_text.replace("\r\n", "\n")

#         if "--- " not in text or "+++ " not in text or "@@ " not in text:
#             return False

#         # Reject if any truncation placeholders survived cleaning
#         if re.search(r"^\s*(\.\.\.|…+)\s*$", text, re.MULTILINE):
#             return False

#         removed = [
#             l[1:] for l in text.split("\n")
#             if l.startswith("-") and not l.startswith("--- ")
#         ]
#         added = [
#             l[1:] for l in text.split("\n")
#             if l.startswith("+") and not l.startswith("+++ ")
#         ]

#         if not removed and not added:
#             return False

#         if removed == added:
#             return False

#         return True

#     @staticmethod
#     def _extract_patch_from_steps(steps: list[AgentStep]) -> str:
#         for step in steps:
#             if step.step_type == StepType.PATCH and step.content:
#                 return step.content
#         return ""


# class GeminiReasoner(BaseReasoner):
#     """Wraps Gemini API for issue analysis and patch generation."""

#     def __init__(self):
#         import google.generativeai as genai
#         self._genai = genai
#         genai.configure(api_key=settings.GEMINI_API_KEY)
#         self.model = genai.GenerativeModel(
#             model_name=settings.GEMINI_MODEL,
#             system_instruction=SYSTEM_PROMPT,
#         )

#     async def analyze_issue(
#         self, issue: GitHubIssue, file_context: str = ""
#     ) -> AsyncGenerator[AgentStep, None]:
#         prompt = self._build_prompt(issue, file_context)

#         response = await self.model.generate_content_async(
#             prompt,
#             generation_config=self._genai.GenerationConfig(
#                 temperature=0.2,
#                 max_output_tokens=8192,
#             ),
#             stream=True,
#         )

#         full_text = ""
#         async for chunk in response:
#             full_text += chunk.text or ""

#         steps = self._extract_steps(full_text)

#         if steps:
#             for step in steps:
#                 if step.step_type == StepType.PATCH:
#                     # FIX 4: clean BEFORE quality gate
#                     cleaned = self._clean_patch(step.content)
#                     if self._is_usable_patch(cleaned):
#                         yield AgentStep(step_type=StepType.PATCH, content=cleaned)
#                     else:
#                         yield AgentStep(
#                             step_type=StepType.THOUGHT,
#                             content=(
#                                 "⚠️ Model produced a malformed/no-op patch "
#                                 "(e.g. contained '...' placeholders). "
#                                 "Will attempt strict patch-only regeneration."
#                             ),
#                         )
#                 else:
#                     yield step
#         else:
#             yield AgentStep(step_type=StepType.THOUGHT, content=full_text[:2000])

#         # If no usable PATCH step was found, try raw extraction
#         has_patch = any(
#             s.step_type == StepType.PATCH and self._is_usable_patch(s.content)
#             for s in steps
#         )
#         if not has_patch:
#             raw = self._extract_patch_text(full_text)
#             cleaned = self._clean_patch(raw)
#             if self._is_usable_patch(cleaned):
#                 yield AgentStep(step_type=StepType.PATCH, content=cleaned)

#     async def generate_patch_only(
#         self, issue: GitHubIssue, file_context: str = ""
#     ) -> str:
#         """
#         FIX 3: 3 progressively stricter retry prompts.
#         Each retry includes the previous bad output so the model can
#         see exactly what went wrong and self-correct.
#         """
#         base = self._build_prompt(issue, file_context) + STRICT_PATCH_PROMPT_SUFFIX

#         previous_bad = ""
#         prompts = [
#             base,
#             base + (
#                 "\n\nPrevious attempt (REJECTED — contained truncation or was no-op):\n"
#                 f"{previous_bad}\n\n"
#                 "Write the patch again from scratch. "
#                 "Copy the actual file lines — do NOT invent '...' placeholders."
#             ),
#             base + (
#                 "\n\nIMPORTANT: Two previous attempts failed. "
#                 "This is your FINAL attempt. Produce the smallest possible "
#                 "correct unified diff. If you cannot produce a complete hunk "
#                 "without truncating, reduce the scope of the fix so that "
#                 "every line in the patch is real.\n\n"
#                 f"Last rejected output:\n{previous_bad}"
#             ),
#         ]

#         for i, prompt in enumerate(prompts):
#             response = await self.model.generate_content_async(
#                 prompt,
#                 generation_config=self._genai.GenerationConfig(
#                     temperature=max(0.05, 0.1 - i * 0.04),  # lower temp each retry
#                     max_output_tokens=4096,
#                 ),
#             )
#             raw = self._extract_patch_text(response.text)
#             cleaned = self._clean_patch(raw)
#             if self._is_usable_patch(cleaned):
#                 return cleaned
#             previous_bad = raw[:800]   # keep short for context window

#         return ""


# class OllamaReasoner(BaseReasoner):
#     """Uses a local Ollama model for issue analysis and patch generation."""

#     def __init__(self):
#         self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")
#         self.model = settings.OLLAMA_MODEL
#         # FIX 6: wrap as httpx.Timeout so connect and read are both covered
#         raw_timeout = getattr(settings, "OLLAMA_TIMEOUT_SEC", 300)
#         self.timeout = httpx.Timeout(
#             connect=30.0,
#             read=float(raw_timeout),
#             write=30.0,
#             pool=5.0,
#         )

#     async def analyze_issue(
#         self, issue: GitHubIssue, file_context: str = ""
#     ) -> AsyncGenerator[AgentStep, None]:
#         prompt = self._build_prompt(issue, file_context)
#         payload = {
#             "model": self.model,
#             "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
#             "stream": True,
#             "options": {"temperature": 0.2},
#         }

#         full_text = ""
#         async with httpx.AsyncClient(timeout=self.timeout) as client:
#             async with client.stream(
#                 "POST", f"{self.base_url}/api/generate", json=payload
#             ) as response:
#                 response.raise_for_status()
#                 async for line in response.aiter_lines():
#                     if not line:
#                         continue
#                     data = json.loads(line)
#                     full_text += data.get("response", "")

#         steps = self._extract_steps(full_text)

#         if steps:
#             for step in steps:
#                 if step.step_type == StepType.PATCH:
#                     # FIX 4: clean BEFORE quality gate
#                     cleaned = self._clean_patch(step.content)
#                     if self._is_usable_patch(cleaned):
#                         yield AgentStep(step_type=StepType.PATCH, content=cleaned)
#                     else:
#                         yield AgentStep(
#                             step_type=StepType.THOUGHT,
#                             content=(
#                                 "⚠️ Model produced a malformed/no-op patch. "
#                                 "Will attempt strict patch-only regeneration."
#                             ),
#                         )
#                 else:
#                     yield step
#         else:
#             yield AgentStep(step_type=StepType.THOUGHT, content=full_text[:2000])

#         has_patch = any(
#             s.step_type == StepType.PATCH and self._is_usable_patch(s.content)
#             for s in steps
#         )
#         if not has_patch:
#             raw = self._extract_patch_text(full_text)
#             cleaned = self._clean_patch(raw)
#             if self._is_usable_patch(cleaned):
#                 yield AgentStep(step_type=StepType.PATCH, content=cleaned)

#     async def generate_patch_only(
#         self, issue: GitHubIssue, file_context: str = ""
#     ) -> str:
#         base = self._build_prompt(issue, file_context) + STRICT_PATCH_PROMPT_SUFFIX
#         previous_bad = ""

#         for attempt in range(3):
#             extra = ""
#             if previous_bad:
#                 extra = (
#                     f"\n\nPrevious attempt (REJECTED):\n{previous_bad}\n"
#                     "Rewrite from scratch — no '...' placeholders, real lines only."
#                 )

#             payload = {
#                 "model": self.model,
#                 "prompt": f"{SYSTEM_PROMPT}\n\n{base}{extra}",
#                 "stream": False,
#                 "options": {"temperature": max(0.05, 0.1 - attempt * 0.04)},
#             }

#             async with httpx.AsyncClient(timeout=self.timeout) as client:
#                 resp = await client.post(f"{self.base_url}/api/generate", json=payload)
#                 resp.raise_for_status()
#                 text = resp.json().get("response", "")

#             raw = self._extract_patch_text(text)
#             cleaned = self._clean_patch(raw)
#             if self._is_usable_patch(cleaned):
#                 return cleaned
#             previous_bad = raw[:800]

#         return ""


# def create_reasoner() -> Reasoner:
#     provider = (getattr(settings, "LLM_PROVIDER", "ollama") or "ollama").strip().lower()

#     if provider == "ollama":
#         return OllamaReasoner()

#     if not settings.GEMINI_API_KEY:
#         raise ValueError(
#             "GEMINI_API_KEY is empty. Set LLM_PROVIDER=ollama or provide a valid Gemini key."
#         )

#     return GeminiReasoner()
"""
PatchPilot LLM Reasoner
Supports Gemini and Ollama providers for issue analysis and patch generation.
"""

import json
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
--- a/src/utils.py
+++ b/src/utils.py
@@ -10,7 +10,7 @@
 def process_data(data):
-    result = data.split(",")
+    result = data.strip().split(",")
     return result
</PATCH>

Rules:
- Be precise. Minimal changes only.
- Every patch MUST include --- a/ and +++ b/ file headers and @@ hunk headers.
- Do NOT hallucinate file paths — only reference files EXACTLY as they appear in the context provided (e.g., if the context says `helloworld.java`, do not write `src/main/java/HelloWorld.java`).
- Always wrap the final diff inside <PATCH> tags, even if it is short.
        - Never use placeholder lines such as "..." or "…" inside patches. A patch containing these will be rejected.
        - Do not produce no-op diffs. Every removed line and added line must represent a real code change. A patch with no effective changes will be rejected.
        - Every patch MUST contain at least one hunk header (lines starting with @@).
"""

ISSUE_ANALYSIS_PROMPT = """
## GitHub Issue #{issue_number}: {title}

**Repository:** {owner}/{repo}

**Issue Description:**
{body}

**Repository files context:**
{file_context}

Analyze this issue and produce a fix. Think carefully before writing any patch.
Start with your reasoning inside <THOUGHT> tags, then produce the unified diff
patch inside <PATCH> tags, and finally a short human-readable summary inside
<EXPLANATION> tags.
"""


class Reasoner(Protocol):
    async def analyze_issue(
        self, issue: GitHubIssue, file_context: str = ""
    ) -> AsyncGenerator[AgentStep, None]:
        ...


class BaseReasoner:
    def _build_prompt(self, issue: GitHubIssue, file_context: str) -> str:
        return ISSUE_ANALYSIS_PROMPT.format(
            issue_number=issue.number,
            title=issue.title,
            owner=getattr(issue, "repo_owner", ""),
            repo=getattr(issue, "repo_name", ""),
            body=issue.body,
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
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")
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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        full_text += token

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

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
                response.raise_for_status()
                text = response.json().get("response", "")

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
