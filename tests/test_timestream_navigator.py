"""Original paid Navigator activation, blessing, and extra-turn replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activated_ability_descriptor import SOURCE_LIBRARY_BOTTOM_CAPABILITY
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.activation_zone_change_costs import activation_zone_change_cost_reference
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


NAME = "Timestream Navigator"


class TimestreamNavigatorTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "navigator.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "bound-effect-program-cards.json", fixtures / "timestream-navigator-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Navigator witness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_navigator_exact_cost_shape_and_authority_fail_closed(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        activation, = (node for node in result.faces[0].nodes if node.event == "activate")
        self.assertIn(SOURCE_LIBRARY_BOTTOM_CAPABILITY, activation.capability_dependencies)
        self.assertIn("activation.condition.citys_blessing", activation.capability_dependencies)
        self.assertIn("turn.extra.fixed_after_current", activation.capability_dependencies)
        for new in ("top of its owner's library", "bottom of your library"):
            changed = replace(card, oracle_text=card.oracle_text.replace("bottom of its owner's library", new))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == SOURCE_LIBRARY_BOTTOM_CAPABILITY)
        row.update(status="blocked", blockers=["Withhold exact source-placement authority"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry).status)

        response = {"source": "S", "from": "battlefield",
                    "cost_summary": {"bottom_library_self": 1, "m": {"GENERIC": 2, "U": 2}, "tap": 1}}
        reference = activation_zone_change_cost_reference
        self.assertEqual("S", reference(response, origin="battlefield", destination="library", object_ref="S"))
        for destination, object_ref in (("hand", "S"), ("library", "different-incarnation")):
            self.assertIsNone(reference(response, origin="battlefield", destination=destination, object_ref=object_ref))
        for invalid in (True, 2, "1"):
            response["cost_summary"]["bottom_library_self"] = invalid
            self.assertIsNone(reference(response, origin="battlefield", destination="library", object_ref="S"))

    def _setup(self, seed, mana, *, ready):
        session = self.session(seed)
        engine = session.engine
        source = self.add(engine, NAME, seat="B", ref="actual-navigator")
        engine.change_control(source.object_id, "A")
        if ready:
            source.acquired_control_turn_count = -1
        for index in range(8):
            self.add(engine, "Generic Bound Plains", ref=f"blessing-land-{index}").tapped = True
        tenth = self.add(engine, "Generic Bound Body", zone="hand", ref="tenth-permanent")
        self.assert_closed(session, source)
        self.prepare(session, mana=mana)
        self.checkpoint(session)
        return session, source, tenth

    def _offer(self, session, source):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"].startswith(f"activate:{source.ref}:")), None)

    def _tenth(self, session, source, tenth):
        self.assertFalse(session.state.players["A"].stats.get("citys_blessing", False))
        self.assertIsNone(self._offer(session, source))
        self.cast(session, "A", tenth)
        self.resolve_top(session)
        self.assertTrue(session.state.players["A"].stats["citys_blessing"])
        self.assertFalse(session.state.players["B"].stats.get("citys_blessing", False))

    def test_actual_navigator_pays_to_owner_bottom_then_controller_takes_distinct_extra_turn(self):
        session, source, tenth = self._setup(414_702, {"U": 4, "W": 1}, ready=True)
        original_turn = session.state.current_turn.turn_id
        original_object = source.logical_object_id
        self._tenth(session, source, tenth)
        action = self._offer(session, source)
        self.assertIsNotNone(action)
        self.assertEqual(1, action["cost_summary"]["bottom_library_self"])
        self.assertEqual({"GENERIC": 2, "U": 2}, action["cost_summary"]["m"])
        owner_library = list(session.state.players["B"].zones["library"])
        self.assertGreater(len(owner_library), 1)
        controller_library = list(session.state.players["A"].zones["library"])
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        activated = session.act("pilot:A", command)
        self.assertTrue(activated.ok, activated.summary)
        moved = session.state.cards[source.object_id]
        self.assertEqual("library", moved.zone)
        self.assertNotEqual(original_object, moved.logical_object_id)
        self.assertEqual([source.object_id] + owner_library, session.state.players["B"].zones["library"])
        self.assertEqual(controller_library, session.state.players["A"].zones["library"])
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertTrue(session.state.players["A"].stats["citys_blessing"])
        self.assertFalse(session.state.extra_turns)
        for viewer in "ACD":
            self.assertNotIn(moved.logical_object_id, json.dumps(session.packet("pilot:" + viewer, full=True)))
        session = self.replay(session, load=True)
        self.resolve_top(session)
        turn, = session.state.extra_turns
        self.assertEqual("A", turn.player)
        self.assertNotEqual(original_turn, turn.turn_id)
        self.until(session, lambda game: game.state.current_turn.turn_id == turn.turn_id)
        self.assertEqual(("A", True), (session.state.active_player, session.state.current_turn.extra))
        self.until(session, lambda game: game.state.active_player == "B"
                   or game.state.pending_decision is not None and game.state.pending_decision.kind == "cleanup.discard")
        if session.state.active_player != "B":
            principal = session.pending_principals()[0]
            context = session.packet(principal, full=True)["decision"]["ctx"]
            cards = [card["id"] for card in context["hand"][:context["count"]]]
            discarded = session.act(principal, {"a": "discard", "cards": cards})
            self.assertTrue(discarded.ok, discarded.summary)
            self.until(session, lambda game: game.state.active_player == "B")
        self.assertFalse(session.state.current_turn.extra)
        self.assertNotIn(session.state.current_turn.turn_id, {original_turn, turn.turn_id})
        self.replay(session, load=True)

    def test_actual_navigator_sickness_and_short_mana_do_not_pay_or_schedule(self):
        for seed, mana, ready in ((414_703, {"U": 4, "W": 1}, False), (414_704, {"U": 1, "W": 1}, True)):
            with self.subTest(ready=ready):
                session, source, tenth = self._setup(seed, mana, ready=ready)
                self._tenth(session, source, tenth)
                self.assertIsNone(self._offer(session, source))
                ability, = session.engine._activated_abilities(session.state.cards[source.object_id])
                command = {"action_id": f"activate:{source.ref}:{ability.ability_id}", "pay": "auto"}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.assertFalse(session.state.cards[source.object_id].tapped)
                self.assertFalse(session.state.extra_turns)
                self.replay(session, load=True)
