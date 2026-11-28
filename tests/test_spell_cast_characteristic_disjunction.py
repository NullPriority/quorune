"""Spell-cast disjunctions use one immutable occurrence for all alternatives."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Sram, Senior Edificer"
PROFILE = "commander_review"


class SpellCastCharacteristicDisjunctionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "spell-cast-characteristic-disjunction.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "spell-cast-characteristic-disjunction-cards.json",
            fixtures / "ability-kind-cost-modifier-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "any-number-target-cards.json",
            fixtures / "grouped-choose-one-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Spell-cast characteristic disjunction", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def ready_source(self, game, *, owner="A"):
        source = self.add(game.engine, CARD, seat=owner)
        if owner != "A":
            game.engine.change_control(source.object_id, "A", reason="Borrowed source fixture")
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        return source

    def assert_above_spell(self, game, source, spell):
        root, trigger = game.state.stack
        self.assertEqual(spell.object_id, root.card_object_id)
        self.assertEqual(("triggered_ability", "A", source.object_id, []),
                         (trigger.kind, trigger.controller, trigger.source_object_id, trigger.targets))
        self.assertEqual(root.ref, trigger.context["stack"])

    @staticmethod
    def draws_since(game, offset):
        return [event.actor for event in game.state.events[offset:] if event.code == "card.draw"]

    def test_actual_all_three_subtypes_trigger_draw_and_aura_counter_does_not_cancel_it(self):
        game = self.session(60102091)
        source = self.ready_source(game, owner="D")
        aura = self.add(game.engine, "Pacifism", zone="hand")
        equipment = self.add(game.engine, "Bonesplitter", zone="hand")
        vehicle = self.add(game.engine, "Sky Skiff", zone="hand")
        plain = self.add(game.engine, "Ornithopter", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.prepare(game, mana={"C": 4, "W": 1})
        game.state.players["B"].mana_pool.update({"U": 2})
        self.checkpoint(game)

        offset = len(game.state.events)
        self.cast(game, "A", aura, [source.ref])
        self.assert_above_spell(game, source, aura)
        self.assertEqual([], self.draws_since(game, offset))
        self.cast(game, "B", counter, [game.state.stack[0].ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["A"], self.draws_since(game, offset))
        draw = game.state.cards[game.state.players["A"].zones["hand"][-1]].ref
        self.assertIn(draw, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(draw, json.dumps(game.packet(principal, full=True)))

        offset = len(game.state.events)
        for spell in (equipment, vehicle):
            self.cast(game, "A", spell)
            self.assert_above_spell(game, source, spell)
            self.resolve_top(game)
            self.assertEqual("stack", game.state.cards[spell.object_id].zone)
            self.assertIsNone(self.resolve(game))
        self.cast(game, "A", plain)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["A", "A"], self.draws_since(game, offset))
        self.replay(game, load=True)

    def test_opposing_equipment_and_uncast_controller_copy_do_not_trigger(self):
        game = self.session(60102092)
        source = self.ready_source(game)
        equipment = self.add(game.engine, "Bonesplitter", seat="B", zone="hand")
        self.prepare(game, actor="B", active="B", mana={"C": 1})
        self.checkpoint(game)
        offset = len(game.state.events)
        self.cast(game, "B", equipment)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(1, len(game.state.stack))
        original = game.state.stack[0]
        copy = game.engine._copy_stack_item(
            controller="A", target=original, targets=(), target_groups={},
            reason="Qualifying but uncast Equipment copy fixture",
        )
        self.assertEqual(("spell_copy", "A"), (copy.kind, copy.controller))
        self.assertEqual(0, self.trigger_count(game, source))
        self.checkpoint(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.draws_since(game, offset))
        self.replay(game, load=True)

    def test_whole_card_trust_and_unrepresented_list_boundaries(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("spell.cast", node.event)
        self.assertEqual((card.oracle_text, 0, len(card.oracle_text)), (node.text, node.span.start, node.span.end))
        self.assertIn("trigger.event.normalized_spell_cast", node.capability_dependencies)
        self.assertIsNone(node.target_schema)
        self.assertEqual(
            {"all": [
                {"field": "controller", "op": "eq", "value": "$source.controller"},
                {"any": [{"field": "subtypes", "op": "contains_any", "value": [kind]}
                         for kind in ("aura", "equipment", "vehicle")]},
            ]},
            node.event_condition,
        )
        report, _semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        for variants in (
            "Aura, Equipment, or Equipment", "Aura, Equipment, or InventedSubtype",
            "Aura, Equipment, and Vehicle", "Aura, Equipment, Vehicle, or Saga",
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace("Aura, Equipment, or Vehicle", variants))
            self.assertNotEqual("exact", compile_card(changed).status, variants)
        with patch("quorune.compiler.spell_cast_predicates._spell_cast_characteristic_query", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)
