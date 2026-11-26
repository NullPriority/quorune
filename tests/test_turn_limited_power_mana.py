"""Native source-power mana and noncreature cast trigger evidence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_condition_model import ActivationConditionKind, ActivationLimit
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_quantity_mana_abilities import PublicQuantityActivatedManaAbilitySpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class TurnLimitedPowerManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "turn-limited-power-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/turn-limited-power-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Turn-limited power mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(
            card, capability_registry=self.registry, capability_profile="commander_review",
        )

    def test_actual_two_abilities_have_closed_descriptors_and_narrow_source_grammar(self):
        card = self.db.lookup("Vivi Ornitier", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, trigger = ir.faces[0].nodes
        self.assertEqual("mana_ability", mana.kind)
        self.assertFalse(mana.effects)
        handler, = mana.handlers
        spec = PublicQuantityActivatedManaAbilitySpec.from_dict(handler["ability"])
        self.assertEqual(("R", "U"), spec.output.colors)
        self.assertEqual("combination", spec.output.selection)
        self.assertEqual("power", spec.output.amount.characteristic)
        self.assertEqual("{0}", spec.costs.cost_text)
        self.assertIs(ActivationLimit.ONCE_PER_TURN, spec.costs.activation_limit)
        self.assertEqual((ActivationConditionKind.CONTROLLERS_TURN,),
                         tuple(condition.kind for condition in spec.costs.activation_conditions))
        self.assertFalse(spec.costs.tap_source)
        self.assertEqual(card.oracle_text.splitlines()[1], trigger.text)
        self.assertEqual("spell.cast", trigger.event)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual((
            {"op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1", "amount": 1, "source": "$source"},
            {"op": "damage_each_opponent", "source": "$source", "amount": 1},
        ), trigger.effects)
        for old, new in (
            ("during your turn and only once", "during any end step and only once"),
            ("on Vivi Ornitier and it", "on another creature and it"),
            ("and it deals 1 damage", "and it deals 2 damage"),
            ("Whenever you cast a noncreature spell", "Whenever you cast a creature spell"),
        ):
            with self.subTest(changed_span=old):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", self.compile(changed).status)

    def activation_offer(self, game, source):
        packet = game.packet("pilot:A", full=True)["decision"]
        return next(
            (row for row in packet["ctx"]["legal"]["actions"]
             if row["id"].startswith(f"activate:{source.ref}:")),
            None,
        )

    def pool_colors(self, game):
        return tuple(game.state.players["A"].mana_pool.get(color, 0) for color in ("U", "R"))

    def test_actual_mixed_mana_uses_current_power_and_pays_two_spells_through_replay(self):
        game = self.session(735433001)
        source = self.add(game.engine, "Vivi Ornitier")
        source.counters["+1/+1"] = 1
        creature = self.add(game.engine, "Ornithopter", zone="hand")
        first_bolt = self.add(game.engine, "Lightning Bolt", zone="hand", ref="initial-bolt")
        second_bolt = self.add(game.engine, "Lightning Bolt", zone="hand", ref="funded-bolt")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        self.add(game.engine, "Mountain")
        for card in (source, creature, first_bolt, second_bolt, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

        self.cast_for(game, "A", creature)
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual(1, self.activation_offer(game, source)["choice_schema"]["mana_output"]["total"])
        self.cast_for(game, "A", first_bolt, ["C"])
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual([40, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual({"+1/+1": 2}, game.state.cards[source.object_id].counters)
        first_item, = game.state.stack
        self.priority(game, "A")
        offer = self.activation_offer(game, source)
        self.assertEqual(2, offer["choice_schema"]["mana_output"]["total"])
        self.assertEqual(["R", "U"], offer["choice_schema"]["mana_output"]["allowed_colors"])
        before = authoritative_state_hash(game.state)
        for actor, bundle in (("A", {"G": 1, "U": 1}), ("A", {"R": 1}), ("B", {"U": 1, "R": 1})):
            result = game.act("pilot:" + actor, {"action_id": offer["id"], "mana_output": bundle})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))

        result = game.act("pilot:A", {"action_id": offer["id"], "mana_output": {"U": 1, "R": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([first_item.ref], [item.ref for item in game.state.stack])
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual((1, 1), self.pool_colors(game))
        game = self.replay(game, load=True)
        self.assertIsNone(self.activation_offer(game, source))
        before = authoritative_state_hash(game.state)
        second_attempt = game.act("pilot:A", {"action_id": offer["id"], "mana_output": {"U": 2}})
        self.assertFalse(second_attempt.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.cast_for(game, "A", bounce, [bear.ref])
        self.assertEqual((0, 1), self.pool_colors(game))
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[bear.object_id].zone)
        self.cast_for(game, "A", second_bolt, ["D"])
        self.assertEqual((0, 0), self.pool_colors(game))
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual({"+1/+1": 4}, game.state.cards[source.object_id].counters)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual([40, 37, 34, 34], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_off_turn_activation_is_unavailable_before_any_use(self):
        game = self.session(735433002)
        source = self.add(game.engine, "Vivi Ornitier")
        source.counters["+1/+1"] = 2
        self.assert_closed(game, source)
        self.prepare(game, active="B")
        self.checkpoint(game)
        self.priority(game, "A")
        self.assertIsNone(self.activation_offer(game, source))
        self.assertEqual((0, 0), self.pool_colors(game))
        self.replay(game, load=True)

    def test_actual_cast_trigger_survives_the_countered_spell_with_or_without_source(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(735433010 + departed)
                source = self.add(game.engine, "Vivi Ornitier")
                bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
                counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
                self.add(game.engine, "Mountain")
                for i in range(2 + departed):
                    self.add(game.engine, "Island", seat="B", ref=f"response-island-{i}")
                removal = self.add(game.engine, "Unsummon", seat="B", zone="hand") if departed else None
                self.assert_closed(game, source)
                self.prepare(game)
                self.checkpoint(game)

                self.cast_for(game, "A", bolt, ["C"])
                spell, trigger = game.state.stack
                self.assertEqual(source.object_id, trigger.source_object_id)
                if removal is not None:
                    self.cast_for(game, "B", removal, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                self.cast_for(game, "B", counter, [spell.ref])
                self.resolve_top(game)
                self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])
                self.assertEqual("graveyard", game.state.cards[bolt.object_id].zone)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(0 if departed else 1, game.state.cards[source.object_id].counters.get("+1/+1", 0))
                self.assertEqual([40, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)
