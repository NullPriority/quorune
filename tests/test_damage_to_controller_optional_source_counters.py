"""Native evidence for optional original-source counters after any dealer damages its controller."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Blood Hound"
ADJACENT = "Retaliator Griffin"


class DamageToControllerOptionalSourceCountersTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "damage-to-controller-optional-source-counters.sqlite3"
        fixture = ROOT / "tests/fixtures/damage-to-controller-optional-source-counters-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Any-source damage and optional counters", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = [item for item in programs if item.event == "damage.dealt"]
        return card, trigger, programs

    def test_original_uses_one_any_source_player_damage_occurrence_and_preserves_cleanup(self):
        card, program, programs = self.program(ORIGINAL)
        self.assertIn("step.begin", {item.event for item in programs})
        self.assertIsNone(program.target_schema)
        self.assertEqual({"all": [
            {"field": "target_kind", "op": "eq", "value": "player"},
            {"field": "target", "op": "eq", "value": "$source.controller"},
        ]}, program.event_condition)
        wrapper, = program.effects
        self.assertEqual(("offer_optional_counter_placement", "$controller"), (wrapper["op"], wrapper["player"]))
        effect = wrapper["effect"]
        self.assertEqual(("$source.zone_object", "+1/+1"), (effect["card"], effect["counter"]))
        self.assertEqual(ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT),
                         ScalarEffectAmountSpec.from_dict(effect["amount"]))
        for altered in (
            card.oracle_text.replace("you're dealt damage", "you lose life"),
            card.oracle_text.replace("this creature.", "target creature.", 1),
            card.oracle_text.replace("that many +1/+1 counters", "that many charge counters"),
            card.oracle_text.replace("on this creature.", "on this creature. Rewrite history.", 1),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, altered)

    def test_raw_roles_cannot_switch_counter_scopes_or_drop_the_current_ability(self):
        _, hound, _ = self.program(ORIGINAL)
        _, griffin, _ = self.program(ADJACENT)
        for altered in (replace(hound, event_condition=griffin.event_condition),
                        replace(griffin, event_condition=hound.event_condition),
                        replace(hound, event_condition=None),
                        replace(hound, coverage=tuple(tag for tag in hound.coverage
                                                     if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE))):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def put_counters(self, game, *, actor="A"):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        applied = game.act("pilot:" + actor, {"action_id": "choose", "choice": "put"})
        self.assertTrue(applied.ok, applied.summary)

    def test_actual_own_dealer_puts_committed_counters_then_the_real_end_step_removes_them(self):
        game = self.session(12040360)
        hound = self.add(game.engine, ORIGINAL)
        griffin = self.add(game.engine, ADJACENT)
        bear = self.add(game.engine, "Grizzly Bears")
        backlash = self.add(game.engine, "Backlash", zone="hand")
        loss = self.add(game.engine, "Sign in Blood", zone="hand")
        for card in (hound, griffin, bear, backlash, loss):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 3, "R": 1})
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.cast_for(game, "A", loss, ["A"])
        self.resolve_top(game)
        self.assertEqual(life - 2, game.state.players["A"].life)
        self.assertEqual(0, self.trigger_count(game, hound))
        self.cast_for(game, "A", backlash, [bear.ref])
        self.resolve_top(game)
        self.assertEqual(life - 4, game.state.players["A"].life)
        self.assertEqual(1, self.trigger_count(game, hound))
        self.assertEqual(0, self.trigger_count(game, griffin))
        game = self.replay(game, load=True)
        self.put_counters(game)
        self.assertEqual(2, game.state.cards[hound.object_id].counters.get("+1/+1", 0))
        self.until(game, lambda current: current.state.step == "end_step" and self.trigger_count(current, hound) == 1)
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[hound.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_actual_simultaneous_lethal_hound_still_triggers_and_cannot_place_counters(self):
        game = self.session(12040370)
        hound = self.add(game.engine, ORIGINAL)
        fallout = self.add(game.engine, "Volcanic Fallout", zone="hand")
        for card in (hound, fallout):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", fallout)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[hound.object_id].zone)
        self.assertEqual([38, 38, 38, 38], [player.life for player in game.state.players.values()])
        self.assertEqual(1, self.trigger_count(game, hound))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[hound.object_id].counters)
        self.replay(game, load=True)

    def test_actual_ability_removal_suppresses_each_variant_independently(self):
        for removed in (ORIGINAL, ADJACENT):
            with self.subTest(removed=removed):
                game = self.session(12040380 + len(removed))
                hound = self.add(game.engine, ORIGINAL)
                griffin = self.add(game.engine, ADJACENT)
                lignify = self.add(game.engine, "Lignify", seat="B", zone="hand")
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                for card in (hound, griffin, lignify, shock):
                    self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B", mana={"C": 1, "G": 1, "R": 1})
                self.checkpoint(game)
                muted, remaining = (hound, griffin) if removed == ORIGINAL else (griffin, hound)
                self.cast_for(game, "B", lignify, [muted.ref], reserve={"R": 1})
                self.resolve_top(game)
                self.assertIn("Treefolk", game.engine._effective_card_data(muted)["type_line"])
                self.cast_for(game, "B", shock, ["A"])
                self.resolve_top(game)
                self.assertEqual(0, self.trigger_count(game, muted))
                self.assertEqual(1, self.trigger_count(game, remaining))
                game = self.replay(game, load=True)
                self.put_counters(game)
                self.assertEqual(2, game.state.cards[remaining.object_id].counters.get("+1/+1", 0))
                self.replay(game, load=True)
