"""Native evidence for Bushido equal to the current attacking creature count."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import AbilityFragmentError, CombatKeywordTriggerKind, CombatKeywordTriggerSpec
from quorune.block_transitions import BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Fumiko the Lowblood"


class BushidoCurrentAttackingCreaturesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "bushido-current-attacking-creatures.sqlite3"
        fixture = ROOT / "tests/fixtures/bushido-current-attacking-creatures-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Bushido current attacking creatures", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_complete_original_keeps_the_dynamic_bushido_and_opposing_attack_requirement(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(programs))
        descriptors = [descriptor for program in programs for descriptor in program.handlers]
        handler, = [descriptor for descriptor in descriptors if descriptor["handler_id"] == "ability.trigger.bushido.v1"]
        fragment = CombatKeywordTriggerSpec.from_dict(handler["fragment"]["value"])
        self.assertIs(CombatKeywordTriggerKind.BUSHIDO_ATTACKING_CREATURE_COUNT, fragment.kind)
        for changed in (
            card.oracle_text.replace("attacking creatures", "blocking creatures"),
            card.oracle_text.replace("attacking creatures", "attacking creatures you control"),
            card.oracle_text.replace("attacking creatures.", "attacking creatures. Draw a card."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_dynamic_keyword_marker_cannot_supply_a_fixed_bonus_or_change_ordinary_records(self):
        fixed = {"schema_version": 1, "kind": "bushido", "amount": 3}
        self.assertEqual(fixed, CombatKeywordTriggerSpec.from_dict(fixed).to_dict())
        dynamic = CombatKeywordTriggerSpec(CombatKeywordTriggerKind.BUSHIDO_ATTACKING_CREATURE_COUNT, 1)
        self.assertEqual(dynamic, CombatKeywordTriggerSpec.from_dict(dynamic.to_dict()))
        with self.assertRaises(AbilityFragmentError):
            CombatKeywordTriggerSpec.from_dict({**dynamic.to_dict(), "amount": 2})

    def next_blockers(self, game, seat):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers"
                   and seat in current.state.pending_decision.actors)

    def bushido(self, game, source):
        def occurrences(current):
            return [item for item in current.state.stack if item.semantic_key == BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY
                    and item.source_object_id == source.object_id]
        self.until(game, lambda current: bool(occurrences(current)))
        trigger, = occurrences(game)
        self.assertIn("Bushido X", trigger.label)
        self.assertFalse(trigger.targets)
        return trigger

    def test_actual_blocker_counts_attackers_at_resolution_after_a_real_response(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                game = self.session(70245010 + remove)
                source = self.add(game.engine, ORIGINAL)
                first = self.add(game.engine, "Grizzly Bears", seat="B", ref="fumiko-first-attacker")
                second = self.add(game.engine, "Grizzly Bears", seat="B", ref="fumiko-other-defender-attacker")
                response = self.add(game.engine, "Swords to Plowshares", zone="hand")
                self.add(game.engine, "Plains")
                for card in (first, second):
                    card.acquired_control_turn_count = -1
                for card in (source, first, second, response):
                    self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B")
                self.checkpoint(game)
                baseline = self.stats(game, source)
                self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
                before = authoritative_state_hash(game.state)
                omitted = game.act("pilot:B", {"action_id": "attack", "attackers": {first.ref: "A"}})
                self.assertFalse(omitted.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                attacked = game.act("pilot:B", {"action_id": "attack", "attackers": {first.ref: "A", second.ref: "C"}})
                self.assertTrue(attacked.ok, attacked.summary)
                self.next_blockers(game, "A")
                blocked = game.act("pilot:A", {"action_id": "block", "blocks": {source.ref: first.ref}})
                self.assertTrue(blocked.ok, blocked.summary)
                self.bushido(game, source)
                self.assertEqual(2, len(game.state.combat.attackers))
                if remove:
                    self.cast_for(game, "A", response, [second.ref])
                    self.resolve_top(game)
                    self.assertEqual("exile", game.state.cards[second.object_id].zone)
                    self.assertEqual({first.object_id}, set(game.state.combat.attackers))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                bonus = 1 if remove else 2
                self.assertEqual(tuple(value + bonus for value in baseline), self.stats(game, source))
                self.replay(game, load=True)

    def test_actual_blocked_source_triggers_once_when_two_creatures_block_it(self):
        game = self.session(70245020)
        source = self.add(game.engine, ORIGINAL)
        attacking = self.add(game.engine, "Grizzly Bears", ref="fumiko-other-attacker")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="fumiko-first-blocker")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="fumiko-second-blocker")
        for card in (source, attacking):
            card.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        baseline = self.stats(game, source)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attacked = game.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B", attacking.ref: "C"}})
        self.assertTrue(attacked.ok, attacked.summary)
        self.next_blockers(game, "B")
        blocked = game.act("pilot:B", {"action_id": "block", "blocks": {first.ref: source.ref, second.ref: source.ref}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.bushido(game, source)
        self.assertEqual(2, len(game.state.combat.blockers[source.object_id]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(tuple(value + 2 for value in baseline), self.stats(game, source))
        self.assertFalse(any(item.semantic_key == BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY for item in game.state.stack))
        self.replay(game, load=True)
