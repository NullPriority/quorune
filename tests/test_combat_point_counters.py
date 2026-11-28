"""Native point-counter mana and both combat directions of the original Stadium."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from attachment_damage_fixture import AttachmentDamageFixture
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_mana_results import FIXED_MANA_POINT_COUNTER_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.combat_point_counters import (
    COMBAT_POINT_COUNTER_CAPABILITY, POINT_COUNTER_OBSERVATION, point_combat_condition,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


STADIUM = "Strixhaven Stadium"
PROFILE = "commander_review"


class CombatPointCounterTests(InheritedNativeFixture, unittest.TestCase):
    attack_for = AttachmentDamageFixture.attack_for

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "combat-point-counter.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "event-damage-source-cards.json",
            fixtures / "all-kind-counter-cards.json",
            fixtures / "combat-point-counter-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Stadium point counters", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_complete_original_requires_tapped_mana_and_two_exact_individual_damage_directions(self):
        card = self.db.lookup(STADIUM, fuzzy=False)
        args = dict(capability_registry=self.registry, capability_profile=PROFILE)
        ir = compile_oracle_card(card, **args)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        admitted, semantics = register_card(self.db, self.registry, STADIUM)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        all_programs = semantics.programs_for_oracle(card.oracle_id)
        mana, = (p for p in all_programs if FIXED_MANA_POINT_COUNTER_CAPABILITY in p.capability_dependencies)
        incoming, = (p for p in all_programs if p.event == "damage.dealt" and p.effects[0]["op"] == "remove_counters")
        outgoing, = (p for p in all_programs if p.event == "damage.dealt" and p.effects[0]["op"] == "place_counters")
        for program, direction in ((incoming, False), (outgoing, True)):
            self.assertEqual(point_combat_condition(outbound=direction), program.event_condition)
            self.assertNotIn("one_or_more_event_batch", program.coverage)
        self.assertIn(COMBAT_POINT_COUNTER_CAPABILITY, outgoing.capability_dependencies)
        deps = tuple(dependency for dependency in mana.capability_dependencies
                     if dependency != FIXED_MANA_POINT_COUNTER_CAPABILITY)
        for changed in (
            replace(mana, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
            replace(incoming, event_condition=outgoing.event_condition),
            replace(outgoing, coverage=(*outgoing.coverage, "one_or_more_event_batch")),
            replace(outgoing, effects=tuple(reversed(outgoing.effects))),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for before, after in (("ten or more", "eleven or more"), ("to an opponent", "to a creature"),
                              ("damage to you", "damage to a player")):
            with self.subTest(after=after):
                modified = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(modified, **args).status)

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activation(self, game, seat, source):
        self.priority(game, seat)
        packet = game.packet("pilot:" + seat, full=True)["decision"]
        return next(action["id"] for action in packet["ctx"]["legal"]["actions"]
                    if action["id"].startswith(f"activate:{source.ref}:"))

    def attacker(self, game, ref, *, seat="A"):
        card = self.add(game.engine, "Grizzly Bears", ref=ref, seat=seat)
        card.acquired_control_turn_count = -1
        return card

    def points(self, game, stadium):
        return game.state.cards[stadium.object_id].counters.get("point", 0)

    def proof_combat_crossing(self):
        game = self.session(12298011)
        stadium = self.add(game.engine, STADIUM)
        stadium.counters.update({"point": 8, "charge": 2})
        first = self.attacker(game, "stadium-first-at-b")
        second = self.attacker(game, "stadium-second-at-b")
        third = self.attacker(game, "stadium-only-at-c")
        healer = self.add(game.engine, "Samite Healer", seat="B")
        healer.acquired_control_turn_count = -1
        for card in (stadium, healer):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

        command = {"action_id": self.activation(game, "A", stadium), "mana_output": {"C": 1}}
        self.reject(game, "D", command)
        self.accept(game, "A", command)
        self.assertTrue(game.state.cards[stadium.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual(9, self.points(game, stadium))
        self.assertFalse(game.state.stack)

        self.attack(game, [(first, "B"), (second, "B"), (third, "C")])
        self.accept(game, "B", {"action_id": self.activation(game, "B", healer), "targets": ["B"], "pay": "auto"})
        self.resolve_top(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "replacement.order")
        self.assertEqual(["B"], list(game.state.pending_decision.actors))
        game = self.replay(game, load=True)
        choice_context = game.packet("pilot:B", full=True)["decision"]["ctx"]
        choice = choice_context["options"][0]["id"]
        events = choice_context["prevention_allocations"][choice]["events"]
        self.assertEqual(2, len(events))
        command = {"action_id": "choose", "replacement": choice,
                   "prevention_allocation": {events[0]["event_id"]: 1}}
        self.reject(game, "A", command)
        self.accept(game, "B", command)
        self.until(game, lambda current: current.state.pending_decision.kind == "trigger.order")
        occurrences = [item for group in game.state.pending_trigger_batches for item in group.items]
        self.assertEqual(["B", "B", "C"], sorted(item.event_facts["target"] for item in occurrences))
        self.assertEqual(3, len({item.event_facts["source"] for item in occurrences}))
        self.assertEqual([37, 38], [game.state.players[seat].life for seat in "BC"])
        ordered = sorted(occurrences, key=lambda item: item.event_facts["target"])
        self.accept(game, "A", {"action_id": "order", "triggers": [item.ref for item in ordered]})
        self.assertEqual("C", game.state.stack[-1].context["target"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertNotIn("C", game.engine.active_seats)
        self.assertIn("B", game.engine.active_seats)
        self.assertEqual({"charge": 2}, game.state.cards[stadium.object_id].counters)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual({"point": 2, "charge": 2}, game.state.cards[stadium.object_id].counters)
        self.replay(game, load=True)

    def test_tap_and_partial_combat_keep_each_damage_occurrence_and_only_its_actual_loser(self):
        self.proof_combat_crossing()

    def test_actual_received_damage_removes_only_one_point_and_never_checks_the_loss_threshold(self):
        game = self.session(12298021)
        stadium = self.add(game.engine, STADIUM)
        stadium.counters.update({"point": 11, "charge": 2})
        inbound = self.attacker(game, "stadium-foreign-attacker", seat="B")
        self.assert_closed(game, stadium)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "combat.attackers")
        self.attack_for(game, "B", [(inbound, "A")])
        self.until(game, lambda current: self.trigger_count(current, stadium) == 1)
        self.assertEqual("A", game.state.stack[-1].context["target"])
        self.assertNotIn(POINT_COUNTER_OBSERVATION, game.state.stack[-1].context)
        self.resolve_top(game)
        self.assertEqual({"point": 10, "charge": 2}, game.state.cards[stadium.object_id].counters)
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual({"A", "B", "C", "D"}, set(game.engine.active_seats))
        self.replay(game, load=True)

    def test_departed_source_keeps_its_final_threshold_and_never_places_on_another_object(self):
        for count in (9, 10):
            with self.subTest(count=count):
                game = self.session(12298030 + count)
                stadium = self.add(game.engine, STADIUM)
                stadium.counters["point"] = count
                body = self.attacker(game, "stadium-attack-before-source-loss")
                removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
                for index in range(2):
                    self.add(game.engine, "Plains", seat="B", ref=f"stadium-source-removal-mana-{index}")
                for card in (stadium, removal):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(body, "B")])
                self.until(game, lambda current: self.trigger_count(current, stadium) == 1)
                self.cast_for(game, "B", removal, [stadium.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[stadium.object_id].zone)
                observed = game.state.stack[-1].context[POINT_COUNTER_OBSERVATION]
                self.assertEqual(count, observed["departure_count"])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(count < 10, "B" in game.engine.active_seats)
                self.replay(game, load=True)

    def test_mana_point_replacement_keeps_tap_and_payment_atomic_for_direct_and_automatic_uses(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                game = self.session(12298050 + automatic)
                stadium = self.add(game.engine, STADIUM)
                stadium.counters["point"] = 8
                spell = self.add(game.engine, "Sol Ring", zone="hand")
                for index in range(2):
                    season = self.add(game.engine, "Doubling Season", ref=f"stadium-mana-doubler-{index}")
                    self.assert_closed(game, season)
                self.assert_closed(game, stadium)
                self.prepare(game)
                self.checkpoint(game)
                if automatic:
                    self.cast_for(game, "A", spell)
                else:
                    self.accept(game, "A", {"action_id": self.activation(game, "A", stadium), "mana_output": {"C": 1}})
                self.assertEqual("replacement.order", game.state.pending_decision.kind)
                self.assertFalse(game.state.cards[stadium.object_id].tapped)
                self.assertEqual(8, self.points(game, stadium))
                self.assertEqual(0, game.state.players["A"].mana_pool["C"])
                self.assertEqual("hand", game.state.cards[spell.object_id].zone)
                game = self.replay(game, load=True)
                context = game.packet("pilot:A", full=True)["decision"]["ctx"]
                choose = {"action_id": "choose", "replacement": context["options"][0]["id"]}
                self.reject(game, "B", choose)
                self.accept(game, "A", choose)
                self.assertTrue(game.state.cards[stadium.object_id].tapped)
                self.assertEqual(12, self.points(game, stadium))
                self.assertEqual(0 if automatic else 1, game.state.players["A"].mana_pool["C"])
                self.assertEqual(4, len(game.engine.active_seats))
                if automatic:
                    self.assertEqual("stack", game.state.cards[spell.object_id].zone)
                    self.resolve_top(game)
                    self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_mutation_that_omits_the_post_placement_threshold_is_detected(self):
        with patch("quorune.rules.combat_point_counters.point_game_loss_bound_results", return_value=()):
            with self.assertRaises(AssertionError):
                self.proof_combat_crossing()
