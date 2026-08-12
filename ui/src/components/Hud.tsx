/**
 * The chrome around the core: the top bar, the reading strip, the left rail, the caption stack,
 * the control track and the dock.
 *
 * Every string these render comes from a sample, from connection state, or from a turn the brain
 * reported. There is no scripted dialogue and no idle chatter. The caption is the one element here
 * carrying the brain's own words, and when the brain has nothing to say it renders nothing at all
 * rather than a greeting - a HUD that narrates is a HUD that is eventually wrong.
 *
 * The layout is the orchestration-centre arrangement: the dock slides in over the right edge and
 * the rest of the chrome contracts to meet it, which is what lets the core keep its own centre in
 * whatever space is left. That contraction is a CSS custom property (--chrome-right), not a
 * measurement taken in JavaScript, so nothing here re-renders on a resize.
 */

import { useState, type FormEvent, type ReactNode } from "react";

import { SensorGap } from "./SensorGap";
import type { ToolLog, TurnStateName } from "../contracts/types";

export type LinkTone = "live" | "stale" | "waiting";

/** What the state pill says for each reported turn. */
const TURN_WORDS: Record<TurnStateName, string> = {
  idle: "STANDBY",
  listening: "LISTENING",
  thinking: "THINKING",
  tool_running: "TOOL RUNNING",
  speaking: "SPEAKING",
};

export interface TopBarProps {
  readonly turn: TurnStateName;
  readonly tone: LinkTone;
  readonly trustEnabled: boolean;
  readonly clock: string;
}

export function TopBar({ turn, tone, trustEnabled, clock }: TopBarProps) {
  return (
    <header className="hud__top">
      <p className="hud__mark">
        LOCAL<span className="hud__mark-tail">_ZERO</span>
        <span className="hud__mark-tm">TM</span>
      </p>

      <div className="hud__pills">
        <span className="pill" role="status">
          <span className="pill__dot" aria-hidden="true" />
          {/* The link outranks the turn. A stale panel saying SPEAKING would be reporting a turn
              it cannot currently see. */}
          {tone === "live" ? TURN_WORDS[turn] : tone === "stale" ? "STALE" : "NO LINK"}
        </span>
      </div>

      <div className="hud__pills hud__pills--right">
        <span className={trustEnabled ? "pill pill--unguarded" : "pill pill--guard"}>
          <span className="pill__dot" aria-hidden="true" />
          {trustEnabled ? "APPROVAL OFF" : "GUARDED"}
        </span>
        <span className="hud__clock">{clock}</span>
      </div>
    </header>
  );
}

export interface Reading {
  readonly label: string;
  /** Null is a gap, and it is rendered as one. It is never shown as a zero. */
  readonly value: string | null;
  readonly unit?: string;
  readonly reason: string;
  /** Dropped first when the strip runs out of room. The leading readings are never roomy. */
  readonly roomy?: boolean;
}

export function ReadingStrip({ readings }: { readonly readings: readonly Reading[] }) {
  return (
    <div className="strip">
      <div className="strip__inner">
        {readings.map((reading) => (
          <div
            className={reading.roomy ? "strip__cell strip__cell--roomy" : "strip__cell"}
            key={reading.label}
          >
            <span className="strip__label">{reading.label}</span>
            {reading.value === null ? (
              <SensorGap label={reading.label} reason={reading.reason} inline />
            ) : (
              <span className="strip__value">
                {reading.value}
                {reading.unit ? <span className="strip__unit">{reading.unit}</span> : null}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export interface RailItem {
  readonly id: string;
  /** Inline SVG, so the rail needs no icon font and makes no image request. */
  readonly icon: ReactNode;
  readonly title: string;
}

export interface RailProps {
  readonly items: readonly RailItem[];
  readonly active: string;
  readonly dockOpen: boolean;
  readonly onSelect: (id: string) => void;
  readonly onClose: () => void;
}

/**
 * The left rail switches what the dock shows. Every entry opens something real - a rail of icons
 * only three of which do anything is a rail that trains you to ignore it, so there is no entry
 * here for a capability that has not been built.
 *
 * The active marker is drawn only while the dock is open, because the rail is a set of doors and
 * highlighting one while none is open says a panel is showing when it is not.
 */
export function Rail({ items, active, dockOpen, onSelect, onClose }: RailProps) {
  return (
    <nav className="hud__rail" aria-label="Panels">
      <div className="hud__sigil" aria-hidden="true">
        0
      </div>

      {items.map((item) => {
        const open = dockOpen && item.id === active;
        return (
          <button
            key={item.id}
            type="button"
            className={open ? "rail-key rail-key--active" : "rail-key"}
            onClick={() => onSelect(item.id)}
            aria-pressed={open}
            aria-expanded={open}
            title={item.title}
          >
            <span className="rail-key__marker" aria-hidden="true" />
            {item.icon}
            <span className="rail-key__name">{item.title}</span>
          </button>
        );
      })}

      <div className="hud__rail-spacer" />

      <button type="button" className="rail-key" onClick={onClose} title="Collapse panel">
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          aria-hidden="true"
        >
          <path d="M4 5h16v14H4zM15 5v14M9.5 10l2.5 2-2.5 2" />
        </svg>
        <span className="rail-key__name">Collapse panel</span>
      </button>
    </nav>
  );
}

export interface CaptionProps {
  readonly turn: TurnStateName;
  /** The brain's own words, or null when it has nothing to say. Null renders nothing. */
  readonly caption: string | null;
  /** The kicker above the caption: what the turn is about. Null renders nothing. */
  readonly detail: string | null;
  /** Set when the panel itself has something to report that outranks the turn. */
  readonly notice: { readonly text: string; readonly tone: LinkTone | "alarm" } | null;
}

/**
 * The stack under the core: level meter, wordmark, kicker, and the line itself.
 *
 * Two sources feed the line and they are kept apart on purpose. `notice` is the panel's own voice
 * and it only ever states fact - what is waiting, what failed, what is not being measured.
 * `caption` is the brain's. When both exist the notice wins, because whatever the brain is saying
 * is not what the user needs while an approval sits unanswered.
 */
export function Caption({ turn, caption, detail, notice }: CaptionProps) {
  const vu =
    turn === "speaking"
      ? "caption__vu caption__vu--speaking"
      : turn === "listening"
        ? "caption__vu caption__vu--listening"
        : "caption__vu";

  const line = notice ?? (caption === null ? null : { text: caption, tone: null });
  const kicker = notice === null ? detail : null;

  return (
    <div className="caption-stack">
      <div className={vu} aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>

      <p className="caption__mark">ZERO</p>
      {kicker === null ? null : <p className="caption__kicker">{kicker}</p>}

      {line === null ? null : (
        <p
          className={
            line.tone === null ? "caption__text" : `caption__text caption__text--${line.tone}`
          }
          role="status"
        >
          {line.text}
          {/*
            The caret marks a line the brain is still adding to. It is not a typewriter: nothing
            here reveals text character by character, because that would put words on screen in an
            order the brain never sent them in, and briefly show sentences it never said.
          */}
          {line.tone === null && turn === "speaking" ? (
            <span className="caption__caret" aria-hidden="true">
              ▌
            </span>
          ) : null}
        </p>
      )}
    </div>
  );
}

export interface AskBoxProps {
  readonly onAsk: (text: string) => boolean;
  /** False while the socket is down, so the box says so rather than swallowing what was typed. */
  readonly connected: boolean;
}

/**
 * The one place the user can put something into the system.
 *
 * It clears only when `onAsk` reports the text actually left. A box that emptied itself on a closed
 * socket would look exactly like a box that had sent something, and the user would be waiting on a
 * turn that never started.
 */
export function AskBox({ onAsk, connected }: AskBoxProps) {
  const [text, setText] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (onAsk(text)) setText("");
  };

  return (
    <form className="ask" onSubmit={submit}>
      <input
        className="ask__input"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={connected ? "Ask Zero" : "Waiting for the brain"}
        aria-label="Ask Zero"
        disabled={!connected}
        maxLength={4000}
        autoComplete="off"
      />
      <button className="ask__send" type="submit" disabled={!connected} aria-label="Send request">
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          aria-hidden="true"
        >
          <path d="M4 12h15M13 6l6 6-6 6" />
        </svg>
      </button>
    </form>
  );
}

export function Dock({
  title,
  open,
  onClose,
  children,
}: {
  readonly title: string;
  readonly open: boolean;
  readonly onClose: () => void;
  readonly children: ReactNode;
}) {
  return (
    <aside className={open ? "dock dock--open" : "dock"} aria-hidden={!open} inert={!open}>
      <div className="dock__head">
        <p className="dock__title">{title.toUpperCase()}</p>
        <button type="button" className="dock__close" onClick={onClose} title="Close panel">
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
          <span className="rail-key__name">Close panel</span>
        </button>
      </div>
      <div className="dock__body">{children}</div>
    </aside>
  );
}

/** Just the time part of a contract timestamp. Returns null rather than inventing a clock. */
function clockPart(stamp: string): string | null {
  const parsed = new Date(stamp);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleTimeString([], { hour12: false });
}

/**
 * What the brain reported running, newest first.
 *
 * Only ever what it reported. An empty log means no tool call has been announced on this
 * connection, and it says exactly that - it does not backfill from the audit log it cannot read,
 * and it does not present an empty list as "nothing has ever run".
 */
export function ToolLogList({ lines }: { readonly lines: readonly ToolLog["payload"][] }) {
  if (lines.length === 0) {
    return (
      <p className="toollog__empty">
        No tool call has been reported on this connection. Anything that ran before this tab
        connected is in the audit log, not here.
      </p>
    );
  }

  return (
    <div className="toollog">
      {lines.map((line, index) => (
        <div
          // Nothing in the payload is unique - the same capability can log the same message twice
          // within one millisecond. Position is the identity, and the list only grows at the head.
          key={`${line.at}-${index}`}
          className={`toollog__row toollog__row--${line.status}`}
        >
          <span className="toollog__at">{clockPart(line.at) ?? "--:--:--"}</span>
          <span className="toollog__tool">{line.capability}</span>
          <span className="toollog__msg">{line.message}</span>
          <span className="toollog__status">{line.status.toUpperCase()}</span>
        </div>
      ))}
    </div>
  );
}
