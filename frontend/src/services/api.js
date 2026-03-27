const API_BASE = import.meta.env.VITE_API_URL || "/api";

function tryParseGitHubUrl(value) {
  if (!value) return null;

  const raw = value.trim();
  const candidate = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;

  try {
    const url = new URL(candidate);
    if (!/github\.com$/i.test(url.hostname)) return null;

    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length < 2) return null;

    return {
      owner: parts[0],
      repo: parts[1].replace(/\.git$/i, ""),
    };
  } catch {
    return null;
  }
}

function normalizeOwnerRepo(owner, repo) {
  const cleanOwner = (owner || "").trim();
  const cleanRepo = (repo || "").trim();

  const fromRepoUrl = tryParseGitHubUrl(cleanRepo);
  if (fromRepoUrl) return fromRepoUrl;

  const fromOwnerUrl = tryParseGitHubUrl(cleanOwner);
  if (fromOwnerUrl) return fromOwnerUrl;

  // Allow entering "owner/repo" directly in the repo field.
  if (!cleanOwner && cleanRepo.includes("/")) {
    const parts = cleanRepo.split("/").filter(Boolean);
    if (parts.length >= 2) {
      return { owner: parts[0], repo: parts[1] };
    }
  }

  return { owner: cleanOwner, repo: cleanRepo };
}

function unwrapIssues(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.issues)) return payload.issues;
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload?.data?.issues)) return payload.data.issues;
  return [];
}

function unwrapIssue(payload) {
  return payload?.data?.data || payload?.data || payload;
}

export async function fetchIssues(owner, repo) {
  const normalized = normalizeOwnerRepo(owner, repo);
  const res = await fetch(`${API_BASE}/issues/${encodeURIComponent(normalized.owner)}/${encodeURIComponent(normalized.repo)}`);
  if (!res.ok) {
    const msg = res.status === 404 ? "Not Found. Use owner + repo name or a valid GitHub URL." : `HTTP ${res.status}`;
    throw new Error(`Failed to fetch issues: ${msg}`);
  }
  const json = await res.json();
  return unwrapIssues(json);
}

export async function fetchIssue(owner, repo, number) {
  const normalized = normalizeOwnerRepo(owner, repo);
  const res = await fetch(`${API_BASE}/issues/${encodeURIComponent(normalized.owner)}/${encodeURIComponent(normalized.repo)}/${number}`);
  if (!res.ok) throw new Error(`Failed to fetch issue: ${res.status}`);
  const json = await res.json();
  return unwrapIssue(json);
}

export function startAgentRun(payload, { onStep, onDone, onError }) {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/agent/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        throw new Error(`Agent run failed: ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const eventBlock of events) {
          const dataLine = eventBlock
            .split("\n")
            .find((line) => line.startsWith("data: "));
          if (!dataLine) continue;

          try {
            const parsed = JSON.parse(dataLine.slice(6));
            if (parsed?.event === "run_started") continue;
            onStep(parsed);
          } catch {
            // Ignore malformed SSE chunks
          }
        }
      }

      onDone();
    } catch (err) {
      if (err.name !== "AbortError") onError(err);
    }
  })();

  return controller;
}
