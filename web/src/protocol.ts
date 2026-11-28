import type {
  Decision,
  DecisionPacket,
  JsonValue,
  PatchOperation,
} from "./generated/protocol";

function pointerTokens(path: string): string[] {
  if (path === "") return [];
  if (!path.startsWith("/")) throw new Error(`Invalid JSON Pointer: ${path}`);
  return path
    .slice(1)
    .split("/")
    .map((token) => token.replaceAll("~1", "/").replaceAll("~0", "~"));
}

export function applyPatch(document: JsonValue, operations: PatchOperation[]): JsonValue {
  let result = structuredClone(document);
  for (const operation of operations) {
    const tokens = pointerTokens(operation.path);
    if (tokens.length === 0) {
      if (operation.op === "remove") result = null;
      else result = structuredClone(operation.value ?? null);
      continue;
    }
    let parent: JsonValue = result;
    for (const token of tokens.slice(0, -1)) {
      if (Array.isArray(parent)) parent = parent[Number(token)];
      else if (parent && typeof parent === "object") parent = parent[token];
      else throw new Error(`Cannot traverse ${operation.path}`);
    }
    const token = tokens.at(-1)!;
    if (Array.isArray(parent)) {
      const index = token === "-" ? parent.length : Number(token);
      if (operation.op === "remove") parent.splice(index, 1);
      else if (operation.op === "add") parent.splice(index, 0, structuredClone(operation.value ?? null));
      else parent[index] = structuredClone(operation.value ?? null);
    } else if (parent && typeof parent === "object") {
      if (operation.op === "remove") delete parent[token];
      else parent[token] = structuredClone(operation.value ?? null);
    } else {
      throw new Error(`Patch parent is not a container: ${operation.path}`);
    }
  }
  return result;
}

function canonical(value: JsonValue): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
    .join(",")}}`;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export interface ProjectedView {
  principal: string;
  state: Record<string, JsonValue>;
  viewHash: string;
  viewRevision: number;
  packetNumber: number;
  decision: Decision | null;
  definitions: Record<string, Record<string, JsonValue>>;
  events: Record<string, JsonValue>[];
}

type ProjectionPrincipal = Pick<ProjectedView, "principal" | "state">;

export function canFollowProjection(
  current: ProjectionPrincipal | null,
  next: { principal: string; state?: ProjectionPrincipal["state"] },
): boolean {
  if (!current || current.principal === next.principal) return true;
  if (next.principal !== "spectator" || !current.principal.startsWith("pilot:")) return false;
  const seat = current.principal.slice("pilot:".length);
  const players = next.state?.players;
  if (!players || typeof players !== "object" || Array.isArray(players)) return false;
  const player = players[seat];
  return !!player && typeof player === "object" && !Array.isArray(player) && player.in === 0;
}

export async function ingestPacket(
  current: ProjectedView | null,
  packet: DecisionPacket,
): Promise<ProjectedView> {
  if (packet.v !== "3.0") throw new Error(`Unsupported protocol ${packet.v}`);
  if (!canFollowProjection(current, packet.mode === "full" ? packet : { principal: packet.principal })) {
    throw new Error("Projection belongs to another principal");
  }
  if (packet.pkt <= 0) throw new Error("Projection packet number must be positive");
  let state: Record<string, JsonValue>;
  if (packet.mode === "full") {
    if (!packet.state) throw new Error("Full projection is missing state");
    state = structuredClone(packet.state);
  } else {
    if (current && packet.pkt <= current.packetNumber) {
      throw new Error("Projection packet is stale or duplicated");
    }
    if (!current) throw new Error("Delta arrived before a full projection");
    if (packet.base !== current.viewHash) throw new Error("Delta base mismatch");
    state = applyPatch(current.state, packet.patch ?? []) as Record<string, JsonValue>;
  }
  const actualHash = (await sha256(canonical(state))).slice(0, 20);
  if (actualHash !== packet.view) throw new Error("Projected-state hash mismatch");
  const definitions = { ...(current?.principal === packet.principal ? current.definitions : {}) };
  for (const definition of packet.defs ?? []) {
    const id = String(definition.cid ?? "");
    if (id) definitions[id] = definition;
  }
  return {
    principal: packet.principal,
    state,
    viewHash: packet.view,
    viewRevision: packet.view_revision,
    packetNumber: packet.pkt,
    decision: packet.decision,
    definitions,
    // A reconnect receives a full packet from a fresh per-connection cursor,
    // so its packet number restarts and its event tail replaces the previous
    // stream's baseline. Deltas continue to append in delivery order.
    events: (packet.mode === "full"
      ? [...(packet.events ?? [])]
      : [...(current?.events ?? []), ...(packet.events ?? [])]
    ).slice(-64),
  };
}
