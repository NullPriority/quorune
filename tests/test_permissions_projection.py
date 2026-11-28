from __future__ import annotations

import json
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from quorune import (
    CommandEnvelope,
    CommanderSession,
    GameService,
    PROTOCOL_VERSION,
    ProjectedClientView,
    ProtocolError,
)
from common import keep_all, load_assets, make_session
from quorune.model import CardInstance, Event
from quorune.projection import StateProjector


class ProjectionCollectionTests(unittest.TestCase):
    def test_visible_definition_scan_resolves_each_prefix_once_in_registry_order(self):
        class CardRegistry(dict):
            scans = 0

            def values(self):
                self.scans += 1
                return super().values()

        oracles = ("00000000-hidden", "aaaaaaaa-first", "aaaaaaaa-later", "bbbbbbbb-selected")
        cards = CardRegistry({oid: SimpleNamespace(oracle_id=oid) for oid in oracles})
        projector = StateProjector(None, SimpleNamespace(cards=cards))
        snapshot = {"rows": [{"cid": "aaaaaaaa"}] * 800}
        previews = [{"cid": "bbbbbbbb-selected"}, {"n": "?"}]
        self.assertEqual(
            {"aaaaaaaa-first", "bbbbbbbb-selected"},
            projector._visible_oracles(snapshot, previews),
        )
        self.assertEqual(1, cards.scans)


    def test_zone_activity_uses_public_causes_and_conceals_hidden_combat_sources(self):
        def card(ref, name, zone, *, face_down=False):
            return CardInstance(ref, ref, f"{len(ref):08d}-{ref}", name, "A", "A", zone,
                                face_down=face_down)

        source = card("A01", "Public attacker", "outside")
        source.known_to = ["A", "B"]
        other_source = card("A002", "Hidden blocker", "battlefield", face_down=True)
        victim = card("A0003", "Defending creature", "graveyard")
        def event(number, code, details, summary="fixture"):
            return Event(number, 5, 2, "A", "combat", "combat_damage", None,
                         code, summary, details, importance=2)

        hits = event(1, "combat.damage", {"damage_events": [
            {"source": source.ref, "target": victim.ref, "amount": 3},
            {"source": other_source.ref, "target": victim.ref, "amount": 1},
        ]})
        death = event(2, "permanent.destroyed", {
            "object": victim.ref, "destination": "graveyard", "cause": "state_based_action",
        })
        zone = event(3, "state.commander_zone", {
            "commander": victim.ref, "origin": "graveyard", "choice": "command",
        })
        private = event(4, "zone.move.private", {"object": other_source.ref}, "Hidden blocker")
        private.visibility = ["A", "analyst"]
        departing = card("A005", "Previously named card", "outside")
        departing.annotations["hidden_after_owner_left"] = True
        move = event(5, "zone.move", {"object": departing.ref, "from": "battlefield", "to": "graveyard"},
                     f"{departing.ref} {departing.printed_name}: battlefield \u2192 graveyard.")
        state = SimpleNamespace(
            players={"A": None, "B": None}, events=[hits, death, zone, private, move], event_sequence=5,
            pending_decision=None, capabilities={}, stack=[],
            cards={card.ref: card for card in (source, other_source, victim, departing)},
        )
        projector = StateProjector(None, state)
        public = projector.event_page("spectator")["events"]
        self.assertEqual([1, 2, 3, 5], [entry["id"] for entry in public])
        self.assertIn("Defending creature was destroyed after taking combat damage", public[1]["summary"])
        self.assertIn("3 from Public attacker", public[1]["summary"])
        self.assertIn("the graveyard", public[1]["summary"])
        self.assertEqual(["Defending creature", "Public attacker"], [row["n"] for row in public[1]["cards"]])
        self.assertNotIn("Hidden blocker", json.dumps(public))
        self.assertEqual("Defending creature moved from the graveyard to the command zone.", public[2]["summary"])
        self.assertEqual("Previously named card", public[3]["cards"][0]["n"])


class PermissionProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_wrong_principal_cannot_use_capability(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=21)
        cap = session.engine.permissions.capability_for("pilot:A")
        result = session.engine.try_submit(token=cap.token, principal="pilot:B", action="keep", payload={})
        self.assertFalse(result.ok)
        self.assertIn("not issued", result.summary)
        self.assertFalse(cap.consumed)

    def test_pilot_cannot_submit_arbiter_or_state_mutation_action(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=22)
        result = session.act("pilot:A", {"a": "resolve", "effects": [{"op": "draw"}]})
        self.assertFalse(result.ok)
        self.assertIn("outside capability scope", result.summary)

    def test_private_hands_are_seat_projected(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=23)
        packet = session.packet("pilot:A", full=True)
        self.assertIn("hand", packet["state"]["players"]["A"])
        self.assertNotIn("hand", packet["state"]["players"]["B"])
        self.assertEqual(7, packet["state"]["players"]["B"]["hand_n"])
        self.assertNotIn("known_top", packet["state"]["players"]["A"])
        b_names = {session.state.cards[oid].printed_name for oid in session.state.players["B"].zones["hand"]}
        a_names = {session.state.cards[oid].printed_name for oid in session.state.players["A"].zones["hand"]}
        unique = b_names - a_names
        if unique:
            serialized = json.dumps(packet)
            self.assertTrue(all(name not in serialized for name in unique))

    def test_commander_damage_is_public_and_names_its_source(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=231)
        commander = next(
            card
            for card in session.state.cards.values()
            if card.owner == "A" and card.is_commander
        )
        session.state.players["B"].commander_damage_received[
            commander.commander_designation_id
        ] = 18

        for principal in ("pilot:A", "pilot:B", "spectator"):
            packet = session.packet(principal, full=True)
            damage = packet["state"]["players"]["B"]["cmd_dmg"]
            self.assertEqual(
                [
                    {
                        "cid": commander.commander_designation_id,
                        "n": commander.printed_name,
                        "owner": "A",
                        "amount": 18,
                    }
                ],
                damage,
            )

    def test_public_battlefield_stats_include_counters_and_report_damage_separately(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=232)
        commander = next(
            card for card in session.state.cards.values()
            if card.owner == "A" and card.is_commander
        )
        session.engine._remove_from_zone(commander)
        session.engine._reset_zone_change(commander, "battlefield")
        commander.zone = "battlefield"
        session.state.players["A"].zones["battlefield"].append(commander.object_id)
        commander.counters["+1/+1"] = 1
        commander.marked_damage = 2

        for principal in ("pilot:A", "pilot:B", "spectator"):
            with self.subTest(principal=principal):
                packet = session.packet(principal, full=True)
                projected, = packet["state"]["players"]["A"]["bf"]
                self.assertEqual(commander.ref, projected["id"])
                self.assertEqual((6, 5), (projected["p"], projected["q"]))
                self.assertEqual(2, projected["dmg"])

    def test_public_spell_on_stack_includes_visible_card_definition(self):
        from quorune.model import StackItem

        session = make_session(self.db, self.mishra, self.zimone, seed=230)
        object_id = session.state.players["A"].zones["hand"][0]
        card = session.state.cards[object_id]
        session.engine._remove_from_zone(card)
        session.engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        session.state.stack.append(
            StackItem(
                uuid.uuid4().hex,
                "S-visible-card",
                "spell",
                "A",
                card.printed_name,
                card_object_id=object_id,
                visibility=list("ABCD"),
            )
        )

        packet = session.packet("pilot:B", full=True)
        projected = packet["state"]["stack"][0]
        self.assertEqual(card.printed_name, projected["n"])
        self.assertEqual(card.oracle_id[:8], projected["cid"])
        self.assertIn(projected["cid"], {row["cid"] for row in packet["defs"]})

    def test_live_capability_is_repeated_in_delta_packets(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=24)
        first = session.packet("pilot:A", full=True)
        second = session.packet("pilot:A")
        self.assertEqual(first["decision"]["cap"], second["decision"]["cap"])
        self.assertEqual("delta", second["mode"])

    def test_delta_packet_is_smaller_than_bootstrap(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=25)
        full = session.packet("pilot:A", full=True)
        delta = session.packet("pilot:A")
        full_size = session.projector.measure(full)["compact_chars"]
        delta_size = session.projector.measure(delta)["compact_chars"]
        self.assertLess(delta_size, full_size * 0.55)

    def test_rules_lookup_accepts_object_refs(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=26)
        ref = session.state.cards[session.state.players["A"].zones["hand"][0]].ref
        digest = session.rules([ref], max_rulings_per_card=1)
        self.assertIn("### ", digest)
        self.assertIn(session.state.cards[session.state.players["A"].zones["hand"][0]].printed_name, digest)

    def test_service_checks_game_and_capability(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=27)
        service = GameService(session)
        cap = session.engine.permissions.capability_for("pilot:A")
        decision = session.state.pending_decision
        bad = service.command(
            CommandEnvelope(
                PROTOCOL_VERSION,
                "wrong",
                "wrong-game-1",
                decision.decision_id,
                "keep",
                cap.token,
                session.state.revision,
                {},
            ),
            principal="pilot:A",
        )
        self.assertFalse(bad.ok)
        good = service.command(
            CommandEnvelope(
                PROTOCOL_VERSION,
                session.state.game_id,
                "valid-1",
                decision.decision_id,
                "keep",
                cap.token,
                session.state.revision,
                {},
            ),
            principal="pilot:A",
        )
        self.assertTrue(good.ok)

    def test_save_and_load_preserves_cursor_and_state(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=28)
        session.packet("pilot:A", full=True)
        with tempfile.TemporaryDirectory() as tmp:
            session.save(tmp)
            loaded = CommanderSession.load(self.db, tmp)
            self.assertEqual(session.state.game_id, loaded.state.game_id)
            self.assertEqual(1, loaded.cursors["pilot:A"].packet_no)
            self.assertEqual(session.cursors["pilot:A"].view_hash, loaded.cursors["pilot:A"].view_hash)
            self.assertEqual(session.state.event_sequence, loaded.state.event_sequence)

    def test_client_reducer_reconstructs_delta_and_rejects_wrong_base(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=281)
        client = ProjectedClientView("pilot:A")
        full = session.packet("pilot:A", full=True)
        first_state = client.ingest(full)
        self.assertEqual(7, first_state["players"]["A"]["hand_n"])

        # A simultaneous keep changes decision metadata and event history but
        # leaves the projected rules state hash-valid.
        self.assertTrue(session.act("pilot:A", {"a": "keep"}).ok)
        delta = session.packet("pilot:A")
        reconstructed = client.ingest(delta)
        self.assertEqual(delta["view"], client.current_hash)
        self.assertEqual(session.projector._snapshot("pilot:A"), reconstructed)

        bad = dict(delta)
        bad["pkt"] = delta["pkt"] + 1
        bad["base"] = "not-the-current-hash"
        with self.assertRaises(ProtocolError):
            client.ingest(bad)

    def test_client_full_resync_starts_a_new_packet_stream(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=282)
        client = ProjectedClientView("pilot:A")
        first_cursor = "network:pilot:A:first"
        reconnect_cursor = "network:pilot:A:reconnect"

        first = session.packet(
            "pilot:A", full=True, cursor_key=first_cursor
        )
        client.ingest(first)
        self.assertTrue(session.act("pilot:A", {"a": "keep"}).ok)
        delta = session.packet("pilot:A", cursor_key=first_cursor)
        client.ingest(delta)
        self.assertEqual(2, client.packet_no)

        reconnect = session.packet(
            "pilot:A", full=True, cursor_key=reconnect_cursor
        )
        self.assertEqual(1, reconnect["pkt"])
        expected_events = list(reconnect.get("events") or [])[-64:]
        state = client.ingest(reconnect)

        self.assertEqual(1, client.packet_no)
        self.assertEqual(reconnect["view"], client.current_hash)
        self.assertEqual(reconnect["state"], state)
        self.assertEqual(expected_events, client.recent_events)

        with self.assertRaisesRegex(ProtocolError, "stale or duplicated"):
            client.ingest(reconnect | {"mode": "delta", "patch": []})


if __name__ == "__main__":
    unittest.main()

# Additional least-privilege and semantic-cache tests are defined separately so
# unittest discovery still sees them when this module is imported.
class ArbiterBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_arbiter_projection_does_not_receive_private_hands(self):
        from quorune.model import StackItem
        import uuid
        session = make_session(self.db, self.mishra, self.zimone, seed=290)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine.state.stack.append(StackItem(uuid.uuid4().hex, "S-unknown", "triggered_ability", "A", "Unknown test ability", semantic_key="unknown:test", visibility=list("ABCD")))
        engine._prepare_stack_resolution()
        packet = session.packet("arbiter", full=True)
        self.assertEqual("arbiter.resolve", packet["decision"]["kind"])
        for seat in "ABCD":
            self.assertNotIn("hand", packet["state"]["players"][seat])
            self.assertEqual(7, packet["state"]["players"][seat]["hand_n"])

    def test_arbiter_does_not_learn_face_down_public_object_identity(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=292)
        engine = session.engine
        object_id = session.state.players["A"].zones["hand"][0]
        engine.move_card(object_id, "battlefield", log=False)
        session.state.cards[object_id].face_down = True
        packet = session.packet("arbiter", full=True)
        projected = packet["state"]["players"]["A"]["bf"]
        face_down = next(obj for obj in projected if obj["id"] == session.state.cards[object_id].ref)
        self.assertEqual("?", face_down["n"])
        self.assertNotIn("cid", face_down)

    def test_cached_semantics_resolve_runtime_target_placeholders(self):
        from quorune.model import StackItem
        from quorune.semantics import SemanticProgram
        import uuid
        session = make_session(self.db, self.mishra, self.zimone, seed=291)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine.semantics.put(SemanticProgram(key="test:bolt", label="Test bolt", effects=[{"op": "damage", "target": "$target.0", "amount": 3}]))
        engine.state.stack.append(StackItem(uuid.uuid4().hex, "S-bolt", "triggered_ability", "A", "Test bolt", semantic_key="test:bolt", targets=["B"], visibility=list("ABCD")))
        engine._prepare_stack_resolution()
        self.assertEqual(37, engine.state.players["B"].life)
        self.assertFalse(engine.state.stack)
        self.assertEqual("A", engine.state.priority_player)
