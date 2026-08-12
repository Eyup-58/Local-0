/**
 * The chrome around the orb: the top bar, the reading strip, the left rail and the caption.
 *
 * Every string these render comes from a sample or from connection state. There is no scripted
 * dialogue and no idle chatter - the caption says what the panel is actually doing, and when it is
 * doing nothing it says that. A HUD that narrates is a HUD that is eventually wrong.
 */

import type { ReactNode } from "react";

import { SensorGap } from "./SensorGap";

export type LinkTone = "live" | "stale" | "waiting";

const LINK_WORDS: Record<LinkTone, string> = {
  live: "LINKED",
  stale: "STALE",
  waiting: "NO LINK",
};

export interface TopBarProps {
  readonly tone: LinkTone;
  readonly trustEnabled: boolean;
  readonly providerMode: string | null;
  readonly clock: string;
}

export function TopBar({ tone, trustEnabled, providerMode, clock }: TopBarProps) {
  return (
    <header className="hud__top">
      <p className="hud__mark">
        LOCAL<span className="hud__mark-tail">ZERO</span>
      </p>

      <div className="hud__pills">
        <span className={`pill pill--${tone}`}>
          <span className="pill__dot" aria-hidden="true" />
          {LINK_WORDS[tone]}
        </span>

        <span className={trustEnabled ? "pill pill--unguarded" : "pill pill--guarded"}>
          <span className="pill__dot" aria-hidden="true" />
          {trustEnabled ? "APPROVAL OFF" : "GUARDED"}
        </span>
      </div>

      <div className="hud__pills hud__pills--right">
        <span className="pill pill--quiet">{(providerMode ?? "—").toUpperCase()}</span>
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
}

export function ReadingStrip({ readings }: { readonly readings: readonly Reading[] }) {
  return (
    <div className="strip">
      {readings.map((reading) => (
        <div className="strip__cell" key={reading.label}>
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
  );
}

export interface RailItem {
  readonly id: string;
  readonly glyph: string;
  readonly title: string;
}

export interface RailProps {
  readonly items: readonly RailItem[];
  readonly active: string;
  readonly onSelect: (id: string) => void;
}

/**
 * The left rail switches what the dock shows. Every entry opens something real - a rail of icons
 * that only three of which do anything is a rail that trains you to ignore it.
 */
export function Rail({ items, active, onSelect }: RailProps) {
  return (
    <nav className="hud__rail" aria-label="Panels">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={item.id === active ? "rail-key rail-key--active" : "rail-key"}
          onClick={() => onSelect(item.id)}
          aria-pressed={item.id === active}
          title={item.title}
        >
          <span aria-hidden="true">{item.glyph}</span>
          <span className="rail-key__name">{item.title}</span>
        </button>
      ))}
    </nav>
  );
}

/**
 * The line under the orb. It is the panel's own voice and it only ever states fact - what is
 * waiting, what failed, or what is being measured.
 */
export function Caption({ text, tone }: { readonly text: string; readonly tone: LinkTone | "alarm" }) {
  return (
    <p className={`caption caption--${tone}`} role="status">
      {text}
    </p>
  );
}

export function Dock({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <aside className="dock">
      <div className="dock__head">
        <p className="eyebrow">{title}</p>
      </div>
      <div className="dock__body">{children}</div>
    </aside>
  );
}
