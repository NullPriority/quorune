"""Native evidence for optional source counters from damage an opponent dealt."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Retaliator Griffin"


class OpponentDamageOptionalSourceCountersTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-damage-optional-source-counters.sqlite3"
        fixture = ROOT / "tests/fixtures/opponent-damage-optional-source-counters-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional counters from opposing damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "damage.dealt"]
        return card, program

    def test_original_admits_one_untargeted_optional_counter_result_and_flying(self):
        card, program = self.program()
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        wrapper, = program.effects
        self.assertEqual(("offer_optional_counter_placement", "$controller"), (wrapper["op"], wrapper["player"]))
        child = wrapper["effect"]
        self.assertEqual(("place_counters", "$source.zone_object", "+1/+1", "$source"),
                         (child["op"], child["card"], child["counter"], child["source"]))
        self.assertEqual(ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT),
                         ScalarEffectAmountSpec.from_dict(child["amount"]))
        for changed in (
            card.oracle_text.replace("an opponent controls", "you control"),
            card.oracle_text.replace("damage to you", "damage to an opponent"),
            card.oracle_text.replace("this creature", "target creature"),
            card.oracle_text.replace("that many", "two"),
            card.oracle_text + " Draw a card.",
        ):
            ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_raw_program_cannot_drop_attribution_optionality_or_the_sealed_original(self):
        _, program = self.program()
        wrapper, = program.effects
        child = wrapper["effect"]
        for changed in (
            replace(program, event_condition=None),
            replace(program, effects=(child,)),
            replace(program, effects=({**wrapper, "player": "$target.0"},)),
            replace(program, effects=({**wrapper, "effect": {**child, "card": "$source"}},)),
            replace(program, effects=({**wrapper, "effect": {**child, "amount": 2}},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def activate(self, game, actor, source, **command):
        self.priority(game, actor)
        rows = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + actor, {"action_id": action, "pay": "auto", **command})
        self.assertTrue(result.ok, result.summary)

    def choose_optional(self, game, actor, choice, *, replay=False):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (decision.kind, decision.actors))
        if replay:
            foreign = "A" if actor == "B" else "B"
            before = authoritative_state_hash(game.state)
            denied = game.act("pilot:" + foreign, {"action_id": "choose", "choice": choice})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            game = self.replay(game, load=True)
        result = game.act("pilot:" + actor, {"action_id": "choose", "choice": choice})
        self.assertTrue(result.ok, result.summary)
        return game

    def test_actual_borrowed_own_dealer_ignores_owner_and_opposing_spell_offers_exact_counters(self):
        for mode in ("put", "decline", "blink"):
            with self.subTest(mode=mode):
                game = self.session(3973300 + len(mode))
                griffin = self.add(game.engine, ORIGINAL)
                rod = self.add(game.engine, "Rod of Ruin", seat="B")
                game.engine.change_control(rod.object_id, "A", reason="Prepare borrowed damage dealer")
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                blink = self.add(game.engine, "Cloudshift", zone="hand") if mode == "blink" else None
                for card in (griffin, rod, shock, blink):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana={"C": 3, "W": 4})
                game.state.players["B"].mana_pool["R"] = 1
                self.checkpoint(game)
                self.assertIn("flying", game.engine._combat_keywords(griffin))
                life = game.state.players["A"].life
                self.activate(game, "A", rod, targets=["A"])
                self.resolve_top(game)
                self.assertEqual(life - 1, game.state.players["A"].life)
                self.assertEqual(0, self.trigger_count(game, griffin))
                self.cast_for(game, "B", shock, ["A"])
                self.resolve_top(game)
                self.assertEqual(life - 3, game.state.players["A"].life)
                self.assertEqual(1, self.trigger_count(game, griffin))
                queued = game.state.stack[-1]
                self.assertEqual((griffin.object_id, "A"), (queued.source_object_id, queued.controller))
                self.assertFalse(queued.targets)
                if blink is not None:
                    original = griffin.logical_object_id
                    self.cast_for(game, "A", blink, [griffin.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(original, game.state.cards[griffin.object_id].logical_object_id)
                    self.assertIsNone(self.resolve(game))
                else:
                    game = self.choose_optional(game, "A", mode, replay=True)
                current = game.state.cards[griffin.object_id]
                self.assertEqual(2 if mode == "put" else 0, current.counters.get("+1/+1", 0))
                self.replay(game, load=True)

    def test_actual_cycled_spell_deals_damage_as_an_opponents_graveyard_card(self):
        game = self.session(3973310)
        griffin = self.add(game.engine, ORIGINAL)
        blast = self.add(game.engine, "Solar Blast", seat="B", zone="hand")
        for card in (griffin, blast):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "R": 2})
        self.checkpoint(game)
        life = game.state.players["A"].life
        before = len(game.state.players["B"].draw_history)
        self.activate(game, "B", blast)
        self.assertEqual("graveyard", game.state.cards[blast.object_id].zone)
        self.target(game, "B", ["A"])
        self.assertEqual(["activated_ability", "triggered_ability"], [item.kind for item in game.state.stack])
        game = self.choose_optional(game, "B", "apply")
        self.assertEqual(life - 1, game.state.players["A"].life)
        self.assertEqual(1, self.trigger_count(game, griffin))
        game = self.choose_optional(game, "A", "put", replay=True)
        self.assertEqual(1, game.state.cards[griffin.object_id].counters.get("+1/+1", 0))
        self.assertEqual(before, len(game.state.players["B"].draw_history))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, len(game.state.players["B"].draw_history))
        self.replay(game, load=True)

    def test_actual_displaced_borrowed_dealer_keeps_its_last_battlefield_controller(self):
        game = self.session(3973320)
        griffin = self.add(game.engine, ORIGINAL)
        rod = self.add(game.engine, "Rod of Ruin")
        game.engine.change_control(rod.object_id, "B", reason="Prepare opposing borrowed dealer")
        original = rod.logical_object_id
        answer = self.add(game.engine, "Disenchant", zone="hand")
        for card in (griffin, rod, answer):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "C": 1})
        game.state.players["B"].mana_pool["C"] = 3
        self.checkpoint(game)
        self.activate(game, "B", rod, targets=["A"])
        self.cast_for(game, "A", answer, [rod.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[rod.object_id].zone)
        self.resolve_top(game)
        damage, = game.engine._current_turn_history("player_damaged")
        self.assertEqual(("B", original, "A", 1),
                         (damage.actor, damage.object_incarnation, damage.target, damage.amount))
        self.assertEqual(1, self.trigger_count(game, griffin))
        game = self.choose_optional(game, "A", "put", replay=True)
        self.assertEqual(1, game.state.cards[griffin.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)
