"""The original departure trigger, overlapping counter recipients and replay."""
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
from quorune.counter_placement_sets import (
    SOURCE_AND_CONTROLLED_SUBTYPE_CAPABILITY, SOURCE_AND_CONTROLLED_SUBTYPE_FIELD,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Ich-Tekik, Salvage Splicer"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class SourceAndControlledSubtypeCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        supporting = {
            "additional-beginning-originals-cards.json": {"Disenchant"},
            "upkeep-creature-token-absence-cards.json": {"Universal Automaton"},
            "creature-subtype-set-results-cards.json": {"Shields of Velis Vel"},
            "optional-object-action-counters.json": {"Hardened Scales"},
            "attached-object-keyword-cards.json": {"Revoke Existence"},
        }
        fixtures = [FIXTURES / "bound-effect-program-cards.json",
                    FIXTURES / "source-and-controlled-subtype-counters-cards.json"]
        for fixture, names in supporting.items():
            data = json.loads((FIXTURES / fixture).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            path = folder / fixture
            path.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(path)
        db = folder / "source-and-subtype-counter.sqlite3"
        build_fixture_database(fixtures, db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Source and subtype counters", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def assert_original_trigger(self, game, source):
        (trigger,) = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertEqual([], trigger.targets)
        self.assertEqual("A", trigger.controller)
        return trigger

    def test_original_is_exact_and_wrong_scope_or_omitted_authority_fails_closed(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)

        def compile_record(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        lowered = compile_record(card)
        self.assertEqual("exact", lowered.status, lowered.material_residuals)
        (node,) = (node for node in lowered.faces[0].nodes if node.event == "artifact.graveyard")
        self.assertIsNone(node.target_schema)
        self.assertIn(SOURCE_AND_CONTROLLED_SUBTYPE_CAPABILITY, node.capability_dependencies)
        (effect,) = node.effects
        self.assertEqual(1, effect[SOURCE_AND_CONTROLLED_SUBTYPE_FIELD])
        self.assertEqual(["golem"], effect["set"]["query"]["subtypes_all"])
        self.assertEqual([], effect["set"]["query"]["types_all"])
        for oracle in (
            card.oracle_text.replace("an artifact is put into a graveyard from the battlefield",
                                     "an artifact enters"),
            card.oracle_text.replace("each Golem you control", "each Golem an opponent controls"),
            card.oracle_text.replace("counter on Ich-Tekik and", "counter on target creature and"),
        ):
            changed = compile_record(replace(card, oracle_text=oracle))
            self.assertNotEqual("exact", changed.status, oracle)

        game = self.session(60310080)
        source = self.add(game.engine, ORIGINAL)
        self.assert_closed(game, source)
        (program,) = (entry for entry in game.engine.semantics.programs_for_oracle(source.oracle_id)
                      if entry.event == "artifact.graveyard")
        kept = tuple(cap for cap in program.capability_dependencies if cap != SOURCE_AND_CONTROLLED_SUBTYPE_CAPABILITY)
        missing = replace(program, capability_dependencies=kept,
                          capability_closure=self.registry.closure(kept, profile=PROFILE).to_dict())
        for candidate in (missing, replace(program, event="artifact.enter"),
                          replace(program, effects=[{**effect, SOURCE_AND_CONTROLLED_SUBTYPE_FIELD: True}]),
                          replace(program, event_condition={"field": "card", "op": "truthy", "value": True})):
            proof = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(proof["strict"])
        proof = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + SOURCE_AND_CONTROLLED_SUBTYPE_CAPABILITY,
                      proof["blockers"])

    def test_actual_artifact_token_departure_retains_only_the_current_owned_subtype_result(self):
        game = self.session(60310081)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        golem = self.add(game.engine, "Universal Automaton", ref="actor-golem")
        foreign = self.add(game.engine, "Universal Automaton", seat="B", ref="foreign-golem")
        destroy = self.add(game.engine, "Disenchant", zone="hand", ref="departing-artifact")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="departing-source")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="golem-private")
        for card in (source, golem, foreign, destroy, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "G": 1, "W": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"W": 1})
        self.assertIsNone(self.resolve(game))
        (created,) = (card for card in game.state.cards.values() if card.is_token
                      and card.controller == "A" and card.zone == "battlefield"
                      and card.printed_name == "Phyrexian Golem Token")
        self.cast(game, "A", destroy, [created.ref])
        self.resolve_top(game)
        trigger = self.assert_original_trigger(game, source)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[golem.object_id].counters.get("+1/+1"))
        self.assertEqual(0, game.state.cards[foreign.object_id].counters.get("+1/+1", 0))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def assert_actual_overlap(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL)
        other = self.add(game.engine, "Grizzly Bears")
        types = self.add(game.engine, "Shields of Velis Vel", zone="hand")
        replacement = self.add(game.engine, "Hardened Scales")
        exile = self.add(game.engine, "Revoke Existence", zone="hand")
        destroy = self.add(game.engine, "Disenchant", zone="hand")
        exiled = self.add(game.engine, "Universal Automaton", seat="B", ref="exile-control")
        departed = self.add(game.engine, "Universal Automaton", seat="B", ref="graveyard-control")
        for card in (source, types, replacement, exile, destroy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", types, ["A"], reserve={"W": 2})
        self.resolve_top(game)
        for card in (source, other):
            data = game.engine._effective_card_data(game.state.cards[card.object_id])
            self.assertIn("golem", game.engine._type_parts(data["type_line"])[1])
        self.cast_for(game, "A", exile, [exiled.ref], reserve={"W": 1})
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual("exile", game.state.cards[exiled.object_id].zone)
        self.cast(game, "A", destroy, [departed.ref])
        self.resolve_top(game)
        self.assert_original_trigger(game, source)
        self.assertEqual("graveyard", game.state.cards[departed.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([3, 2], [game.state.cards[obj.object_id].counters.get("+1/+1")
                                  for obj in (source, other)])
        self.replay(game, load=True)

    def test_actual_overlapping_golem_source_contributes_twice_to_one_counter_event(self):
        self.assert_actual_overlap(60310082)

    def test_omitted_source_contribution_mutation_changes_real_replacement_result(self):
        from quorune import counter_placement_sets

        owner = counter_placement_sets.source_and_controlled_subtype_counter_requests
        def only_subtype(snapshot, current_source, **options):
            return owner(snapshot, None, **options)
        with patch.object(counter_placement_sets, "source_and_controlled_subtype_counter_requests", only_subtype):
            with self.assertRaisesRegex(AssertionError, r"3, 2"):
                self.assert_actual_overlap(60310083)
