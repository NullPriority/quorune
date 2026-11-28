import assert from "node:assert/strict";
import test from "node:test";

import { ingestPacket } from "../src/protocol.ts";

async function stateHash(state: Record<string, unknown>): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(state)),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 20);
}

test("a full projection starts a new per-connection packet stream", async () => {
  const initialState = { rev: 1 };
  const initial = await ingestPacket(null, {
    v: "3.0",
    mode: "full",
    principal: "pilot:A",
    base: null,
    view: await stateHash(initialState),
    state: initialState,
    view_revision: 1,
    pkt: 9,
    decision: null,
    events: [{ id: 1, c: "old" }],
  });

  const reconnectedState = { rev: 2 };
  const reconnected = await ingestPacket(initial, {
    v: "3.0",
    mode: "full",
    principal: "pilot:A",
    base: null,
    view: await stateHash(reconnectedState),
    state: reconnectedState,
    view_revision: 2,
    pkt: 1,
    decision: null,
    events: [{ id: 2, c: "reconnect" }],
  });

  assert.equal(reconnected.packetNumber, 1);
  assert.deepEqual(reconnected.state, reconnectedState);
  assert.deepEqual(reconnected.events, [{ id: 2, c: "reconnect" }]);

  await assert.rejects(
    ingestPacket(reconnected, {
      v: "3.0",
      mode: "delta",
      principal: "pilot:A",
      base: reconnected.viewHash,
      view: reconnected.viewHash,
      view_revision: 2,
      pkt: 1,
      decision: null,
      patch: [],
    }),
    /stale or duplicated/,
  );
});

test("an eliminated pilot follows a full public projection with no private retained data", async () => {
  const initialState = { players: { A: { in: 1 } }, rev: 1 };
  const initial = await ingestPacket(null, {
    v: "3.0", mode: "full", principal: "pilot:A", base: null,
    view: await stateHash(initialState), state: initialState, view_revision: 1, pkt: 4,
    decision: null, defs: [{ cid: "secret-definition" }], events: [{ id: 1, c: "private" }],
  });

  const publicState = { players: { A: { in: 0 } }, rev: 2 };
  const publicPacket = {
    v: "3.0", mode: "full", principal: "spectator", base: null,
    view: await stateHash(publicState), state: publicState, view_revision: 2, pkt: 5,
    decision: null, defs: [{ cid: "public-definition" }], events: [{ id: 2, c: "player.eliminated" }],
  } as const;
  const mutablePublicPacket = {
    ...publicPacket, defs: [...publicPacket.defs], events: [...publicPacket.events],
  };
  const observing = await ingestPacket(initial, mutablePublicPacket);
  assert.equal(observing.principal, "spectator");
  assert.deepEqual(Object.keys(observing.definitions), ["public-definition"]);
  assert.deepEqual(observing.events, [{ id: 2, c: "player.eliminated" }]);

  await assert.rejects(ingestPacket(initial, { ...mutablePublicPacket, mode: "delta" }), /another principal/);
  await assert.rejects(ingestPacket(initial, { ...mutablePublicPacket, state: initialState }), /another principal/);
  await assert.rejects(ingestPacket(observing, { ...mutablePublicPacket, principal: "pilot:A" }), /another principal/);
});
