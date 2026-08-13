import type { CapabilityResult } from "../contracts/types";

/**
 * What a capability that read something found.
 *
 * **Every cell is untrusted text.** A process name, a game title, an install directory — all of it
 * came from outside this machine's control, and a title is whatever a publisher typed into a store
 * page. It is safe to display for exactly one reason: React renders these as text nodes, and this
 * repository imports no markdown or HTML renderer. `bans.test.ts` is what keeps that true, and
 * `dangerouslySetInnerHTML` is banned repository-wide.
 *
 * Nothing here is interactive. A cell is not a link, not a button, and not a value anything sends
 * back to the brain — a table the user reads, and no further authority than that.
 */
export function ResultTable({ result }: { readonly result: CapabilityResult["payload"] }) {
  return (
    <div className="result">
      <div className="result__head">
        <span className="result__tool">{result.capability}</span>
        <span className="result__count">
          {result.rows.length} {result.rows.length === 1 ? "row" : "rows"}
        </span>
      </div>

      {result.rows.length === 0 ? (
        // A capability that ran and found none is a different fact from one that never ran. Drawing
        // nothing would make the two look identical.
        <p className="result__empty">It ran and found nothing.</p>
      ) : (
        <table className="result__table">
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              // Position is the identity. Nothing in a row is unique - two processes can share a
              // name and differ only by pid - so a key built from the cells would collapse them.
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {result.truncated && (
        // Reported by the brain, never inferred here from the row count. A list cut at the bound
        // and drawn without saying so is a lie about what is on the machine.
        <p className="result__truncated">
          There were more rows than fit; the rest were not sent.
        </p>
      )}
    </div>
  );
}
