"""Exercise the outgoing combat shield and its one target's mana value."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Subdue"


class TargetCombatPreventionAndManaValueToughnessTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-combat-mana-value.sqlite3"
        fixture = ROOT / "tests/fixtures/target-combat-prevention-and-mana-value-toughness-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target mana-value witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_original_shares_one_creature_target_and_reads_once_in_the_toughness_slot(self):
        card, program = self.source_program()
        self.assertEqual(["creature"], program.target_schema["types_any"])
        self.assertEqual(1, program.target_schema["count"])
        shield, stats = program.effects
        self.assertEqual(("combat", "$target.0", "*", ["battlefield"]),
            (shield["damage_kind"], shield["chosen_source"], shield["subject"], shield["source_predicate"]["zones"]))
        self.assertEqual(("modify_stats_until_end_of_turn", "$target.0", 0),
                         (stats["op"], stats["card"], stats["power"]))
        amount = ScalarEffectAmountSpec.from_dict(stats["toughness"])
        self.assertEqual((ScalarAmountOrigin.TARGET, "mana_value"), (amount.origin, amount.characteristic))
        for text in (
            card.oracle_text.replace("by target creature", "to target creature"),
            card.oracle_text.replace("target creature", "target permanent"),
            card.oracle_text.replace("+0/+X", "+X/+0"),
            card.oracle_text.replace("its mana value", "your life total"),
            card.oracle_text + " Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                    capability_registry=self.registry).status)

    def test_raw_composition_rejects_alternate_roles_amounts_and_order(self):
        _, program = self.source_program()
        shield, stats = program.effects
        for effects in (
            (stats, shield),
            (shield,),
            (shield, {**stats, "card": "$target.1"}),
            (shield, {**stats, "power": 1}),
            (shield, {**stats, "toughness": ScalarEffectAmountSpec(
                ScalarAmountOrigin.TARGET, characteristic="power").to_dict()}),
        ):
            with self.subTest(effects=effects):
                result = bind_semantic_program_runtime(replace(program, effects=effects),
                    capability_registry=self.registry, profile="commander_review")
                self.assertFalse(result["strict"], result)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def attack_from_b(self, game, *attackers):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {card.ref: "A" for card in attackers}})
        self.assertTrue(result.ok, result.summary)

    def test_actual_combat_preserves_incoming_damage_and_the_original_mana_value_toughness(self):
        game = self.session(61343201)
        engine = game.engine
        target = self.add(engine, "Grizzly Bears", seat="B")
        other = self.add(engine, "Savannah Lions", seat="B")
        blocker = self.add(engine, "Hill Giant")
        spell = self.add(engine, ORIGINAL, zone="hand")
        self.add(engine, "Forest")
        self.assert_closed(game, spell)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.attack_from_b(game, target, other)
        life = game.state.players["A"].life
        self.cast_for(game, "A", spell, [target.ref])
        self.resolve_top(game)
        self.assertEqual((2, 4), self.stats(game, target))
        self.assertEqual((2, 1), self.stats(game, other))
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:A", {"action_id": "block", "blocks": {blocker.ref: target.ref}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: current.state.step == "end_combat")
        victim = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", 3), (victim.zone, victim.marked_damage))
        self.assertEqual(0, game.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(life - 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_zero_mana_value_keeps_prevention_even_when_toughness_is_unchanged(self):
        game = self.session(61343202)
        engine = game.engine
        target = self.add(engine, "Memnite", seat="B")
        spell = self.add(engine, ORIGINAL, zone="hand")
        self.add(engine, "Forest")
        self.assert_closed(game, spell)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.attack_from_b(game, target)
        life = game.state.players["A"].life
        self.cast_for(game, "A", spell, [target.ref])
        self.resolve_top(game)
        self.assertEqual((1, 1), self.stats(game, target))
        self.until(game, lambda current: current.state.step == "end_combat")
        self.assertEqual(life, game.state.players["A"].life)
        self.replay(game, load=True)
