/**
 * The network boundary, shown and switched.
 *
 * docs/SECURITY.md §11: Local mode sends nothing off this machine and Cloud mode permits outbound.
 * That difference is the most consequential setting in the product, so this component is written
 * around one rule: **the user can always tell which one is in force**, without opening anything.
 *
 * Two things it deliberately does not do:
 *
 * * **It never displays the key**, not even a masked prefix. The brain sends a boolean; there is
 *   nothing here to render even if somebody wanted to. The input is cleared the moment it is sent.
 * * **It does not decide anything.** Selecting Cloud without a stored key is refused by the brain,
 *   not disabled here — a control that greys itself out is a client-side rule, and the boundary is
 *   not a client-side rule. The disabled attribute below is a courtesy that saves a round trip; the
 *   refusal still exists on the other end.
 */

import { useState } from "react";
import type { ProviderMode } from "../contracts/types";

interface ProviderControlProps {
  readonly mode: ProviderMode;
  readonly model: string;
  readonly hasKey: boolean;
  readonly onSelect: (mode: ProviderMode) => void;
  readonly onKey: (key: string) => void;
}

export function ProviderControl({ mode, model, hasKey, onSelect, onKey }: ProviderControlProps) {
  const [draft, setDraft] = useState("");

  const submitKey = () => {
    const value = draft.trim();
    if (value.length === 0) return;

    onKey(value);
    // Cleared on send, not on acknowledgement. A key sitting in a form field is a key on screen.
    setDraft("");
  };

  return (
    <section className="provider" aria-label="Model provider">
      <p className="provider__state" role="status">
        {mode === "local" ? (
          <>
            <strong>Local</strong> — nothing leaves this machine. Model: {model || "not reported"}
          </>
        ) : (
          <>
            <strong>Cloud</strong> — requests go to the provider. Model: {model || "not reported"}
          </>
        )}
      </p>

      <div className="provider__controls">
        <button
          type="button"
          className="button"
          onClick={() => onSelect("local")}
          disabled={mode === "local"}
        >
          Use local
        </button>
        <button
          type="button"
          className="button"
          onClick={() => onSelect("cloud")}
          disabled={mode === "cloud" || !hasKey}
        >
          Use cloud
        </button>
      </div>

      <div className="provider__key">
        <label className="provider__label" htmlFor="provider-key">
          {hasKey ? "A key is stored. Entering another replaces it." : "No key is stored."}
        </label>
        <input
          id="provider-key"
          className="provider__input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          placeholder="Provider key"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="button" className="button" onClick={submitKey} disabled={draft.trim().length === 0}>
          Store key
        </button>
      </div>

      <p className="provider__note">
        The key is written to the Windows Credential Manager. It is never written to a file in this
        project, never logged, and never sent back to this screen.
      </p>
    </section>
  );
}
