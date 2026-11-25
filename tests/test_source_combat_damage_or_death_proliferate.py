"""One printed multi-event ability produces one proliferate per occurrence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_public_multi_event_bindings import fixed_public_multi_event_binding_spec
from quorune.counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Park Heights Maverick"


class SourceCombatDamageOrDeathProliferateTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "source-combat-damage-or-death-proliferate.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "source-combat-damage-or-death-proliferate-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source damage and death proliferation", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_registers_one_exact_multi_event_proliferate_ability(self):
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        trigger, = (program for program in programs if program.effects == [
            {"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1},
        ])
        self.assertEqual({"continuous", "combat.declaration", "damage.dealt"}, {program.event for program in programs})
        self.assertIn("trigger.subscription.fixed_multi_event", trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        subscriptions = FixedEventSubscriptionSet.from_condition(trigger.event_condition)
        self.assertEqual(("damage.dealt", "creature.dies"), tuple(row.event for row in subscriptions.subscriptions))
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"},
                         subscriptions.subscription_for("creature.dies").condition)
        from quorune.rules.node_capability_shapes import single_proliferate_node_capabilities
        self.assertEqual(
            ("counter.producer.proliferate",),
            single_proliferate_node_capabilities(
                effects=({"op": "proliferate"},), target_schema=None, mechanic_ids=("proliferate",),
            ),
        )
        for invalid in (False, 0, 2, "1"):
            self.assertEqual(
                (),
                single_proliferate_node_capabilities(
                    effects=({"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: invalid},),
                    target_schema=None, mechanic_ids=("proliferate",),
                ),
            )
        damage = subscriptions.subscription_for("damage.dealt").condition["all"]
        self.assertIn({"field": "target_kind", "op": "eq", "value": "player"}, damage)
        self.assertIn({"field": "combat", "op": "truthy", "value": True}, damage)
        for unsupported in (
            "Whenever this creature deals damage to a player or dies, proliferate.",
            "Whenever this creature deals combat damage to a planeswalker or dies, proliferate.",
            "Whenever this creature deals combat damage to a player or dies, draw a card.",
        ):
            self.assertIsNone(fixed_public_multi_event_binding_spec(unsupported, card_name=ORIGINAL))

    def choose_proliferation(self, game, target, *, excluded=None, private=None):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        allowed = packet["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertIn(target.ref, allowed)
        self.assertEqual(sorted(allowed), list(allowed))
        if excluded is not None:
            self.assertNotIn(excluded.ref, allowed)
        if private is not None:
            self.assertNotIn(private.ref, json.dumps(packet))
        command = {"action_id": "choose", "objects": [target.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return game

    def test_actual_combat_damage_and_later_death_trigger_the_same_printed_ability_once_each(self):
        game = self.session(70134021)
        source = self.add(game.engine, ORIGINAL)
        target = self.add(game.engine, "Elvish Mystic", seat="C")
        target.counters["+1/+1"] = 1
        private = self.add(game.engine, "Humble", seat="C", zone="hand", ref="maverick-unrelated-private")
        destroy = self.add(game.engine, "Doom Blade", zone="hand")
        for index in range(2):
            self.add(game.engine, "Swamp", ref=f"maverick-swamp-{index}")
        source.acquired_control_turn_count = -1
        for card in (source, destroy):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[source.object_id].counters["+1/+1"])

        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        damage = game.state.stack[-1]
        self.assertEqual("damage.dealt", damage.context["event"])
        self.assertEqual(37, game.state.players["B"].life)
        game = self.choose_proliferation(game, target, private=private)
        self.assertEqual(2, game.state.cards[target.object_id].counters["+1/+1"])
        self.assertEqual(0, self.trigger_count(game, source))
        self.until(game, lambda current: current.state.phase == "postcombat_main")

        self.cast_for(game, "A", destroy, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, source))
        death = game.state.stack[-1]
        self.assertEqual((damage.semantic_key, "creature.dies"), (death.semantic_key, death.context["event"]))
        game = self.choose_proliferation(game, target, excluded=source, private=private)
        self.assertEqual(3, game.state.cards[target.object_id].counters["+1/+1"])
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_fight_other_death_and_planeswalker_damage_do_not_offer_proliferation(self):
        game = self.session(70134022)
        source = self.add(game.engine, ORIGINAL)
        other = self.add(game.engine, "Elvish Mystic", seat="B")
        planeswalker = self.add(game.engine, "Jace Beleren", seat="B")
        planeswalker.counters["loyalty"] = 3
        fight = self.add(game.engine, "Prey Upon", zone="hand")
        source.acquired_control_turn_count = -1
        for card in (source, fight, planeswalker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", fight, [source.ref, other.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))
        self.attack(game, [(source, planeswalker.ref)])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(1, game.state.cards[planeswalker.object_id].counters["loyalty"])
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)
