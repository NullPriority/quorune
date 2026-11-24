"""A current-turn compound keyword grant has disjoint live recipients."""

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
from quorune.compiler.conditional_granted_ability_templates import TURN_SOURCE_ENCHANTMENT_CREATURE_KEYWORDS_TEMPLATE
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.conditional_continuous import ConditionalTypedKeywordHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Yuna, Hope of Spira"
PROFILE = "commander_review"


class TurnGatedSourceAndEnchantmentKeywordsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "turn-source-enchantment-keywords.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/turn-gated-source-and-enchantment-keywords-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current enchantment creatures", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, "Yuna, Hope of Spira")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        grant, = [item for item in programs if item.event == "characteristics.evaluate"]
        return card, grant, [item for item in programs if item is not grant]

    def test_complete_original_keeps_both_keyword_recipients_and_finality_return(self):
        card, grant, (reanimate,) = self.programs()
        self.assertEqual(TURN_SOURCE_ENCHANTMENT_CREATURE_KEYWORDS_TEMPLATE, grant.provenance["template_id"])
        source, others = grant.handlers
        self.assertEqual("controller_turn", source["source_condition"]["kind"])
        self.assertEqual(source["source_condition"], others["source_condition"])
        self.assertEqual("source", source["target"]["kind"])
        self.assertEqual("source_controller", others["target"]["target_controller"])
        self.assertTrue(others["target"]["exclude_source"])
        self.assertEqual(ObjectQuerySpec(zones=("battlefield",), types_all=("creature", "enchantment")),
                         ObjectQuerySpec.from_dict(others["target"]["predicate"]))
        self.assertEqual(source["modifier"], others["modifier"])
        self.assertEqual(["Trample", "Lifelink", "Ward {2}"], source["modifier"]["add_abilities"])
        fragment, = source["modifier"]["add_ability_fragments"]
        self.assertEqual({"kind": "ward", "value": {"schema_version": 1, "generic_cost": 2}}, fragment)
        self.assertIn("counter.rule.finality", reanimate.capability_dependencies)
        self.assertIn("zone.reanimate.entry_counters", reanimate.capability_dependencies)
        for text in (
            card.oracle_text.replace("Yuna and ", "Your commander and "),
            card.oracle_text.replace("During your turn", "During each opponent's turn"),
            card.oracle_text.replace("enchantment creatures", "artifact creatures"),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)

    def test_raw_composition_rejects_missing_overlapping_and_mismatched_branches(self):
        _, grant, _ = self.programs()
        source, others = grant.handlers
        other_target = others["target"]
        for altered in (
            (source,),
            (others, source),
            (source, {**others, "target": {**other_target, "exclude_source": False}}),
            (source, {**others, "target": {**other_target, "target_controller": "any"}}),
            (source, {**others, "modifier": {**others["modifier"], "power": 1}}),
        ):
            with self.subTest(handlers=len(altered), payload=altered[-1]):
                denied = bind_semantic_program_runtime(
                    replace(grant, handlers=altered), capability_registry=self.registry, profile=PROFILE,
                )
                self.assertFalse(denied["strict"], denied)
                self.assertTrue(any("runtime_turn_source_enchantment_creature_keywords:invalid:" in blocker
                                    for blocker in denied["blockers"]), denied)

    def granted_keywords(self, game, card):
        return {"trample", "lifelink"} & game.engine._combat_keywords(game.state.cards[card.object_id])

    def ward_scene(self):
        game = self.session(70221041)
        engine = game.engine
        yuna = self.add(engine, NAME, zone="hand")
        evening = self.add(engine, "Enchanted Evening", zone="hand")
        courser = self.add(engine, "Nyxborn Courser")
        bear = self.add(engine, "Grizzly Bears", ref="enchantment-own-bear")
        other = self.add(engine, "Grizzly Bears", seat="B", ref="enchantment-opposing-bear")
        response = self.add(engine, "Unsummon", seat="B", zone="hand")
        hidden = self.add(engine, "Counterspell", seat="D", zone="hand", ref="enchantment-hidden-spell")
        for index in range(3):
            self.add(engine, "Island", seat="B", ref=f"enchantment-ward-payment-{index}")
        for card in (yuna, evening, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "G": 1, "W": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", yuna, reserve={"C": 3, "W": 2})
        self.resolve_top(game)
        self.assertEqual({"trample", "lifelink"}, self.granted_keywords(game, yuna))
        self.assertEqual({"trample", "lifelink"}, self.granted_keywords(game, courser))
        self.assertFalse(self.granted_keywords(game, bear))
        self.assertFalse(self.granted_keywords(game, other))

        self.cast_for(game, "A", evening)
        self.resolve_top(game)
        self.assertIn("Enchantment", engine._effective_card_data(game.state.cards[yuna.object_id])["type_line"])
        self.assertEqual({"trample", "lifelink"}, self.granted_keywords(game, bear))
        self.assertFalse(self.granted_keywords(game, other))
        self.cast_for(game, "B", response, [yuna.ref])
        queued_wards = [item for batch in game.state.pending_trigger_batches for item in batch.items
                        if item.get("semantic_key") == "builtin:ward"]
        wards = [item for item in game.state.stack if item.semantic_key == "builtin:ward"]
        self.assertEqual(1, len(wards) + len(queued_wards), "The overlapping source must trigger exactly one Ward")
        ward, = wards
        spell, = [item for item in game.state.stack if item.card_object_id == response.object_id]
        self.assertEqual(("A", "B", spell.ref, yuna.ref, {"GENERIC": 2}),
                         (ward.controller, ward.context["payer"], ward.context["target_stack"],
                          ward.context["targeted_permanent"], ward.context["cost"]))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        return game, yuna, response

    def test_actual_type_change_keeps_one_ward_and_real_ward_choice(self):
        game, yuna, response = self.ward_scene()
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (choice.kind, choice.actors))
        self.assertTrue(choice.payload_by_actor["B"]["payable"])
        game = self.replay(game, load=True)
        decline = {"action_id": "choose", "pay": False}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", decline).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", decline)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[yuna.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[response.object_id].zone)
        self.assertFalse(game.state.stack)

    def test_actual_enchantment_source_witness_kills_the_overlapping_query(self):
        lower = ConditionalTypedKeywordHandler.lower

        def include_source(handler, descriptor, context):
            target = descriptor["target"]
            if target["kind"] == "fixed_query":
                descriptor = {**descriptor, "target": {**target, "exclude_source": False}}
            return lower(handler, descriptor, context)

        with patch.object(ConditionalTypedKeywordHandler, "lower", include_source):
            with self.assertRaisesRegex(AssertionError, "1 != 2.*overlapping source must trigger exactly one Ward"):
                self.ward_scene()

    def test_actual_end_step_retains_the_return_finality_rule_and_turn_expiry(self):
        game = self.session(12210721)
        engine = game.engine
        yuna = self.add(engine, NAME, zone="hand")
        courser = self.add(engine, "Nyxborn Courser")
        fervor = self.add(engine, "Fervor", zone="graveyard", ref="own-enchantment-graveyard")
        foreign = self.add(engine, "Fervor", seat="C", zone="graveyard", ref="foreign-enchantment-graveyard")
        destroy = self.add(engine, "Disenchant", seat="B", zone="hand")
        for index in range(2):
            self.add(engine, "Plains", seat="B", ref=f"enchantment-finality-payment-{index}")
        for card in (yuna, fervor, destroy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", yuna)
        self.resolve_top(game)
        self.assertEqual({"trample", "lifelink"}, self.granted_keywords(game, courser))
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "choose", "targets": [foreign.ref]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "A", [fervor.ref])
        self.resolve_top(game)
        returned = game.state.cards[fervor.object_id]
        self.assertEqual(("battlefield", 1), (returned.zone, returned.counters.get("finality")))
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        self.cast_for(game, "B", destroy, [returned.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[fervor.object_id].zone)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")
        self.assertFalse(self.granted_keywords(game, yuna))
        self.assertFalse(self.granted_keywords(game, courser))
        self.replay(game, load=True)
