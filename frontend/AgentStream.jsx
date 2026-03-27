import { useEffect, useRef } from "react";
import { Brain, Zap, CheckSquare, Code2, GitCommit, AlertTriangle } from "lucide-react";
import styles from "./AgentStream.module.css";

const STEP_CONFIG = {
  thought:  { icon: Brain,        label: "THINK",  color: "var(--neon-cyan)"   },
  action:   { icon: Zap,          label: "ACT",    color: "var(--neon-yellow)" },
  result:   { icon: CheckSquare,  label: "OUT",    color: "var(--neon-green)"  },
  patch:    { icon: Code2,        label: "PATCH",  color: "var(--neon-orange)" },
  commit:   { icon: GitCommit,    label: "GIT",    color: "var(--neon-green)"  },
  error:    { icon: AlertTriangle,label: "ERROR",  color: "var(--neon-pink)"   },
};

function StepRow({ step, index }) {
  const cfg = STEP_CONFIG[step.step_type] || STEP_CONFIG.result;
  const Icon = cfg.icon;

  // Truncate patch content in stream view
  const content = step.step_type === "patch"
    ? (step.content.split("\n").slice(0, 6).join("\n") + "\n...")
    : step.content;

  return (
    <div
      className={styles.step}
      style={{ animationDelay: `${index * 30}ms` }}
      data-type={step.step_type}
    >
      <div className={styles.stepLeft}>
        <div className={styles.stepIcon} style={{ color: cfg.color, borderColor: cfg.color }}>
          <Icon size={11} />
        </div>
        <div className={styles.stepLine} style={{ background: cfg.color }} />
      </div>
      <div className={styles.stepRight}>
        <div className={styles.stepHeader}>
          <span className={styles.stepLabel} style={{ color: cfg.color }}>{cfg.label}</span>
          {step.timestamp && (
            <span className={styles.stepTime}>
              {new Date(step.timestamp).toLocaleTimeString("en-US", { hour12: false })}
            </span>
          )}
        </div>
        <pre className={styles.stepContent}>{content}</pre>
      </div>
    </div>
  );
}

export default function AgentStream({ steps, running }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps.length]);

  return (
    <div className={styles.stream}>
      <div className={styles.inner}>
        {steps.map((step, i) => (
          <StepRow key={i} step={step} index={i} />
        ))}
        {running && (
          <div className={styles.cursor}>
            <span className={styles.cursorDot} />
            <span className={styles.cursorText}>AGENT PROCESSING...</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
