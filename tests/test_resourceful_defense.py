"""Actual departure and movement of multiple public counter kinds."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_counter_movement_model import CHOSEN_COUNTER_QUANTITIES_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.departure_counter_transfer import DEPARTURE_COUNTER_TRANSFER_CAPABILITY
from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement_fixture


PROFILE = "commander_review"


class ResourcefulDefenseTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject
    activate = movement_fixture.FixedOneCounterMovementTests.activate
    lands = movement_fixture.FixedOneCounterMovementTests.lands

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "resourceful-defense.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/resourceful-defense-cards.json",
            ROOT / "tests/fixtures/fixed-one-counter-movement-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            ROOT / "tests/fixtures/attachment-source-return-cards.json",
            ROOT / "tests/fixtures/fixed-direct-phasing-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Resourceful Defense", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, actor="A", active="A", mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=actor, active=active, mana=mana)
        self.checkpoint(game)

    def counters(self, game, card):
        return game.state.cards[card.object_id].counters

    def amount(self, game, expected_kind, upper, count):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        visible = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(expected_kind, visible["prompt"])
        action, = visible["legal_actions"]
        schema = action["choice_schema"]
        self.assertEqual((0, upper), (schema["minimum"], schema["maximum"]))
        for other in "BCD":
            decision = game.packet("pilot:" + other, full=True)["decision"]
            self.assertIsNone(decision)
        command = {"action_id": "choose", "amount": count}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "amount": upper + 1})
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_printed_counter_lines_require_original_cost_event_and_roles(self):
        card = self.db.lookup("Resourceful Defense", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        movement, = (row for row in programs if CHOSEN_COUNTER_QUANTITIES_CAPABILITY in row.capability_dependencies)
        departure, = (row for row in programs if DEPARTURE_COUNTER_TRANSFER_CAPABILITY in row.capability_dependencies)
        ability, = activated_abilities_from_descriptors(movement.handlers)
        self.assertFalse(ability.tap_source)
        self.assertFalse(ability.sorcery_speed)
        self.assertEqual({"GENERIC": 4, "W": 1}, {key: n for key, n in ability.mana.items() if n})
        self.assertEqual("you", departure.target_schema["controller_relation"])
        self.assertTrue(movement.target_schema["globally_distinct"])
        self.assertEqual(["you", "you"], [row["controller_relation"] for row in movement.target_schema["groups"]])
        for program, cap in ((movement, CHOSEN_COUNTER_QUANTITIES_CAPABILITY),
                             (departure, DEPARTURE_COUNTER_TRANSFER_CAPABILITY)):
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            blocked = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(blocked, capability_registry=self.registry, profile=PROFILE)["strict"])
        for old, new in (("{4}{W}", "{4}"), ("if it had counters on it,", "if it had a charge counter on it,"),
                         ("onto a second target permanent you control", "onto a second target permanent")):
            with self.subTest(old=old):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

    def test_paid_movement_uses_distinct_controlled_objects_and_exact_choices(self):
        game = self.session(78545201)
        source = self.add(game.engine, "Resourceful Defense")
        donor = self.add(game.engine, "Grizzly Bears")
        self.counters(game, donor).update({"+1/+1": 3, "charge": 2, "quest": 1})
        recipient = self.add(game.engine, "Forest")
        opponent = self.add(game.engine, "Island", seat="B")
        self.start(game, source, active="B", mana={"C": 4, "W": 1})
        command = {"action_id": f"activate:{source.ref}:ab2", "pay": "auto"}
        self.reject(game, "A", {**command, "targets": [donor.ref, donor.ref]})
        self.reject(game, "A", {**command, "targets": [donor.ref, opponent.ref]})
        self.reject(game, "B", {**command, "targets": [donor.ref, recipient.ref]})
        self.activate(game, source, "ab2", [donor.ref, recipient.ref])
        self.assertEqual({}, {k: n for k, n in game.state.players["A"].mana_pool.items() if n})
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.amount(game, "+1/+1", 3, 2)
        self.assertEqual(({"+1/+1": 3, "charge": 2, "quest": 1}, {}),
                         (self.counters(game, donor), self.counters(game, recipient)))
        game = self.replay(game, load=True)
        self.amount(game, "charge", 2, 0)
        self.amount(game, "quest", 1, 1)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(({"+1/+1": 1, "charge": 2}, {"+1/+1": 2, "quest": 1}),
                         (self.counters(game, donor), self.counters(game, recipient)))
        self.replay(game, load=True)

    def test_movement_waits_for_replacement_and_preserves_the_zeroed_kind(self):
        game = self.session(78545202)
        source = self.add(game.engine, "Resourceful Defense")
        donor = self.add(game.engine, "Grizzly Bears")
        recipient = self.add(game.engine, "Grizzly Bears", ref="resourceful-other-creature")
        self.counters(game, donor).update({"+1/+1": 3, "charge": 1})
        scales = self.add(game.engine, "Hardened Scales")
        vorinclex = self.add(game.engine, "Vorinclex, Monstrous Raider", seat="B")
        self.start(game, source, scales, vorinclex, mana={"C": 4, "W": 1})
        self.activate(game, source, "ab2", [donor.ref, recipient.ref])
        self.amount(game, "+1/+1", 3, 3)
        self.amount(game, "charge", 1, 1)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(({"+1/+1": 3, "charge": 1}, {}), (self.counters(game, donor), self.counters(game, recipient)))
        game = self.replay(game, load=True)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        order, = (row["id"] for row in rows if row["source"] == scales.ref)
        command = {"action_id": "choose", "replacement": order}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "replacement": "forged-counter-replacement"})
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(({"charge": 1}, {"+1/+1": 2}), (self.counters(game, donor), self.counters(game, recipient)))
        self.replay(game, load=True)

    def test_departure_copies_the_old_incarnation_and_revalidates_the_recipient(self):
        self.witness_blinked_permanent()

    def witness_blinked_permanent(self, *, bounce_recipient=False):
        game = self.session(78545203 + bounce_recipient)
        source = self.add(game.engine, "Resourceful Defense", zone="hand")
        donor = self.add(game.engine, "Grizzly Bears")
        self.counters(game, donor).update({"+1/+1": 2, "charge": 3, "quest": 1, "flying": 1})
        recipient = self.add(game.engine, "Grizzly Bears", ref="retained-departure-recipient")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        private = self.add(game.engine, "Cloudshift", ref="resourceful-private-unrelated-card", seat="C", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand") if bounce_recipient else None
        self.add(game.engine, "Island", seat="B")
        self.lands(game, "Plains", 4)
        self.start(game, source, blink, *((bounce,) if bounce else ()))
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        old_identity = game.state.cards[donor.object_id].logical_object_id
        self.cast(game, "A", blink, [donor.ref])
        target = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (target.kind, target.actors))
        self.assertNotEqual(old_identity, game.state.cards[donor.object_id].logical_object_id)
        self.assertEqual({}, self.counters(game, donor))
        self.target(game, "A", [recipient.ref])
        if bounce:
            self.cast(game, "B", bounce, [recipient.ref])
            self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({} if bounce else {"+1/+1": 2, "charge": 3, "quest": 1, "flying": 1},
                         self.counters(game, recipient))
        self.assertEqual({}, self.counters(game, donor))
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_returned_or_removed_recipient_gets_no_counters_for_the_original_target(self):
        self.witness_blinked_permanent(bounce_recipient=True)

    def test_simultaneous_departure_uses_the_previous_controller_and_ignores_phasing(self):
        game = self.session(78545205)
        source = self.add(game.engine, "Resourceful Defense")
        controlled = self.add(game.engine, "Grizzly Bears", seat="B", ref="borrowed-counted-creature")
        game.engine.change_control(controlled.object_id, "A")
        self.counters(game, controlled)["charge"] = 2
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="opponent-counted-creature")
        self.counters(game, opponent)["quest"] = 1
        self.add(game.engine, "Grizzly Bears", ref="empty-controlled-creature")
        phased = self.add(game.engine, "Grizzly Bears", ref="phased-counted-creature")
        self.counters(game, phased)["charge"] = 3
        ripple = self.add(game.engine, "Reality Ripple", zone="hand")
        recipient = self.add(game.engine, "Plains")
        wipe = self.add(game.engine, "Planar Cleansing", zone="hand")
        self.lands(game, "Plains", 8)
        self.add(game.engine, "Island", ref="resourceful-ripple-island")
        self.start(game, source, ripple, wipe)
        self.cast(game, "A", ripple, [phased.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[phased.object_id].phased_out)
        self.cast(game, "A", wipe)
        self.target(game, "A", [recipient.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("B", game.state.cards[controlled.object_id].controller)
        self.assertEqual({"charge": 2}, self.counters(game, recipient))
        self.assertEqual({"charge": 3}, self.counters(game, phased))
        self.replay(game, load=True)

    def test_counter_quantity_witness_kills_a_fixed_one_amount_mutation(self):
        from quorune.semantic_choices import counter_movement_quantities as owner

        original = owner.MoveCounterSetIntent

        def fixed_amount(**kwargs):
            return original(**{**kwargs, "amounts": {name: 1 for name in kwargs["amounts"]}})

        with patch.object(owner, "MoveCounterSetIntent", side_effect=fixed_amount), self.assertRaisesRegex(AssertionError, "quest"):
            self.test_paid_movement_uses_distinct_controlled_objects_and_exact_choices()

    def test_departure_witness_kills_using_a_different_snapshot_amount(self):
        from quorune.semantic_choices import departure_counter_transfer as owner

        original = owner.departure_counter_snapshot

        def fixed_amount(counters):
            return {kind: 1 for kind in original(counters)}

        with patch.object(owner, "departure_counter_snapshot", side_effect=fixed_amount), self.assertRaisesRegex(AssertionError, "charge"):
            self.witness_blinked_permanent()
