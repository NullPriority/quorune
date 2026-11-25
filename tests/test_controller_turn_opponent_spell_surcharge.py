"""The embedded controller-turn clause uses the existing exact spell-price owner."""

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
from quorune.cast_cost_modifiers import (
    CastCostAffectedController, CastCostTurnRelation, PublicCastCostModifierV2Spec,
)
from quorune.compiler import public_cast_cost_modifiers
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.cast_costs import FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID
from scripts.build_test_database import build_fixture_database
import test_class_lifecycle as class_controls
from test_controller_creature_keyword_absence import register_card


NAME = "Paladin Class"
SUFFIX = "Spells your opponents cast during your turn cost {1} more to cast."
PREFIX = "During your turn, spells your opponents cast cost {1} more to cast."
PROFILE = "commander_review"


class ControllerTurnOpponentSpellSurchargeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-turn-spell-price.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-turn-opponent-spell-surcharge-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controller turn spell price", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        tax, = [p for p in programs if p.event == "cast.cost.modify"]
        self.assertEqual(2, sum(item.event == "activate" for item in programs))
        self.assertTrue(any(item.event == "creature.attacks" for item in programs))
        self.assertTrue(any(item.event == "characteristics.evaluate" for item in programs))
        return card, tax

    def test_original_uses_the_same_price_descriptor_and_keeps_every_class_level(self):
        card, tax = self.programs()
        expected = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(SUFFIX, PREFIX)),
                                       capability_registry=self.registry, capability_profile=PROFILE)
        reference, = [node for face in expected.faces for node in face.nodes if node.event == "cast.cost.modify"]
        self.assertEqual(reference.handlers, tuple(tax.handlers))
        spec = PublicCastCostModifierV2Spec.from_dict(tax.handlers[0]["modifier"])
        self.assertEqual(CastCostAffectedController.SOURCE_OPPONENTS, spec.affected_controller)
        self.assertEqual(CastCostTurnRelation.SOURCE_CONTROLLER_TURN, spec.turn_relation)
        self.assertEqual((("GENERIC", 1),), spec.mana_adjustment)
        for bad in (
            SUFFIX.replace("during your turn", "during their turns"),
            SUFFIX.replace("Spells", "Noncreature spells"),
            SUFFIX.replace("cost {1} more", "cost {X} more"),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(SUFFIX, bad)),
                                     capability_registry=self.registry)
            self.assertFalse(any(node.handlers == reference.handlers for face in ir.faces for node in face.nodes))

    def test_original_modifier_requires_its_existing_exact_price_authority(self):
        _, tax = self.programs()
        caps = [value for value in tax.capability_dependencies if value != FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID]
        closure = self.registry.closure(caps, profile=PROFILE).to_dict() if caps else None
        result = bind_semantic_program_runtime(replace(tax, capability_dependencies=caps, capability_closure=closure),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID, result["blockers"])

    def scene(self, seed, *, extra=False):
        game = self.session(seed)
        engine = game.engine
        paladin = self.add(engine, NAME, zone="hand")
        body = self.add(engine, "Grizzly Bears", ref="paladin-a-bear")
        opposing = self.add(engine, "Grizzly Bears", seat="B", ref="paladin-b-bear")
        spell = self.add(engine, "Unsummon", seat="B", zone="hand")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="paladin-private")
        funds = [self.add(engine, "Island", seat="B", ref="paladin-b-island")]
        if extra:
            funds.append(self.add(engine, "Plains", seat="B", ref="paladin-b-generic"))
        for index in range(4):
            self.add(engine, "Plains", ref=f"paladin-a-price-{index}")
        for card in (paladin, spell):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", paladin)
        self.resolve_top(game)
        return game, paladin, body, opposing, spell, funds, private

    def spell_offered(self, game, spell):
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        return any(item["id"] == f"cast:{spell.ref}" for item in rows)

    def test_actual_underfunded_offer_and_command_fail_only_during_the_source_controllers_turn(self):
        game, _paladin, body, _opposing, spell, funds, private = self.scene(60121101)
        self.assertFalse(self.spell_offered(game, spell))
        before = authoritative_state_hash(game.state)
        command = {"action_id": f"cast:{spell.ref}", "targets": [body.ref], "pay": "auto"}
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.cards[funds[0].object_id].tapped)

        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")
        self.assertTrue(self.spell_offered(game, spell), "The opponent's own turn releases the tax")
        self.cast_for(game, "B", spell, [body.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[body.object_id].zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_tax_uses_the_extra_mana_and_the_paid_class_level_still_grants_stats(self):
        game, paladin, body, opposing, spell, funds, _private = self.scene(60121102, extra=True)
        self.priority(game, "A")
        source = game.state.cards[paladin.object_id]
        abilities = class_controls.ClassLifecycleRuntimeTests.class_abilities(game.engine, source)
        action = f"activate:{source.ref}:{abilities[2].ability_id}"
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in offered})
        advanced = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(advanced.ok, advanced.summary)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[paladin.object_id].class_level)
        self.assertEqual(3, game.engine._numeric_stat(body.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(opposing.object_id, "power"))

        self.assertTrue(self.spell_offered(game, spell))
        self.cast_for(game, "B", spell, [opposing.ref])
        self.resolve_top(game)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in funds))
        self.assertEqual("hand", game.state.cards[opposing.object_id].zone)
        self.replay(game, load=True)

    def test_actual_turn_rollover_kills_an_unconditional_tax_mutant(self):
        original = public_cast_cost_modifiers.public_cast_cost_modifier_template

        def drop_turn(text):
            spec = original(text)
            if spec is not None and spec.turn_relation is CastCostTurnRelation.SOURCE_CONTROLLER_TURN:
                return replace(spec, turn_relation=CastCostTurnRelation.ANY)
            return spec

        with patch.object(public_cast_cost_modifiers, "public_cast_cost_modifier_template", drop_turn):
            with self.assertRaisesRegex(AssertionError, "The opponent's own turn releases the tax"):
                self.test_actual_underfunded_offer_and_command_fail_only_during_the_source_controllers_turn()
