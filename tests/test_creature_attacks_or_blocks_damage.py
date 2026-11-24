"""Native evidence for one damage trigger over both declared combat participants."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Powerstone Minefield"


class CreatureAttacksOrBlocksDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "creature-attacks-or-blocks-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/creature-attacks-or-blocks-damage-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damage to combat participants", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return program

    def test_whole_original_uses_one_untargeted_program_with_two_combat_occurrences(self):
        program = self.program()
        subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
        self.assertEqual({"creature.attacks", "creature.blocks"}, {row.event for row in subscriptions.subscriptions})
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        self.assertEqual(({"op": "damage", "source": "$source", "target": "$event.permanent_object", "amount": 2},),
                         tuple(dict(effect) for effect in program.effects))

    def test_raw_combat_program_requires_both_branches_and_the_original_participant(self):
        program = self.program()
        for altered in (
            replace(program, event_condition=None),
            replace(program, active_zone="graveyard"),
            replace(program, effects=({'op': 'damage', 'source': '$source', 'target': '$context.card', 'amount': 2},)),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_two_attackers_and_a_single_blocker_resolve_their_own_damage_once(self):
        game = self.session(50933031)
        observer = self.add(game.engine, ORIGINAL, zone="hand")
        spiders = [self.add(game.engine, "Giant Spider", ref=f"minefield-attacker-{n}") for n in range(2)]
        wall = self.add(game.engine, "Wall of Glare", seat="B")
        remove = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B", ref="minefield-dis-enchant-1")
        self.add(game.engine, "Plains", seat="B", ref="minefield-dis-enchant-2")
        for card in (observer, *spiders, wall, remove):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", observer)
        self.resolve_top(game)
        self.attack(game, [(spider, "B") for spider in spiders])
        self.assertEqual(2, self.trigger_count(game, observer))
        game = self.replay(game, load=True)
        for _ in spiders:
            self.resolve_top(game)
        self.assertEqual([2, 2], [game.state.cards[spider.object_id].marked_damage for spider in spiders])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {wall.ref: [spider.ref for spider in spiders]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, self.trigger_count(game, observer))
        self.cast_for(game, "B", remove, [observer.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[observer.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[wall.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_blinked_attacker_keeps_its_new_incarnation_outside_the_waiting_damage(self):
        game = self.session(50833041)
        observer = self.add(game.engine, ORIGINAL, seat="C")
        spider = self.add(game.engine, "Giant Spider")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Plains")
        for card in (observer, spider, blink):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(spider, "B")])
        self.assertEqual(1, self.trigger_count(game, observer))
        before = game.state.cards[spider.object_id].logical_object_id
        self.cast_for(game, "A", blink, [spider.ref])
        self.resolve_top(game)
        self.assertNotEqual(before, game.state.cards[spider.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[spider.object_id]
        self.assertEqual(("battlefield", 0), (current.zone, current.marked_damage))
        self.replay(game, load=True)
