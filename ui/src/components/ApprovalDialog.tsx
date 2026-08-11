/**
 * The approval dialog: what will actually run, and a decision about it.
 *
 * docs/SECURITY.md section 5. Every rule in this file exists because the person reading this dialog
 * is deciding whether to let something touch their machine, and the only thing between them and a
 * misleading render is this component.
 *
 * **Everything is rendered as text.** React escapes interpolated values, and that is the mechanism -
 * not a sanitiser, not a filter. `dangerouslySetInnerHTML` is banned repository-wide and no markdown
 * renderer is imported anywhere near this path, both enforced by bans.test.ts. If any field of this
 * payload could carry markup into the DOM, then anything that can influence a string in it can
 * influence what the human sees while deciding - including hiding the real path behind link text.
 *
 * **Nothing dangerous is the focused default.** Reject takes focus, whatever the origin. Enter is
 * not bound to approve, and on a destructive operation Approve stays disabled briefly so a queued
 * keystroke or a reflexive click cannot confirm it. Reject is never delayed: making the safe answer
 * wait would be an argument for the dangerous one.
 */

import { useEffect, useRef, useState } from "react";
import type { ApprovalRequest } from "../contracts/types";

/** How long Approve stays disabled on a destructive operation. SECURITY.md section 5 says 2s. */
export const DESTRUCTIVE_ARMING_MS = 2000;

interface ApprovalDialogProps {
  readonly request: ApprovalRequest["payload"];
  readonly onDecision: (approve: boolean) => void;
}

export function ApprovalDialog({ request, onDecision }: ApprovalDialogProps) {
  const untrusted = request.origin === "untrusted_content";
  const destructive = request.side_effect === "destructive";

  const [armed, setArmed] = useState(!destructive);
  const rejectRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!destructive) return;

    const timer = window.setTimeout(() => setArmed(true), DESTRUCTIVE_ARMING_MS);
    return () => window.clearTimeout(timer);
  }, [destructive, request.request_id]);

  useEffect(() => {
    // The safe answer holds focus. A keystroke already in flight when this appeared lands here.
    rejectRef.current?.focus();
  }, [request.request_id]);

  return (
    <div
      className={untrusted ? "approval approval--untrusted" : "approval"}
      role="dialog"
      aria-modal="true"
      aria-label="Approval required"
    >
      <div className="approval__head">
        <p className="eyebrow">Approval required</p>
        <div className="approval__badges">
          <span className={`badge badge--${request.side_effect}`}>{request.side_effect}</span>
          <span className={untrusted ? "badge badge--untrusted" : "badge"}>{request.origin}</span>
        </div>
      </div>

      {untrusted && (
        <p className="approval__warning">
          This request exists because of content Local Zero read, not because you asked for it.
        </p>
      )}

      <p className="approval__capability">{request.capability}</p>

      <dl className="approval__args">
        {Object.entries(request.resolved_args).map(([name, value]) => (
          <div className="approval__arg" key={name}>
            <dt>{name}</dt>
            {/* String() rather than a template: a boolean or a null must be visible as what it is,
                and an empty render would be a field the user never saw. */}
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>

      <div className="approval__paths">
        <p className="eyebrow">Affected paths</p>
        {request.affected_paths.length === 0 ? (
          // "Touches nothing" and "we did not work out what it touches" are different facts, and
          // the user is entitled to know which one this is.
          <p className="approval__none">No files are affected by this operation.</p>
        ) : (
          <ul>
            {request.affected_paths.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="approval__controls">
        <button ref={rejectRef} type="button" className="button button--reject" onClick={() => onDecision(false)}>
          Reject
        </button>
        <button type="button" className="button button--approve" disabled={!armed} onClick={() => onDecision(true)}>
          {armed ? "Approve" : "Approve (wait…)"}
        </button>
      </div>
    </div>
  );
}
