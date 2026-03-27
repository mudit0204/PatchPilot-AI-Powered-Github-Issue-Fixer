import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Search, RefreshCw, GitBranch, AlertCircle, Tag, Clock } from "lucide-react";
import { fetchIssues } from "../services/api";
import { formatDistanceToNow } from "date-fns";
import styles from "./Dashboard.module.css";

const LABEL_COLORS = {
  bug:         "#ff006e",
  enhancement: "#00f5ff",
  question:    "#ffe600",
  help:        "#00ff88",
  default:     "#7a9bb5",
};

function getLabelColor(name) {
  const lower = name.toLowerCase();
  for (const [key, val] of Object.entries(LABEL_COLORS)) {
    if (lower.includes(key)) return val;
  }
  return LABEL_COLORS.default;
}

export default function Dashboard() {
  const [owner, setOwner]   = useState(() => localStorage.getItem("pp_owner") || "");
  const [repo, setRepo]     = useState(() => localStorage.getItem("pp_repo")  || "");
  const [issues, setIssues] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState(null);
  const [loaded, setLoaded] = useState(false);
  const navigate = useNavigate();

  const load = async () => {
    if (!owner.trim() || !repo.trim()) return;
    setLoading(true);
    setError(null);
    localStorage.setItem("pp_owner", owner.trim());
    localStorage.setItem("pp_repo",  repo.trim());
    try {
      const data = await fetchIssues(owner.trim(), repo.trim());
      setIssues(data);
      setLoaded(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onKeyDown = (e) => { if (e.key === "Enter") load(); };

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <h1 className={styles.title}>
            <span className={styles.titleAccent}>{">"}</span> ISSUE TERMINAL
          </h1>
          <p className={styles.subtitle}>Select a repository to scan for issues</p>
        </div>
      </div>

      {/* Repo Input */}
      <div className={styles.repoInputCard}>
        <div className={styles.repoInputRow}>
          <div className={styles.inputGroup}>
            <label className={styles.inputLabel}>OWNER</label>
            <div className={styles.inputWrap}>
              <span className={styles.inputPrefix}>@</span>
              <input
                className={styles.input}
                placeholder="octocat"
                value={owner}
                onChange={e => setOwner(e.target.value)}
                onKeyDown={onKeyDown}
              />
            </div>
          </div>

          <div className={styles.inputSep}>/</div>

          <div className={styles.inputGroup}>
            <label className={styles.inputLabel}>REPOSITORY</label>
            <div className={styles.inputWrap}>
              <GitBranch size={13} className={styles.inputIcon} />
              <input
                className={styles.input}
                placeholder="hello-world"
                value={repo}
                onChange={e => setRepo(e.target.value)}
                onKeyDown={onKeyDown}
              />
            </div>
          </div>

          <button
            className={styles.scanBtn}
            onClick={load}
            disabled={loading || !owner || !repo}
          >
            {loading
              ? <RefreshCw size={14} className={styles.spinning} />
              : <Search size={14} />
            }
            {loading ? "SCANNING..." : "SCAN"}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className={styles.errorBanner}>
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      {/* Issues */}
      {loaded && !error && (
        <div className={styles.issuesSection}>
          <div className={styles.issuesHeader}>
            <span className={styles.issuesCount}>
              <span className={styles.countNum}>{issues.length}</span> OPEN ISSUES
            </span>
            <button className={styles.refreshBtn} onClick={load}>
              <RefreshCw size={12} /> REFRESH
            </button>
          </div>

          {issues.length === 0 ? (
            <div className={styles.emptyState}>
              <span className={styles.emptyIcon}>✓</span>
              <p>No open issues found. Repository is clean.</p>
            </div>
          ) : (
            <div className={styles.issueGrid}>
              {issues.map((issue, i) => (
                <div
                  key={issue.number}
                  className={styles.issueCard}
                  style={{ animationDelay: `${i * 40}ms` }}
                  onClick={() => navigate(`/issues/${owner}/${repo}/${issue.number}`)}
                >
                  <div className={styles.issueTop}>
                    <span className={styles.issueNum}>#{issue.number}</span>
                    <span className={styles.issuePilot}>PATCH →</span>
                  </div>

                  <h3 className={styles.issueTitle}>{issue.title}</h3>

                  {issue.body && (
                    <p className={styles.issueBody}>
                      {issue.body.slice(0, 120)}{issue.body.length > 120 ? "..." : ""}
                    </p>
                  )}

                  <div className={styles.issueMeta}>
                    {issue.labels?.map(lbl => (
                      <span
                        key={lbl}
                        className={styles.label}
                        style={{ borderColor: getLabelColor(lbl), color: getLabelColor(lbl) }}
                      >
                        <Tag size={9} /> {lbl}
                      </span>
                    ))}

                    {issue.created_at && (
                      <span className={styles.issueTime}>
                        <Clock size={10} />
                        {formatDistanceToNow(new Date(issue.created_at), { addSuffix: true })}
                      </span>
                    )}
                  </div>

                  <div className={styles.cardGlow} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Landing state */}
      {!loaded && !loading && (
        <div className={styles.landing}>
          <div className={styles.landingArt}>
            <div className={styles.hexGrid}>
              {Array.from({ length: 18 }).map((_, i) => (
                <div key={i} className={styles.hex} style={{ animationDelay: `${i * 0.15}s` }} />
              ))}
            </div>
          </div>
          <p className={styles.landingHint}>Enter a GitHub repository above to begin</p>
        </div>
      )}
    </div>
  );
}
