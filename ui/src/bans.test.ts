/**
 * Repository-wide bans, enforced as tests rather than as discipline.
 *
 * The ROADMAP makes grep the evidence for these. A grep somebody remembers to run is not evidence,
 * so the greps live here and run on every `npm test`.
 *
 * Both bans exist because the approval dialog in M3 will render backend-resolved text describing
 * an operation the user is about to authorize. If any of that text can become markup, an attacker
 * who controls a filename controls what the user thinks they are approving. The rules are
 * established now, while the surface is small, rather than at the point they start to matter.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(import.meta.dirname);

const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".css", ".html"]);

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return SOURCE_EXTENSIONS.has(extname(path)) ? [path] : [];
  });
}

/** Lines that are not comments, so prose may name what code may not do. */
function codeLines(path: string): { readonly number: number; readonly text: string }[] {
  return readFileSync(path, "utf-8")
    .split(/\r?\n/)
    .map((text, index) => ({ number: index + 1, text }))
    .filter(({ text }) => {
      const trimmed = text.trimStart();
      return !trimmed.startsWith("//") && !trimmed.startsWith("*") && !trimmed.startsWith("/*");
    });
}

function offenders(pattern: RegExp): string[] {
  return sourceFiles(SRC)
    .filter((path) => !path.endsWith("bans.test.ts"))
    .flatMap((path) =>
      codeLines(path)
        .filter(({ text }) => pattern.test(text))
        .map(({ number, text }) => `${relative(SRC, path)}:${number}: ${text.trim()}`),
    );
}

describe("repository bans", () => {
  it("never uses dangerouslySetInnerHTML", () => {
    const found = offenders(/dangerouslySetInnerHTML/);

    expect(found, `dangerouslySetInnerHTML is banned repository-wide:\n${found.join("\n")}`).toEqual([]);
  });

  it("imports no markdown or HTML renderer", () => {
    // Named explicitly rather than pattern-matched on "markdown": a renderer imported under an
    // unrelated name is what this is meant to catch, and the list is short enough to maintain.
    const found = offenders(/from\s+["'](react-markdown|marked|markdown-it|showdown|dompurify|sanitize-html)["']/);

    expect(found, `no markdown or HTML renderer may be imported:\n${found.join("\n")}`).toEqual([]);
  });

  it("never calls innerHTML directly", () => {
    const found = offenders(/\.innerHTML\s*=/);

    expect(found, `assigning innerHTML bypasses React's escaping:\n${found.join("\n")}`).toEqual([]);
  });

  it("loads no remote fonts or stylesheets", () => {
    // Whether Local Zero makes outbound connections at all is an open question in SECURITY.md.
    // A font CDN would answer it by accident.
    const found = offenders(/@import\s+url\(|https:\/\/fonts\./);

    expect(found, `the UI must not fetch remote resources:\n${found.join("\n")}`).toEqual([]);
  });
});
