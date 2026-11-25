"""The exact Angelic Voices condition uses current type and color membership."""

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
from quorune.characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import NEGATIVE_COLOR_QUERY_CAPABILITY, ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Angelic Voices"
PROFILE = "commander_review"
CAPABILITY = NEGATIVE_COLOR_QUERY_CAPABILITY
FIXTURES = ROOT / "tests/fixtures"


class ControlledNonartifactNoncolorAbsenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "controlled-nonartifact-noncolor.sqlite3"
        build_fixture_database([basic, FIXTURES / "controlled-nonartifact-noncolor-absence-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current artifact and color exclusions", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        static, = semantics.programs_for_oracle(record.oracle_id)
        return record, static

    def test_exact_original_uses_both_exclusions_in_one_controlled_zero_count(self):
        record, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        rule, = program.handlers
        condition = rule["source_condition"]
        self.assertEqual(("query_count_at_most", 0), (condition["kind"], condition["amount"]))
        quantity = condition["quantity"]
        self.assertEqual("controller_zone", quantity["scope"])
        self.assertEqual(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",),
                                        excluded_types=("artifact",), colors_none=("W",)),
                         ObjectQuerySpec.from_dict(quantity["query"]))
        for text in (
            record.oracle_text.replace("nonartifact, nonwhite", "nonartifact or nonwhite"),
            record.oracle_text.replace("nonartifact, nonwhite creatures", "nonartifact, nonwhite lands"),
            record.oracle_text.replace("nonartifact, nonwhite", "nonartifact, noncolorless"),
        ):
            result = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                         capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in result.faces for node in face.nodes))

    def test_raw_conjunction_requires_the_independent_negative_color_capability(self):
        _, program = self.program()
        rule, = program.handlers
        spec = CharacteristicQuantitySpec.from_dict(rule["source_condition"]["quantity"])
        for changes in (
            {"schema_version": 1},
            {"scope": CharacteristicQuantityScope.OPPONENT_ZONES},
            {"query": replace(spec.query, excluded_types=())},
            {"query": replace(spec.query, colors_none=("W", "B"))},
        ):
            with self.assertRaises(ValueError):
                replace(spec, **changes)
        caps = [value for value in program.capability_dependencies if value != CAPABILITY]
        changed = replace(program, capability_dependencies=caps,
                          capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_white_multicolor_and_artifact_exceptions_follow_color_and_zone_changes(self):
        game = self.session(613050601)
        engine = game.engine
        voices = self.add(engine, NAME, zone="hand")
        white = self.add(engine, "Safehold Elite")
        artifact = self.add(engine, "Memnite")
        foe = self.add(engine, "Grizzly Bears", seat="B", ref="voices-foreign-green")
        unwanted = self.add(engine, "Grizzly Bears", zone="hand", ref="voices-own-green")
        hour = self.add(engine, "Darkest Hour", seat="B", zone="hand")
        self.add(engine, "Vedalken Orrery", seat="B")
        remove = self.add(engine, "Disenchant", zone="hand")
        bounce = self.add(engine, "Boomerang", zone="hand")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="voices-unrevealed")
        for card in (voices, white, artifact, hour, remove, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "W": 3, "G": 1, "U": 2})
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)

        self.cast_for(game, "A", voices, reserve={"C": 2, "W": 1, "G": 1, "U": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((3, 3), self.stats(game, white))
        self.assertEqual((2, 2), self.stats(game, artifact))
        self.assertEqual((2, 2), self.stats(game, foe))

        self.cast_for(game, "B", hour)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"B"}, set(game.engine._effective_card_data(game.state.cards[white.object_id])["colors"]))
        self.assertEqual((2, 2), self.stats(game, white))
        self.assertEqual((1, 1), self.stats(game, artifact))
        game = self.replay(game, load=True)

        self.cast_for(game, "A", remove, [hour.ref], reserve={"C": 1, "G": 1, "U": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"G", "W"}, set(game.engine._effective_card_data(game.state.cards[white.object_id])["colors"]))
        self.assertEqual((3, 3), self.stats(game, white))
        self.assertEqual((2, 2), self.stats(game, artifact))
        self.cast_for(game, "A", unwanted, reserve={"U": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), self.stats(game, white))
        self.assertEqual((2, 2), self.stats(game, unwanted))
        self.cast_for(game, "A", bounce, [unwanted.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((3, 3), self.stats(game, white))
        self.assertEqual((2, 2), self.stats(game, artifact))
        self.assertEqual((2, 2), self.stats(game, foe))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_nonwhite_artifact_kills_omitting_the_type_exclusion(self):
        from quorune.dynamic_characteristics import object_matches_query

        def ignore_artifact_exclusion(row, query):
            if query.types_all == ("creature",) and query.excluded_types == ("artifact",) and query.colors_none == ("W",):
                query = replace(query, excluded_types=())
            return object_matches_query(row, query)

        with patch("quorune.dynamic_characteristics.object_matches_query", side_effect=ignore_artifact_exclusion):
            with self.assertRaisesRegex(AssertionError, "Tuples differ"):
                self.test_actual_white_multicolor_and_artifact_exceptions_follow_color_and_zone_changes()
