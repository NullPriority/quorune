"""Printed Hauntwoods entry and attack results through the named land owner."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.basic_land_types import BASIC_LAND_MANA
from quorune.carddb import CardDatabase
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_basic_land_token import fixed_basic_land_token_payload_dependencies
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests/fixtures/fixed-basic-land-token-cards.json"


class FixedBasicLandTokenCompilerTests(unittest.TestCase):
    def test_fixed_definition_closes_only_the_declared_land_types_and_tapped_entry(self):
        card = next(record for record in json.loads(FIXTURE.read_bytes())["cards"]
                    if record["name"] == "Overlord of the Hauntwoods")
        body = card["oracle_text"].splitlines()[1].split(", ", 1)[1]
        template = fixed_token_creation_effect_template(body)
        self.assertIsNotNone(template)
        effect = template.effect
        self.assertEqual("Everywhere", effect["name"])
        self.assertIn("mana.intrinsic.basic_land_type", fixed_basic_land_token_payload_dependencies(effect))
        for changed in (dict(effect, tapped=False), dict(effect, quantity=True),
                        dict(effect, characteristics={**effect["characteristics"], "colors": ["G"]}),
                        dict(effect, characteristics={**effect["characteristics"],
                                                     "type_line": "Token Land — Forest"})):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                fixed_basic_land_token_payload_dependencies(changed)
        self.assertIsNone(fixed_token_creation_effect_template(
            "Create a colorless land token named Everywhere that is every basic land type."
        ))


class FixedBasicLandTokenNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-land-token.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", FIXTURE], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Fixed land token", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def assert_land_tokens(self, game, count):
        tokens = [card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token and card.printed_name == "Everywhere"]
        self.assertEqual(count, len(tokens))
        for land in tokens:
            self.assertEqual("A", land.controller)
            self.assertTrue(land.tapped)
            settings = game.engine._effective_card_data(land)
            types, subtypes, supertypes = game.engine._type_parts(settings["type_line"])
            self.assertEqual(({"land"}, set(BASIC_LAND_MANA), set()), (types, subtypes, supertypes))
            self.assertEqual([], settings["colors"])
            self.assertEqual(
                {"intrinsic_" + kind for kind in BASIC_LAND_MANA},
                {ability.ability_id for ability in game.engine._activated_abilities(land) if ability.mana_ability},
            )

    def test_actual_paid_hauntwoods_entry_gives_one_tapped_colorless_land_with_each_mana_ability(self):
        game = self.session(702176200)
        source = self.add(game.engine, "Overlord of the Hauntwoods", zone="hand")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="land-maker-private")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "G": 2})
        self.checkpoint(game)
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        command = {"action_id": offer["id"], "cost_option": "impending", "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assert_land_tokens(game, 1)
        self.assertEqual(4, game.state.cards[source.object_id].counters.get("time"))
        for seat in ("A", "B", "C"):
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        self.assert_land_tokens(game, 1)

    def test_actual_hauntwoods_attack_doubles_the_named_tapped_land_definition(self):
        game = self.session(702176201)
        source = self.add(game.engine, "Overlord of the Hauntwoods")
        multiplier = self.add(game.engine, "Parallel Lives")
        self.assert_closed(game, source)
        self.assert_closed(game, multiplier)
        self.prepare(game)
        self.checkpoint(game)
        self.assert_land_tokens(game, 0)
        self.attack(game, [(source, "B")])
        self.assertIsNone(self.resolve(game))
        self.assert_land_tokens(game, 2)
        self.assert_land_tokens(self.replay(game, load=True), 2)

    def test_removing_the_named_land_lowering_restores_the_material_printed_result(self):
        from quorune.oracle_ir import compile_oracle_card

        record = self.db.lookup("Overlord of the Hauntwoods")
        with patch("quorune.compiler.fixed_token_production_templates._fixed_basic_land_token_template",
                   return_value=None):
            lowered = compile_oracle_card(record, capability_registry=self.registry,
                                         capability_profile="commander_review")
        self.assertTrue(any(value.kind == "trigger_effect" for value in lowered.material_residuals))
