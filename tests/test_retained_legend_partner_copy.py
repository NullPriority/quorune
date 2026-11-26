from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import canonical_ability_fragments
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import RETAINED_LEGEND_PARTNER_COPY_QUERY, EntryCopySpec
from quorune.legend_rule_model import LEGEND_RULE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.token_copy_recipes import RETAINED_LEGEND_PARTNER_VERSION, retained_legend_rule_fragment
from scripts.build_test_database import build_fixture_database


SOURCE = "Sakashima of a Thousand Faces"
PARTNER = "Rograkh, Son of Rohgahh"


class RetainedLegendPartnerCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "retained-legend-partner.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "copiable-entry-ability-grant-cards",
            "legend-rule-exemption-cards", "granted-static-characteristic-cards", "activation-public-state-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Partner copy", [
            DeckEntry(SOURCE, 1, "commander"), DeckEntry(PARTNER, 1, "commander"), DeckEntry("Island", 30),
        ], [SOURCE, PARTNER])

    def copy_choice(self, game, model, *, excluded=()):
        decision = self.resolve(game)
        self.assertEqual("replacement.order", decision.kind)
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choices = [row["id"] for row in options if not row["decline"]]
        for row in excluded:
            self.assertFalse(any(row.ref in choice for choice in choices))
        choice, = (row for row in choices if model.ref in row)
        command = {"action_id": "choose", "replacement": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def assert_retained(self, game, card):
        current = game.state.cards[card.object_id]
        self.assertEqual(card.oracle_id, current.oracle_id)
        self.assertEqual("Isamaru, Hound of Konda", game.engine.display_name(current.object_id))
        values = game.engine._effective_card_data(current)
        self.assertIn("Partner", values["keywords"])
        self.assertIn(retained_legend_rule_fragment(), canonical_ability_fragments(values["ability_fragments"]))

    def test_actual_controlled_copy_retains_partner_and_legend_from_command_zone_and_replays(self):
        game = self.session(70470524)
        source = next(card for card in game.state.cards.values()
                      if card.owner == "A" and card.is_commander and card.printed_name == SOURCE)
        designation = source.commander_designation_id
        model = self.add(game.engine, "Isamaru, Hound of Konda", ref="retained-copy-own-legend")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="excluded-foreign-copy")
        private = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="retained-copy-hidden-card")
        clone = self.add(game.engine, "Clone", zone="hand")
        humble = self.add(game.engine, "Humble", zone="hand")
        remove = self.add(game.engine, "Murder", zone="hand")
        for card in (source, model, clone, humble, remove):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "U": 2, "W": 1, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        game = self.copy_choice(game, model, excluded=(foreign,))
        self.assert_retained(game, source)
        member = game.state.cards[source.object_id]
        self.assertTrue(member.is_commander)
        self.assertEqual(designation, member.commander_designation_id)
        self.assertEqual({self.db.lookup(name).oracle_id for name in (SOURCE, PARTNER)},
                         set(game.state.commander_oracle_ids["A"]))
        self.assertEqual("priority", game.state.pending_decision.kind)

        self.cast(game, "A", clone)
        game = self.copy_choice(game, source)
        self.assert_retained(game, clone)
        self.assertFalse(game.state.cards[clone.object_id].is_commander)
        self.cast(game, "A", humble, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assert_retained(game, clone)
        self.assertEqual(0, game.engine._numeric_stat(source.object_id, "power"))
        self.cast(game, "A", remove, [clone.ref])
        decision = self.resolve(game)
        self.assertEqual(("state.legend", ["A"]), (decision.kind, decision.actors))
        self.assertEqual({model.ref, source.ref}, set(decision.payload_by_actor["A"]["keep_one"]))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "card": source.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[model.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(designation, game.state.cards[source.object_id].commander_designation_id)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_retention_requires_the_exact_printed_siblings_and_controlled_other_copy_query(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        current = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", current.status, current.material_residuals)
        node, = (node for face in current.faces for node in face.nodes if node.kind == "replacement_effect")
        spec = EntryCopySpec.from_dict(node.handlers[0]["copy"])
        self.assertEqual(RETAINED_LEGEND_PARTNER_COPY_QUERY, spec.selection)
        self.assertEqual(RETAINED_LEGEND_PARTNER_VERSION, spec.exception.schema_version)
        self.assertIn(LEGEND_RULE_CAPABILITY, node.capability_dependencies)
        with self.assertRaises(ValueError):
            replace(spec,selection=replace(spec.selection,controller=None))
        for before, after in (("permanents you control", "creatures you control"),
                              ("another creature you control", "another creature an opponent controls"),
                              ("Sakashima's other abilities", "another creature's abilities"),
                              ("Partner (", "Flying (")):
            with self.subTest(change=before):
                variant = replace(record, oracle_text=record.oracle_text.replace(before, after))
                result = compile_oracle_card(variant, capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", result.status, result.material_residuals)

    def test_omitting_retained_source_abilities_from_the_compiler_is_killed(self):
        with patch("quorune.compiler.entry_copy_ability_nodes._retained_legend_partner_copy", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_retention_requires_the_exact_printed_siblings_and_controlled_other_copy_query()
