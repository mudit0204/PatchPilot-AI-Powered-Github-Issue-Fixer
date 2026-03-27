import { useState } from "react";
import { Copy, Check } from "lucide-react";
import styles from "./PatchViewer.module.css";

function DiffLine({ line }) {
  const isAdd    = line.startsWith("+") && !line.startsWith("+++");
  const isRemove = line.startsWith("-") && !line.startsWith("---");
  const isHunk   = line.startsWith("@@");
  const isMeta   = line.startsWith("---") || line.startsWith("+++") || line.startsWith("diff");

  const cls = isAdd ? styles.lineAdd
            : isRemove ? styles.lineRemove
            : isHunk   ? styles.lineHunk
            : isMeta   ? styles.lineMeta
            : styles.lineCtx;

  return (
    <div className={`${styles.line} ${cls}`}>
      <span className={styles.linePrefix}>
        {isAdd ? "+" : isRemove ? "−" : isHunk ? "≡" : " "}
      </span>
      <span className={styles.lineText}>{line.slice(isHunk ? 0 : 1)}</span>
    </div>
  );
}

export default function PatchViewer({ patch }) {
  const [copied, setCopied] = useState(false);

  const lines = patch.split("\n");

  const copy = async () => {
    await navigator.clipboard.writeText(patch);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const additions = lines.filter(l => l.startsWith("+") && !l.startsWith("+++")).length;
  const deletions  = lines.filter(l => l.startsWith("-") && !l.startsWith("---")).length;

  return (
    <div className={styles.viewer}>
      {/* Stats bar */}
      <div className={styles.statsBar}>
        <span className={styles.statAdd}>+{additions}</span>
        <span className={styles.statDel}>−{deletions}</span>
        <button className={styles.copyBtn} onClick={copy}>
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? "COPIED" : "COPY"}
        </button>
      </div>

      {/* Diff */}
      <div className={styles.diff}>
        {lines.map((line, i) => (
          <DiffLine key={i} line={line} />
        ))}
      </div>
    </div>
  );
}
