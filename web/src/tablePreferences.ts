import type { CSSProperties } from "react";
import { PRIORITY_STOPS, type PriorityStop } from "./tableAutomation.ts";

export const TABLE_PREFERENCES_KEY = "commander-table-preferences:v2";
export const LEGACY_TABLE_PREFERENCES_KEY = "commander-table-preferences:v1";

export type RightRailPanel = "stack" | "activity" | "card";
export type BoardDensity = "comfortable" | "compact";
export type OpponentView = "all" | "A" | "B" | "C" | "D";
export type TableColors = { accent: string; background: string };

export const DEFAULT_TABLE_COLORS: Readonly<TableColors> = { accent: "#a6ed72", background: "#101713" };
const TABLE_COLORS_KEY = "quorune-table-colors";

export function parseTableColors(raw: string | null): TableColors {
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as Partial<TableColors>;
      if (typeof parsed.accent === "string" && /^#[0-9a-f]{6}$/i.test(parsed.accent)
        && typeof parsed.background === "string" && /^#[0-9a-f]{6}$/i.test(parsed.background)) {
        return { accent: parsed.accent.toLowerCase(), background: parsed.background.toLowerCase() };
      }
    } catch {
      // An older or incomplete saved preference falls back to the standard colors.
    }
  }
  return { ...DEFAULT_TABLE_COLORS };
}

export function loadTableColors(userId: string): TableColors {
  return parseTableColors(userId ? window.localStorage.getItem(`${TABLE_COLORS_KEY}:${userId}`) : null);
}

export function saveTableColors(userId: string, colors: TableColors): void {
  if (userId) window.localStorage.setItem(`${TABLE_COLORS_KEY}:${userId}`, JSON.stringify(colors));
}

export function tableColorStyle(colors: TableColors): CSSProperties | undefined {
  if (colors.accent === DEFAULT_TABLE_COLORS.accent && colors.background === DEFAULT_TABLE_COLORS.background) return undefined;
  const channels = [1, 3, 5].map((start) => Number.parseInt(colors.accent.slice(start, start + 2), 16) / 255);
  const [red, green, blue] = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  return {
    "--user-table-background": colors.background,
    "--accent": colors.accent,
    "--accent-strong": `color-mix(in srgb, ${colors.accent} 75%, white)`,
    "--accent-ink": luminance >= .179 ? "#000000" : "#ffffff",
  } as CSSProperties;
}

export const CARD_SIZE_ZONES = [
  ["hand", "Hand"],
  ["battlefield", "Battlefield"],
  ["command", "Command zone"],
  ["graveyard", "Graveyard"],
  ["exile", "Exile"],
  ["library", "Library choices"],
  ["stack", "Stack"],
] as const;
export const MIN_CARD_SIZE = 0.6;
export const MAX_CARD_SIZE = 1.6;
export const MIN_HAND_PANEL_HEIGHT = 100;
export const MAX_HAND_PANEL_HEIGHT = 650;
export type CardSizeZone = typeof CARD_SIZE_ZONES[number][0];

export type TablePreferences = {
  autoMana: boolean;
  autoPass: boolean;
  holdNewStack: boolean;
  holdGraveyard: boolean;
  passWithOptionalActivations: boolean;
  priorityStops: PriorityStop[];
  seatPriorityStops: Partial<Record<Exclude<OpponentView, "all">, PriorityStop[]>>;
  handPanelHeight: number;
  handWidth: number;
  handArtFocused: boolean;
  opponentWidths: Partial<Record<Exclude<OpponentView, "all">, number>>;
  handAutoCollapse: boolean;
  cardScale: number;
  zoneCardScale: Partial<Record<CardSizeZone, number>>;
  opponentView: OpponentView;
  rightRailWidth: number;
  rightRailOrder: RightRailPanel[];
  inspectorCollapsed: boolean;
  boardDensity: BoardDensity;
  compactPhaseRail: boolean;
  activityVisible: boolean;
};

export const DEFAULT_TABLE_PREFERENCES: Readonly<TablePreferences> = {
  autoMana: true,
  autoPass: true,
  holdNewStack: false,
  holdGraveyard: false,
  passWithOptionalActivations: false,
  priorityStops: [],
  seatPriorityStops: {},
  handPanelHeight: 190,
  handWidth: 66,
  handArtFocused: false,
  opponentWidths: {},
  handAutoCollapse: true,
  cardScale: 1,
  zoneCardScale: {},
  opponentView: "all",
  rightRailWidth: 270,
  rightRailOrder: ["stack", "activity", "card"],
  inspectorCollapsed: false,
  boardDensity: "comfortable",
  compactPhaseRail: false,
  activityVisible: true,
};

function boundedNumber(value: unknown, fallback: number, minimum: number, maximum: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback;
}

export function priorityStopsForTurn(preferences: TablePreferences, activeSeat: Exclude<OpponentView, "all"> | undefined): PriorityStop[] {
  const scoped = activeSeat ? preferences.seatPriorityStops[activeSeat] ?? [] : [];
  return [...new Set([...preferences.priorityStops, ...scoped])];
}

function priorityStops(raw: unknown): PriorityStop[] {
  return PRIORITY_STOPS.filter(({ id }) => Array.isArray(raw) && raw.includes(id)).map(({ id }) => id);
}

function seatPriorityStops(raw: unknown): TablePreferences["seatPriorityStops"] {
  if (!raw || typeof raw !== "object") return {};
  const saved = raw as Record<string, unknown>;
  return Object.fromEntries(["A", "B", "C", "D"].flatMap((seat) => {
    const selected = priorityStops(saved[seat]).filter((stop) => stop !== "before_my_turn");
    return selected.length ? [[seat, selected]] : [];
  }));
}

function zoneSizes(raw: unknown): TablePreferences["zoneCardScale"] {
  if (!raw || typeof raw !== "object") return {};
  const settings = raw as Record<string, unknown>;
  return Object.fromEntries(CARD_SIZE_ZONES.flatMap(([zone]) =>
    typeof settings[zone] === "number"
      ? [[zone, boundedNumber(settings[zone], 1, MIN_CARD_SIZE, MAX_CARD_SIZE)]]
      : []));
}

export function effectiveCardScale(value: TablePreferences, zone: CardSizeZone): number {
  return value.cardScale * (value.zoneCardScale[zone] ?? 1);
}

export function tablePreferenceStyle(tablePreferences: TablePreferences): CSSProperties {
  return {
    ...Object.fromEntries(CARD_SIZE_ZONES.map(([zone]) => [`--${zone}-card-scale`, effectiveCardScale(tablePreferences, zone)])),
    "--hand-share": `${tablePreferences.handWidth}%`,
    "--hand-panel-height": `${tablePreferences.handPanelHeight}px`,
    "--card-scale": String(tablePreferences.cardScale),
    "--right-rail-width": `${tablePreferences.rightRailWidth}px`,
  } as CSSProperties;
}

function railOrder(value: unknown): RightRailPanel[] {
  if (!Array.isArray(value)) return [...DEFAULT_TABLE_PREFERENCES.rightRailOrder];
  const allowed = new Set<RightRailPanel>(["stack", "activity", "card"]);
  const result = value.filter((item): item is RightRailPanel => typeof item === "string" && allowed.has(item as RightRailPanel));
  return result.length === allowed.size && new Set(result).size === allowed.size
    ? result
    : [...DEFAULT_TABLE_PREFERENCES.rightRailOrder];
}

export function parseTablePreferences(raw: string | null): TablePreferences {
  if (!raw) return { ...DEFAULT_TABLE_PREFERENCES, rightRailOrder: [...DEFAULT_TABLE_PREFERENCES.rightRailOrder] };
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    return {
      autoMana: typeof value.autoMana === "boolean" ? value.autoMana : DEFAULT_TABLE_PREFERENCES.autoMana,
      autoPass: typeof value.autoPass === "boolean" ? value.autoPass : DEFAULT_TABLE_PREFERENCES.autoPass,
      holdNewStack: typeof value.holdNewStack === "boolean" ? value.holdNewStack : DEFAULT_TABLE_PREFERENCES.holdNewStack,
      holdGraveyard: value.holdGraveyard === true,
      passWithOptionalActivations: value.passWithOptionalActivations === true,
      priorityStops: priorityStops(value.priorityStops),
      seatPriorityStops: seatPriorityStops(value.seatPriorityStops),
      handPanelHeight: boundedNumber(value.handPanelHeight, DEFAULT_TABLE_PREFERENCES.handPanelHeight, MIN_HAND_PANEL_HEIGHT, MAX_HAND_PANEL_HEIGHT),
      handWidth: boundedNumber(value.handWidth, 66, 35, 76),
      handArtFocused: value.handArtFocused === true,
      opponentWidths: Object.fromEntries(["A", "B", "C", "D"].flatMap((seat) => {
        const widths = value.opponentWidths as Record<string, unknown> | null | undefined;
        return typeof widths?.[seat] === "number" ? [[seat, boundedNumber(widths[seat], 100, 50, 200)]] : [];
      })),
      handAutoCollapse: typeof value.handAutoCollapse === "boolean" ? value.handAutoCollapse : DEFAULT_TABLE_PREFERENCES.handAutoCollapse,
      cardScale: boundedNumber(value.cardScale, DEFAULT_TABLE_PREFERENCES.cardScale, MIN_CARD_SIZE, MAX_CARD_SIZE),
      zoneCardScale: zoneSizes(value.zoneCardScale),
      opponentView: value.opponentView === "A" || value.opponentView === "B" || value.opponentView === "C" || value.opponentView === "D"
        ? value.opponentView : "all",
      rightRailWidth: boundedNumber(value.rightRailWidth, DEFAULT_TABLE_PREFERENCES.rightRailWidth, 260, 520),
      rightRailOrder: railOrder(value.rightRailOrder),
      inspectorCollapsed: typeof value.inspectorCollapsed === "boolean" ? value.inspectorCollapsed : DEFAULT_TABLE_PREFERENCES.inspectorCollapsed,
      boardDensity: value.boardDensity === "compact" || value.boardDensity === "comfortable" ? value.boardDensity : DEFAULT_TABLE_PREFERENCES.boardDensity,
      compactPhaseRail: typeof value.compactPhaseRail === "boolean" ? value.compactPhaseRail : DEFAULT_TABLE_PREFERENCES.compactPhaseRail,
      activityVisible: typeof value.activityVisible === "boolean" ? value.activityVisible : DEFAULT_TABLE_PREFERENCES.activityVisible,
    };
  } catch {
    return { ...DEFAULT_TABLE_PREFERENCES, rightRailOrder: [...DEFAULT_TABLE_PREFERENCES.rightRailOrder] };
  }
}

export function loadTablePreferences(): TablePreferences {
  if (typeof window === "undefined") return parseTablePreferences(null);
  const current = window.localStorage.getItem(TABLE_PREFERENCES_KEY);
  if (current) return parseTablePreferences(current);
  return parseTablePreferences(window.localStorage.getItem(LEGACY_TABLE_PREFERENCES_KEY));
}

export function saveTablePreferences(value: TablePreferences): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TABLE_PREFERENCES_KEY, JSON.stringify(value));
  window.localStorage.removeItem(LEGACY_TABLE_PREFERENCES_KEY);
}
