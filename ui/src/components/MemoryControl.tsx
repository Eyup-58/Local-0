/**
 * What Local Zero remembers, and where the memory comes from.
 *
 * The panel's job is to make three things checkable at a glance, because each of them is a claim
 * the rest of the product makes and cannot prove on its own:
 *
 * * **whether memory is on at all.** Off is an ordinary state - no vault configured, or a renamed
 *   folder - and it reads as "off", never as an empty vault that happens to have nothing in it;
 * * **which vault**, in full, so a user with more than one can see which is loaded;
 * * **whether search ranks by meaning or by keyword alone.** Search that quietly gets worse is the
 *   failure nobody notices, so the degraded mode is named rather than hidden.
 *
 * Vault text never appears here. This is counts and configuration - the notes themselves reach the
 * user through the reader, marked as what they are.
 */

import type { MemoryStatus } from "../contracts/types";

interface MemoryControlProps {
  readonly status: MemoryStatus["payload"];
  readonly onReindex: () => void;
}

function formatSync(timestamp: string | null): string {
  if (timestamp === null) return "not scanned yet";

  const parsed = Date.parse(timestamp);
  return Number.isNaN(parsed) ? "not scanned yet" : new Date(parsed).toLocaleTimeString();
}

export function MemoryControl({ status, onReindex }: MemoryControlProps) {
  if (!status.enabled) {
    return (
      <section className="memory" aria-label="Memory">
        <p className="memory__state" role="status">
          <strong>Memory off</strong> — no vault is configured. Set OBSIDIAN_VAULT_PATH and restart
          to give Local Zero a long-term memory. Everything else works without one.
        </p>
      </section>
    );
  }

  return (
    <section className="memory" aria-label="Memory">
      <p className="memory__state" role="status">
        <strong>Memory on</strong> — {status.notes} notes, {status.chunks} passages, last scanned{" "}
        {formatSync(status.last_indexed_at)}.
      </p>

      <dl className="memory__facts">
        <div>
          <dt>Vault</dt>
          <dd className="memory__path">{status.vault}</dd>
        </div>
        <div>
          <dt>Search</dt>
          <dd>
            {status.embeddings_available
              ? `by meaning and keyword — ${status.embedded_chunks} of ${status.chunks} passages embedded`
              : "keyword only — no embedding model answered"}
          </dd>
        </div>
      </dl>

      {/* Wrapped rather than passed directly: onClick would hand the DOM event to a callback whose
          signature takes nothing, and the next person to add a parameter would find one already
          there. */}
      <button type="button" className="button" onClick={() => onReindex()}>
        Rescan vault
      </button>
    </section>
  );
}
