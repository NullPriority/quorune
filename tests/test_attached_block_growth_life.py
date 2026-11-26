"""One actual Aura growth and life trigger follows its original block occurrence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.attack_event_results import ATTACHED_BLOCK_GROWTH_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class AttachedBlockGrowthLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "attached-block-growth-and-life.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "attached-block-growth-and-life-cards.json",
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "unpreventable-damage-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Attachment and two block occurrences", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def _declare_combat_with_gift(self, *, seed, attacker_enchanted):
        game = self.session(seed)
        aura = self.add(game.engine, "Gift of the Woods", zone="hand")
        attacker = self.add(game.engine, "Grizzly Bears", ref="enchanted-combat-attacker")
        attacker.acquired_control_turn_count = -1
        blockers = [self.add(game.engine, "Grizzly Bears", seat="B", ref=f"enchanted-combat-blocker-{index}")
                    for index in range(2)]
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        enchanting = attacker if attacker_enchanted else blockers[0]
        self.assert_closed(game, aura)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", aura, [enchanting.ref])
        self.resolve_top(game)
        self.until(game, lambda match: match.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:A", {"action_id": "attack", "atk": {attacker.ref: "B"}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda match: match.state.pending_decision.kind == "combat.blockers")
        blocks = game.act("pilot:B", {"action_id": "block", "blocks": {card.ref: attacker.ref for card in blockers}})
        self.assertTrue(blocks.ok, blocks.summary)
        self.assertEqual(1, self.trigger_count(game, aura))
        return game, enchanting, bounce

    def test_both_actual_block_roles_buff_the_enchanted_object_once_and_replay(self):
        for attacker_enchanted in (False, True):
            with self.subTest(attacker_enchanted=attacker_enchanted):
                game, recipient, _ = self._declare_combat_with_gift(
                    seed=50990301 + attacker_enchanted, attacker_enchanted=attacker_enchanted,
                )
                self.resolve_top(game)
                self.assertEqual((2, 5), tuple(game.engine._numeric_stat(recipient.object_id, stat)
                                                for stat in ("power", "toughness")))
                self.assertEqual((41, 40), (game.state.players["A"].life, game.state.players["B"].life))
                self.replay(game, load=True)

    def test_actual_recipient_departure_skips_growth_but_still_gains_life_and_replays(self):
        game, recipient, bounce = self._declare_combat_with_gift(seed=50990305, attacker_enchanted=True)
        self.cast_for(game, "B", bounce, [recipient.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[recipient.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(41, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_original_requires_the_closed_attachment_event_and_declared_result_owner(self):
        report, semantic = register_card(self.db, self.registry, "Gift of the Woods")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [item for item in semantic.programs_for_oracle(report["oracle_id"]) if item.event == "creature.blocks"]
        self.assertEqual({"creature.blocks", "creature.becomes_blocked"},
                         {item["event"] for item in program.event_condition["event_subscriptions"]})
        reduced = tuple(cap for cap in program.capability_dependencies if cap != ATTACHED_BLOCK_GROWTH_CAPABILITY)
        without = replace(program, capability_dependencies=reduced,
                          capability_closure=self.registry.closure(reduced, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(without, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"], result)

    def test_missing_growth_compiler_is_killed(self):
        card = self.db.lookup("Gift of the Woods", fuzzy=False)
        original = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", original.status)
        with patch("quorune.rules.attack_event_results.attached_block_growth_effects", side_effect=ValueError("no result")):
            changed = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", changed.status)
