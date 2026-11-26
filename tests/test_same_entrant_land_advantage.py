"""A land arrival compares its original opponent against the trigger controller."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.land_entry_condition_model import (
    ENTRANT_LAND_ADVANTAGE_CAPABILITY, ENTRANT_LAND_ADVANTAGE_COVERAGE, entrant_land_advantage_condition,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Archaeomancer's Map"
PROFILE = "commander_review"


class SameEntrantLandAdvantageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "same-entrant-land-advantage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/same-entrant-land-advantage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Same entering opponent", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, source, *companions, active="B", mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in (source, *companions):
            self.assert_closed(game, card)
        self.prepare(game, active=active, actor=active, mana=mana)
        self.checkpoint(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def play_land(self, game, seat, card):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row.get("action") == "play_land" and row.get("card") == card.ref)
        self.act(game, seat, {"action_id": offer["id"]})
        self.assertEqual("battlefield", game.state.cards[card.object_id].zone)

    def expect_hand_choice(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        return {row["id"] for row in context["objects"]}

    def test_actual_cast_preserves_up_to_two_publicly_revealed_basic_plains(self):
        game = self.session(603043051)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        first = self.add(game.engine, "Snow-Covered Plains", zone="library", ref="map-basic-plains-first")
        second = self.add(game.engine, "Snow-Covered Plains", zone="library", ref="map-basic-plains-second")
        wrong = self.add(game.engine, "Island", zone="library", ref="map-non-plains")
        self.ready(game, source, active="A", mana={"C": 2, "W": 1})
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        self.reject(game, "A", {"action_id": "choose", "search_cards": [first.ref, wrong.ref]})
        self.act(game, "A", {"action_id": "choose", "search_cards": [first.ref, second.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["hand", "hand", "library"],
                         [game.state.cards[card.object_id].zone for card in (first, second, wrong)])
        packet = game.packet("pilot:B", full=True)
        shown = {row["id"]: row["n"] for row in packet["state"]["players"]["A"]["known_hand"]}
        self.assertEqual({first.ref: "Snow-Covered Plains", second.ref: "Snow-Covered Plains"}, shown)
        self.assertNotIn(wrong.ref, json.dumps(packet))
        self.replay(game, load=True)

    def test_actual_entrant_stays_bound_after_source_departure_and_land_choice_is_private(self):
        game = self.session(603043052)
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            game.engine.move_card(object_id, "library", log=False)
        source = self.add(game.engine, ORIGINAL)
        chose = self.add(game.engine, "Forest", zone="hand", ref="map-private-chosen-land")
        spare = self.add(game.engine, "Island", zone="hand", ref="map-private-other-land")
        nonland = self.add(game.engine, "Grizzly Bears", zone="hand", ref="map-private-nonland")
        self.add(game.engine, "Forest", ref="map-a-land")
        self.add(game.engine, "Forest", seat="B", ref="map-b-land")
        entering = self.add(game.engine, "Island", seat="B", zone="hand", ref="map-b-entrant")
        remove = self.add(game.engine, "Naturalize", seat="C", zone="hand")
        self.add(game.engine, "Snow-Covered Plains", seat="C")
        self.add(game.engine, "Forest", seat="C", ref="map-c-land")
        self.ready(game, source, remove)
        self.play_land(game, "B", entering)
        trigger, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual(("A", "B"), (trigger.controller, trigger.context["controller"]))
        self.assertEqual(game.state.cards[entering.object_id].logical_object_id, trigger.context["card_object_identity"])
        self.cast(game, "C", remove, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.assertEqual({chose.ref, spare.ref}, self.expect_hand_choice(game))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for hidden in (chose, spare, nonland):
                self.assertNotIn(hidden.ref, packet)
        command = {"action_id": "choose", "card": chose.ref}
        self.reject(game, "B", command)
        self.reject(game, "A", {"action_id": "choose", "card": nonland.ref})
        land_plays = game.state.players["A"].land_plays_remaining
        self.act(game, "A", command)
        self.assertIsNone(self.resolve(game))
        chosen = game.state.cards[chose.object_id]
        self.assertEqual(("battlefield", "A", False), (chosen.zone, chosen.controller, chosen.tapped))
        self.assertEqual(land_plays, game.state.players["A"].land_plays_remaining)
        self.assertEqual("hand", game.state.cards[spare.object_id].zone)
        for seat in "BCD":
            self.assertNotIn(spare.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_same_entrant_count_is_strict_at_arrival_and_after_phasing(self):
        for original_lands in (0, 1, 2):
            with self.subTest(original_lands=original_lands):
                game = self.session(603043053 + original_lands)
                source = self.add(game.engine, ORIGINAL)
                self.add(game.engine, "Forest", ref="map-comparer-land")
                private = self.add(game.engine, "Island", zone="hand", ref="map-land-hidden-on-recheck")
                entering = self.add(game.engine, "Forest", seat="B", zone="hand", ref="map-original-entrant")
                for number in range(original_lands):
                    self.add(game.engine, "Forest", seat="B", ref=f"map-older-land-{number}")
                self.add(game.engine, "Island", seat="C", ref="map-richer-island")
                self.add(game.engine, "Forest", seat="C", ref="map-richer-forest")
                self.add(game.engine, "Forest", seat="C", ref="map-richer-other-forest")
                phase = self.add(game.engine, "Reality Ripple", seat="C", zone="hand")
                self.ready(game, source, phase)
                self.play_land(game, "B", entering)
                self.assertEqual(int(original_lands > 0), self.trigger_count(game, source))
                if original_lands:
                    original = game.state.stack[-1].ref
                    self.cast(game, "C", phase, [entering.ref])
                    self.resolve_top(game)
                    self.assertTrue(game.state.cards[entering.object_id].phased_out)
                    game = self.replay(game, load=True)
                    if original_lands == 1:
                        self.assertIsNone(self.resolve(game))
                        self.assertTrue(any(event.code == "stack.trigger.removed"
                                            and event.details.get("stack") == original
                                            and event.details.get("reason") == "intervening_condition_false"
                                            for event in game.state.events))
                    else:
                        self.assertIn(private.ref, self.expect_hand_choice(game))
                        self.act(game, "A", {"action_id": "choose"})
                        self.assertIsNone(self.resolve(game))
                self.assertEqual("hand", game.state.cards[private.object_id].zone)
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)

    def test_complete_original_requires_same_entrant_recheck_and_private_land_result(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        cap = ENTRANT_LAND_ADVANTAGE_CAPABILITY
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            entry, triggered = ir.faces[0].nodes
            self.assertEqual("permanent.enter.self", entry.event)
            self.assertEqual({"minimum": 0, "maximum": 2}, entry.effects[0]["count"])
            self.assertEqual(entrant_land_advantage_condition(), triggered.event_condition)
            self.assertIn(cap, triggered.capability_dependencies)
        bound, semantics = register_card(self.db, self.registry, "Archaeomancer's Map")
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(programs))
        program, = (item for item in programs if cap in item.capability_dependencies)
        self.assertIn(ENTRANT_LAND_ADVANTAGE_COVERAGE, program.coverage)
        dependencies = tuple(key for key in program.capability_dependencies if key != cap)
        without = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(without, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", report["blockers"])
        for changed in (
            replace(program, event_condition={"field": "controller", "op": "ne", "value": "$source.controller"}),
            replace(program, coverage=[key for key in program.coverage if key != "intervening_condition"]),
            replace(program, effects=[{**program.effects[0], "tapped": True}]),
        ):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (
            ("that player", "an opponent"),
            ("more lands than you", "at least as many lands as you"),
            ("land card from your hand", "land card from your graveyard"),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertFalse(any(cap in node.capability_dependencies for node in compiled.faces[0].nodes))
