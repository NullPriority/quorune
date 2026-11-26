import type { CommandEnvelope, CommandReceipt } from "./generated/protocol";

const TAB_STORAGE_KEY = "commander-browser-tab";

function newTabId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function browserTabId(): string {
  const existing = sessionStorage.getItem(TAB_STORAGE_KEY);
  if (existing && /^[0-9a-f]{32}$/.test(existing)) return existing;
  const created = newTabId();
  sessionStorage.setItem(TAB_STORAGE_KEY, created);
  return created;
}

export function beginGuestSession(): void {
  // A duplicated/incognito tab may inherit sessionStorage. Rotating before an
  // explicit login guarantees that this page selects its own HttpOnly cookie.
  sessionStorage.setItem(TAB_STORAGE_KEY, newTabId());
}

function csrfToken(): string {
  const row = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("commander_csrf="));
  return row ? decodeURIComponent(row.split("=", 2)[1]) : "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrfToken() } : {}),
      "X-Commander-Tab": browserTabId(),
      ...(init.headers ?? {}),
    },
  });
  const payload = (await response.json()) as { detail?: unknown };
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && "message" in detail
        ? String((detail as { message: unknown }).message)
        : `Request failed: ${response.status}`;
    throw new ApiError(message, response.status, detail);
  }
  return payload as T;
}

export interface Guest {
  guest_id: string;
  display_name: string;
  expires_at: string;
}

export interface SavedDeck {
  id: string;
  name: string;
  commanders: string[];
}

export interface RecentGame {
  game_id: string;
  room_id: string;
  status: GameLifecycle["status"];
  created_at: string;
  format_profile: string;
}

export interface DeckPreflight {
  trusted_only_ready: boolean;
  card_rules_ready?: boolean;
  data_integrity_issue?: boolean;
  card_behavior_ready?: boolean;
  format_verification_pending?: boolean;
  affected_cards?: { name: string; reasons: string[] }[];
  affected_card_count?: number;
  data_integrity_blockers?: CardDataMismatch[];
}

export interface CardSourceExcerpt {
  text: string;
  published_at?: string;
  source?: string;
}

export interface CardDataMismatch {
  diagnostic_id: string;
  kind: string;
  card: string;
  oracle_id: string;
  programs: { key: string; origin: string; oracle_hash: string | null; rulings_hash: string | null }[];
  comparison?: {
    reviewed_date: string | null;
    current_date: string | null;
    changes: { kind: "added" | "removed" | "changed" | "formatting"; before?: CardSourceExcerpt; after?: CardSourceExcerpt }[];
  };
}

export interface Seat {
  seat: string;
  guest_id: string | null;
  display_name: string | null;
  ready: boolean;
  mine: boolean;
  deck: {
    deck_id: string;
    name: string;
    deck_list_fingerprint: string;
    preflight?: DeckPreflight;
    format_legality: {
      status: string;
      issue_count: number;
      issues: LegalityIssue[];
    };
  } | null;
}

export interface LegalityIssue {
  code: string;
  message: string;
  card: string;
  board: string;
  legality: string;
  released_at: string | null;
  confirmable: boolean;
}

export interface LegalityConfirmationRequired {
  code: "legality_confirmation_required";
  message: string;
  confirmation: string;
  deck_list_fingerprint: string;
  issues: LegalityIssue[];
}

export interface Room {
  room_id: string;
  owner_guest_id: string;
  status: string;
  game_id: string | null;
  format_profile: string;
  seat_count: 2 | 4;
  spectator: boolean;
  seats: Seat[];
}

export interface GameLifecycle {
  game_id: string;
  room_id: string;
  status: "active" | "paused" | "complete" | "aborted";
  state_revision: number;
  format_profile: string;
  seat: string | null;
  spectator: boolean;
  role: "player" | "spectator";
  owner: boolean;
  can_stop: boolean;
  can_resume: boolean;
  created_at: string;
  updated_at: string;
  turn_sequence: number;
  active_player: string;
  phase: string;
  step: string;
  pending_principals: string[];
  game_over: boolean;
  winner: string | null;
  draw: boolean;
  pause_reason: { kind?: string; label?: string } | null;
  commands: number;
  decisions: number;
  events: number;
  persistence: {
    pending: boolean;
    pending_seconds: number | null;
    last_total_seconds: number | null;
    last_authoritative_seconds: number | null;
    last_derived_review_seconds: number | null;
  };
}

export interface PublicGameEvent {
  id: number;
  code: string;
  actor: string | null;
  summary: string;
  importance: number;
}

export interface PublicEventPage {
  events: PublicGameEvent[];
  next_after: number;
  has_more: boolean;
}

export interface SystemStatus {
  server: string;
  protocol: string;
  build?: string;
  card_data: {
    ready: boolean;
    phase: string;
    detail: string;
    updating: boolean;
    automatic_updates: boolean;
    update_interval_hours: number;
    restart_required: boolean;
    last_checked_at: string | null;
    next_check_at: string | null;
    last_error: string | null;
    database: {
      cards: number;
      rulings: number;
      image_references: number;
      retained_game_snapshots: number;
      oracle_updated_at: string | null;
      rulings_updated_at: string | null;
    };
  };
  images: {
    mode: string;
    downloaded: number;
    ready: boolean;
  };
  browser: { served_by_server: boolean };
}

export type DeckSubmission = {
  deck: { deck_id: string; name: string; commanders: string[] };
  preflight: DeckPreflight;
  format_legality: { status: string; issues: LegalityIssue[] };
};

export const api = {
  system: () => request<SystemStatus>("/api/v1/system"),
  refreshSystem: () =>
    request<{ accepted: boolean; card_data: SystemStatus["card_data"] }>(
      "/api/v1/system/refresh",
      { method: "POST", body: JSON.stringify({}) },
    ),
  me: () => request<{ guest: Guest }>("/api/v1/me"),
  savedDecks: () => request<{ decks: SavedDeck[] }>("/api/v1/me/decks"),
  savedDeck: (deckId: string) => request<{
    deck: { name: string; commanders: string[]; entries: {
      name: string; quantity: number; board: string;
      printing?: { set_code?: string; collector_number?: string; finish?: string };
    }[] };
  }>(`/api/v1/me/decks/${deckId}`),
  recentGames: () => request<{ games: RecentGame[] }>("/api/v1/me/games"),
  guest: (display_name: string) =>
    request<{ guest: Guest }>("/api/v1/guests", {
      method: "POST",
      body: JSON.stringify({ display_name }),
    }),
  createRoom: (player_count: 2 | 4 = 4) =>
    request<{ room: Room; invite_code: string }>("/api/v1/rooms", {
      method: "POST",
      body: JSON.stringify({ player_count }),
    }),
  joinRoom: (invite_code: string, seat: string) =>
    request<{ room: Room }>("/api/v1/rooms/join", {
      method: "POST",
      body: JSON.stringify({ invite_code, seat }),
    }),
  watchRoom: (invite_code: string) =>
    request<{ room: Room }>("/api/v1/rooms/watch", {
      method: "POST",
      body: JSON.stringify({ invite_code }),
    }),
  room: (roomId: string) => request<{ room: Room }>(`/api/v1/rooms/${roomId}`),
  rotateInvite: (roomId: string) =>
    request<{ invite_code: string }>(`/api/v1/rooms/${roomId}/invite`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  replaceRoom: (roomId: string, player_count: 2 | 4) =>
    request<{ room: Room; invite_code: string }>(`/api/v1/rooms/${roomId}/replace`, {
      method: "POST",
      body: JSON.stringify({ player_count }),
    }),
  removeSeat: (roomId: string, seat: string) =>
    request<{ room: Room }>(`/api/v1/rooms/${roomId}/seats/${seat}`, {
      method: "DELETE",
    }),
  leaveRoom: (roomId: string) =>
    request<{ left: boolean }>(`/api/v1/rooms/${roomId}/membership`, {
      method: "DELETE",
    }),
  deck: (
    roomId: string,
    name: string,
    commander: string,
    decklist: string,
    source_url?: string,
    legality_confirmation?: string,
    saved_deck_id?: string,
  ) =>
    request<DeckSubmission>(
      `/api/v1/rooms/${roomId}/deck`,
      {
        method: "PUT",
        body: JSON.stringify({
          name,
          commander,
          decklist,
          source_url: source_url || null,
          legality_confirmation: legality_confirmation || null,
          saved_deck_id: saved_deck_id || null,
        }),
      },
    ),
  clearDeck: (roomId: string) =>
    request<{ room: Room }>(`/api/v1/rooms/${roomId}/deck`, {
      method: "DELETE",
    }),
  start: (roomId: string, useReviewedCardData = false) =>
    request<{ game_id: string }>(`/api/v1/rooms/${roomId}/start`, {
      method: "POST",
      body: JSON.stringify(useReviewedCardData ? { use_reviewed_card_data: true } : {}),
    }),
  game: (gameId: string) =>
    request<{ game: GameLifecycle }>(`/api/v1/games/${gameId}`),
  rematch: (gameId: string) =>
    request<{ room: Room; invite_code: string }>(`/api/v1/games/${gameId}/rematch`, {
      method: "POST", body: JSON.stringify({}),
    }),
  events: (gameId: string, after = 0, limit = 100) =>
    request<PublicEventPage>(
      `/api/v1/games/${gameId}/events?after=${after}&limit=${limit}`,
    ),
  stop: (gameId: string, reason: string) =>
    request<{ game: GameLifecycle }>(`/api/v1/games/${gameId}/stop`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resume: (gameId: string) =>
    request<{ game: GameLifecycle }>(`/api/v1/games/${gameId}/resume`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  command: (gameId: string, envelope: CommandEnvelope) =>
    request<{ receipt: CommandReceipt }>(`/api/v1/games/${gameId}/commands`, {
      method: "POST",
      body: JSON.stringify(envelope),
    }),
};

export function streamUrl(gameId: string): string {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/api/v1/games/${gameId}/stream`;
}

export function streamProtocols(): string[] {
  return [`commander.tab.${browserTabId()}`];
}
