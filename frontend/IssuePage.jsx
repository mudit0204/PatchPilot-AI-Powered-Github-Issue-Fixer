import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Play, Eye, GitCommit, AlertTriangle, Loader2, CheckCircle, XCircle } from "lucide-react";
import { fetchIssue, startAgentRun } from "../services/api";
import AgentStream from "../components/agent/AgentStream";
import PatchViewer from "../components/agent/PatchViewer";
import styles from "./IssuePage.module.css";

export default function IssuePage() {
  const { owner, repo, number } = useParams();
  const navigate = useNavigate();

  const [issue, setIssue]       = useState(null);
  const [issueLoading, setIssueLoading] = useState(true);
  const [issueError, setIssueError]     = useState(null);

  // Agent run state
  const [runState, setRunState] = useState("idle"); // idle | running | done | failed
  const [steps, setSteps]       = useState([]);
  const [patch, setPatch]       = useState("");
  const [prUrl, setPrUrl]       = useState("");
  const [commitSha, setCommitSha] = useState("");
  const [dryRun, setDryRun]     = useState(false);
  const abortRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await fetchIssue(owner, repo, number);
        setIssue(data);
      } catch (e) {
        setIssueError(e.message);
      } finally {
        setIssueLoading(false);
      }
    })();
    return () => abortRef.current?.abort();
  }, [owner, repo, number]);

  const triggerRun = (dry = false) => {
    setDryRun(dry);
    setSteps([]);
    setPatch("");
    setPrUrl("");
    setCommitSha("");
    setRunState("running");

    abortRef.current = startAgentRun(
      { repo_owner: owner, repo_name: repo, issue_number: parseInt(number), dry_run: dry },
      {
        onStep: (step) => {
          setSteps(prev => [...prev, step]);
          if (step.step_type === "patch" && step.content) setPatch(step.content);
          if (step.metadata?.pr_url)     setPrUrl(step.metadata.pr_url);
          if (step.metadata?.commit_sha) setCommitSha(step.metadata.commit_sha);
          if (step.step_type === "error") setRunState("failed");
        },
        onDone:  () => setRunState(prev => prev !== "failed" ? "done" : "failed"),
        onError: () => setRunState("failed"),
      }
    );
  };

  const stopRun = () => {
    abortRef.current?.abort();
    setRunState("idle");
  };

  if (issueLoading) return (
    <div className={styles.center}>
      <Loader2 size={24} className={styles.spinner} />
      <span className={styles.loadingText}>FETCHING ISSUE DATA...</span>
    </div>
  );

  if (issueError) return (
    <div className={styles.center}>
      <AlertTriangle size={24} color="var(--neon-pink)" />
      <span className={styles.errorText}>{issueError}</span>
    </div>
  );

  return (
    <div className={styles.page}>
      {/* Back */}
      <button className={styles.backBtn} onClick={() => navigate("/")}>
        <ArrowLeft size={14} /> BACK
      </button>

      {/* Issue header */}
      <div className={styles.issueHeader}>
        <div className={styles.issueHeaderTop}>
          <span className={styles.issueNum}>#{issue.number}</span>
          <span className={styles.issueRepo}>{owner}/{repo}</span>
        </div>
        <h1 className={styles.issueTitle}>{issue.title}</h1>
        {issue.body && (
          <pre className={styles.issueBody}>{issue.body}</pre>
        )}
      </div>

      {/* Controls */}
      <div className={styles.controls}>
        {runState === "idle" && (
          <>
            <button className={styles.runBtn} onClick={() => triggerRun(false)}>
              <Play size={14} /> DEPLOY PATCHPILOT
            </button>
            <button className={styles.dryBtn} onClick={() => triggerRun(true)}>
              <Eye size={14} /> DRY RUN
            </button>
          </>
        )}
        {runState === "running" && (
          <button className={styles.stopBtn} onClick={stopRun}>
            <XCircle size={14} /> ABORT MISSION
          </button>
        )}
        {(runState === "done" || runState === "failed") && (
          <button className={styles.runBtn} onClick={() => triggerRun(dryRun)}>
            <Play size={14} /> RE-RUN
          </button>
        )}

        {/* Status badge */}
        {runState === "running" && (
          <div className={styles.statusBadge} data-state="running">
            <Loader2 size={12} className={styles.spinner} /> AGENT ACTIVE
          </div>
        )}
        {runState === "done" && (
          <div className={styles.statusBadge} data-state="done">
            <CheckCircle size={12} /> MISSION COMPLETE
          </div>
        )}
        {runState === "failed" && (
          <div className={styles.statusBadge} data-state="failed">
            <XCircle size={12} /> MISSION FAILED
          </div>
        )}
      </div>

      {/* Result summary */}
      {(prUrl || commitSha) && (
        <div className={styles.resultBar}>
          {commitSha && (
            <div className={styles.resultItem}>
              <GitCommit size={12} />
              <span className={styles.resultLabel}>COMMIT</span>
              <code className={styles.resultVal}>{commitSha.slice(0, 8)}</code>
            </div>
          )}
          {prUrl && (
            <a href={prUrl} target="_blank" rel="noreferrer" className={styles.prLink}>
              VIEW PULL REQUEST →
            </a>
          )}
        </div>
      )}

      {/* Two-column: stream + patch */}
      {steps.length > 0 && (
        <div className={styles.outputGrid}>
          <div className={styles.streamCol}>
            <div className={styles.colHeader}>
              <span className={styles.colTitle}>AGENT STREAM</span>
              <span className={styles.colCount}>{steps.length} STEPS</span>
            </div>
            <AgentStream steps={steps} running={runState === "running"} />
          </div>

          {patch && (
            <div className={styles.patchCol}>
              <div className={styles.colHeader}>
                <span className={styles.colTitle}>GENERATED PATCH</span>
              </div>
              <PatchViewer patch={patch} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
