"""The original Equipment prices a private draw from recorded death power."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.fixed_effect_payment import equipped_death_life_draw_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MASK = "Mask of Griselbrand"


class EquippedDeathPowerLifeDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-death-power-life-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-death-power-life-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Quote the last equipped power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_equipment_siblings_and_exact_death_binding(self):
        admitted, semantics = register_card(self.db, self.registry, MASK)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup(MASK, fuzzy=False)
        effects = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (program for program in effects if program.event == "creature.dies")
        self.assertEqual([equipped_death_life_draw_effect()], trigger.effects)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual({"field": "attachments", "op": "contains_any", "value": ["$source.ref"]}, trigger.event_condition)
        for mutated in (replace(trigger, event="permanent.leave"), replace(trigger, event_condition=None)):
            bound = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"])
        for changed in (
            card.oracle_text.replace("its power", "its toughness"),
            card.oracle_text.replace("draw X cards", "draw twice X cards"),
        ):
            changed_ir = compile_oracle_card(
                replace(card, oracle_text=changed), capability_registry=self.registry, capability_profile="commander_review",
            )
            self.assertNotEqual("exact", changed_ir.status)

    def setup_mask(self, seed, responses, *, life=None, growth=False, another_creature=False):
        game = self.session(seed)
        mask = self.add(game.engine, MASK, zone="hand")
        equipped = self.add(game.engine, "Runeclaw Bear", ref="mask-equipped-bear")
        other = self.add(game.engine, "Runeclaw Bear", ref="mask-unattached-bear") if another_creature else None
        spells = {name: self.add(game.engine, name, seat="B", zone="hand") for name in responses}
        if growth:
            spells["Giant Growth"] = self.add(game.engine, "Giant Growth", zone="hand")
        for index in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"mask-swamp-{index}")
        for index in range(2):
            self.add(game.engine, "Plains", seat="B", ref=f"mask-plains-{index}")
        self.add(game.engine, "Forest", zone="library", ref="mask-private-draw")
        for card in (mask, *spells.values()):
            self.assert_closed(game, card)
        if life is not None:
            game.state.players["A"].life = life
        self.prepare(game, mana={"C": 4, "B": 2, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", mask, reserve={"C": 3, "G": 1})
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = (row for row in actions if row["id"].startswith(f"activate:{mask.ref}:") and row.get("target_schema"))
        result = game.act("pilot:A", {
            "action_id": equip["id"], "targets": [equipped.ref], "pay": "auto", "reserve": {"G": 1},
        })
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(equipped.object_id, game.state.cards[mask.object_id].attached_to)
        keywords = game.engine._effective_card_data(game.state.cards[equipped.object_id])["keywords"]
        self.assertTrue({"flying", "lifelink"} <= {keyword.casefold() for keyword in keywords})
        return game, mask, equipped, other, spells

    def price(self, game, amount, *, payable=True):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(amount, decision["life_payment"])
        self.assertEqual(payable, decision["payable"])
        self.assertIn(f"Pay {amount} life", decision["prompt"])
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])

    def pay(self, game, selected):
        result = game.act("pilot:A", {"action_id": "choose", "pay": selected})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_pumped_creature_quotes_five_after_both_permanents_leave(self):
        game, mask, creature, _, spells = self.setup_mask(
            60310601, ("Murder", "Disenchant"), growth=True,
        )
        self.cast_for(game, "A", spells["Giant Growth"], [creature.ref])
        self.resolve_top(game)
        self.cast_for(game, "B", spells["Murder"], [creature.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, mask))
        self.cast_for(game, "B", spells["Disenchant"], [mask.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[mask.object_id].zone)
        self.price(game, 5)
        before = authoritative_state_hash(game.state)
        for seat, fields in (("B", {"pay": True}), ("A", {"pay": 2}), ("A", {"pay": True, "amount": 2})):
            result = game.act("pilot:" + seat, {"action_id": "choose", **fields})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        life = game.state.players["A"].life
        hand = list(game.state.players["A"].zones["hand"])
        draws = list(reversed(game.state.players["A"].zones["library"][-5:]))
        self.pay(game, True)
        self.assertEqual(life - 5, game.state.players["A"].life)
        self.assertEqual(hand + draws, game.state.players["A"].zones["hand"])
        self.assertNotIn("mask-private-draw", json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_unaffordable_payment_can_only_be_declined(self):
        game, _, creature, _, spells = self.setup_mask(60310602, ("Murder",), life=1)
        self.cast_for(game, "B", spells["Murder"], [creature.ref])
        self.resolve_top(game)
        self.price(game, 2, payable=False)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        hand = list(game.state.players["A"].zones["hand"])
        self.pay(game, False)
        self.assertEqual((1, hand), (game.state.players["A"].life, game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_negative_power_allows_zero_payment_and_draws_nothing(self):
        game, _, creature, _, spells = self.setup_mask(60310603, ("Last Gasp",))
        self.cast_for(game, "B", spells["Last Gasp"], [creature.ref])
        self.resolve_top(game)
        trigger = game.state.stack[-1]
        self.assertEqual(("creature.dies", -1), (trigger.context["event"], trigger.context["power"]))
        self.price(game, 0)
        game = self.replay(game, load=True)
        life = game.state.players["A"].life
        hand = list(game.state.players["A"].zones["hand"])
        self.pay(game, True)
        self.assertEqual((life, hand), (game.state.players["A"].life, game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_unattached_death_and_equipped_exile_do_not_trigger(self):
        game, mask, creature, other, spells = self.setup_mask(
            60310604, ("Murder", "Swords to Plowshares"), another_creature=True,
        )
        self.cast_for(game, "B", spells["Murder"], [other.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, mask))
        self.cast_for(game, "B", spells["Swords to Plowshares"], [creature.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[creature.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, mask))
        self.replay(game, load=True)
