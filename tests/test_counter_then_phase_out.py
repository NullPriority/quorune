"""The same-target counter placement and phasing sequence."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.phasing_model import PHASE_OUT_CAPABILITY, PHASE_OUT_OPERATION, PHASING_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.occurrence_object_results import COUNTER_THEN_PHASE_OUT_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Slip Out the Back"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class CounterThenPhaseOutTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "counter-then-phase-out.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "counter-then-phase-out-cards.json", "phasing-cards.json", "counter-prohibition-cards.json",
            "chosen-counter-removal-cards.json", "chosen-counter-movement-cards.json",
            "fixed-one-counter-movement-cards.json", "ability-kind-cost-modifier-cards.json",
            "activation-result-views-cards.json", "bound-effect-program-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Counter and phase out", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_binding_and_counter_placement_and_phase_owners_stay_independent(self):
        printed = self.db.lookup(CARD, fuzzy=False)
        record = replace(query_record(printed.oracle_text), type_line="Instant")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(printed.oracle_text)), (node.span.start, node.span.end))
        self.assertEqual(("place_counters", PHASE_OUT_OPERATION), tuple(effect["op"] for effect in node.effects))
        self.assertEqual(("$target.0", "$target.0"), tuple(effect["card"] for effect in node.effects))
        owners = {COUNTER_THEN_PHASE_OUT_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
                  "counter.producer.fixed_effect", "target.revalidate_resolution"}
        self.assertLessEqual(owners, set(node.capability_dependencies))

        forbidden = printed.oracle_text.replace("a +1/+1 counter", "a shield counter")
        result = compile_oracle_card(replace(record, oracle_text=forbidden),
                                     capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", result.status)
        for replacement in ("Another target creature phases out.", "It also creates a Treasure token."):
            with self.subTest(replacement=replacement):
                changed = replace(record, oracle_text=printed.oracle_text.replace("It phases out.", replacement))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                                capability_profile=PROFILE).status)
        result, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        bound, = semantics.programs_for_oracle(printed.oracle_id)
        for cap in sorted(owners):
            with self.subTest(cap=cap):
                rest = [key for key in bound.capability_dependencies if key != cap]
                changed = replace(bound, capability_dependencies=rest,
                                  capability_closure=self.registry.closure(rest, profile=PROFILE).to_dict())
                self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                               profile=PROFILE)["strict"])
        altered = replace(bound, effects=[bound.effects[0], {**bound.effects[1], "card": "$target.1"}])
        self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])

    def begin(self, game, source, creature):
        self.assert_closed(game, source)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source, [creature.ref])

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_paid_opponent_phases_with_foreign_equipment_and_returns_before_untap(self):
        game = self.session(70226011)
        source = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="phased-target")
        outsider = self.add(game.engine, "Grizzly Bears", seat="C", ref="unrelated-creature")
        equipment = self.add(game.engine, "Bonesplitter", seat="C")
        attach_objects(game.state.cards, equipment, target, source_timestamp=equipment.zone_timestamp,
                       players=game.state.players)
        target.tapped = True
        target.acquired_control_turn_count = -1
        original = target.logical_object_id, target.zone_timestamp
        private = self.add(game.engine, CARD, seat="D", zone="hand", ref="other-seat-private")
        self.begin(game, source, target)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        target = game.state.cards[target.object_id]
        equipment = game.state.cards[equipment.object_id]
        self.assertEqual("battlefield", target.zone)
        self.assertTrue(target.phased_out)
        self.assertEqual("B", target.phasing_record.return_seat)
        self.assertEqual({"+1/+1": 1}, target.counters)
        self.assertTrue(equipment.phased_out)
        self.assertEqual(original[0], equipment.phasing_record.attached_object.logical_object_id)
        self.assertFalse(game.state.cards[outsider.object_id].phased_out)
        self.assertEqual(original, (target.logical_object_id, target.zone_timestamp))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)

        self.until(game, lambda g: g.state.active_player == "B" and g.state.step == "upkeep")
        target = game.state.cards[target.object_id]
        equipment = game.state.cards[equipment.object_id]
        self.assertEqual(original, (target.logical_object_id, target.zone_timestamp))
        self.assertFalse(target.phased_out)
        self.assertFalse(target.tapped)
        self.assertFalse(equipment.phased_out)
        self.assertEqual(target.object_id, equipment.attached_to)
        self.assertEqual({"+1/+1": 1}, target.counters)
        self.replay(game, load=True)

    def test_prohibited_counter_still_phases_the_legal_creature(self):
        game = self.session(70226012)
        source = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        solemnity = self.add(game.engine, "Solemnity", seat="C")
        self.assert_closed(game, solemnity)
        self.begin(game, source, target)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[target.object_id].phased_out)
        self.assertEqual({}, game.state.cards[target.object_id].counters)
        self.replay(game, load=True)

    def test_affected_player_orders_counters_before_phasing_on_replay(self):
        game = self.session(70226013)
        source = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        scales = self.add(game.engine, "Hardened Scales", seat="B")
        doubling = self.add(game.engine, "Doubling Season", seat="B")
        for card in (scales, doubling):
            self.assert_closed(game, card)
        self.begin(game, source, target)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertFalse(game.state.cards[target.object_id].phased_out)
        self.assertEqual({}, game.state.cards[target.object_id].counters)
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        candidate, = [row["id"] for row in options if row["source"] == scales.ref]
        command = {"action_id": "choose", "replacement": candidate}
        self.reject(game, "C", command)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[target.object_id].phased_out)
        self.assertEqual({"+1/+1": 4}, game.state.cards[target.object_id].counters)
        self.replay(game, load=True)

    def test_target_blink_cancels_both_instructions_for_the_new_incarnation(self):
        game = self.session(70226014)
        source = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        blink = self.add(game.engine, "Ephemerate", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.assert_closed(game, blink)
        original = target.logical_object_id
        self.begin(game, source, target)
        self.cast(game, "B", blink, [target.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[target.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", False, {}), (current.zone, current.phased_out, current.counters))
        self.replay(game, load=True)
