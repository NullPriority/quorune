"""Committed noncombat damage excess and its locked resolution follow-up."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.excess_damage import EXCESS_TRAMPLE_CAPABILITY, committed_recipient_excess
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Overwhelming Victory"
FIXTURE = ROOT / "tests/fixtures/committed-excess-damage-cards.json"


class CommittedExcessDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        db = Path(cls.temp.name) / "committed-excess.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", FIXTURE], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        command = "Generic Bound Commander"
        cls.deck = DeckDefinition("Committed excess", [
            DeckEntry(command, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [command])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_exact_excess_instruction_rejects_wrong_target_or_result(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        ir = compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(EXCESS_TRAMPLE_CAPABILITY, node.capability_dependencies)
        for old, new in (("to target creature", "to any target"),
                         ("+X/+0", "+X/+X"), ("excess damage", "prevented damage")):
            with self.subTest(new=new):
                self.assertNotEqual("exact", compile(
                    replace(card, oracle_text=card.oracle_text.replace(old, new)),
                ).status)

    def test_local_excess_reads_pre_damage_threshold_and_actual_multitype_recipient(self):
        card = SimpleNamespace(object_id="recipient", logical_object_id="recipient@1", zone="battlefield",
                               marked_damage=1, deathtouch_damage=False, counters={"loyalty": 2})
        host = SimpleNamespace(state=SimpleNamespace(cards={"recipient": card}),
                               _effective_card_data=lambda _c: {"type_line": "Creature Planeswalker"},
                               _type_parts=lambda _: ({"creature", "planeswalker"}, set(), set()),
                               _numeric_stat=lambda _id, _stat: 4)
        event = SimpleNamespace(target_kind="permanent", target_object_id="recipient", target_logical_object_id="recipient@1",
                                dealt_amount=5, source_keywords=())
        self.assertEqual([3], [row.amount for row in committed_recipient_excess(host, (event,))])
        event.source_keywords = ("deathtouch",)
        self.assertEqual([4], [row.amount for row in committed_recipient_excess(host, (event,))])
        event.dealt_amount = 0
        self.assertEqual((), committed_recipient_excess(host, (event,)))
        event.dealt_amount = 5
        event.target_logical_object_id = "recipient@0"
        with self.assertRaises(ValueError):
            committed_recipient_excess(host, (event,))

    def test_actual_victory_uses_marked_damage_and_keeps_trample_when_damage_is_prevented(self):
        for seed, shield, delta in ((12010091, False, 4), (12010092, True, 0)):
            with self.subTest(shield=shield):
                game = self.session(seed)
                game.state.config.semantic_policy = "trusted_only"
                spell = self.add(game.engine, CARD, zone="hand")
                team = self.add(game.engine, "Grizzly Bears", ref="victory-team")
                target = self.add(game.engine, "Grizzly Bears", seat="B", ref="victory-original-target")
                other = self.add(game.engine, "Grizzly Bears", seat="B", ref="victory-opposing-nontarget")
                target.marked_damage = 1
                if shield:
                    target.counters["shield"] = 1
                self.assert_closed(game, spell)
                self.prepare(game, mana={"C": 4, "R": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [target.ref])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((2 + delta, 2), tuple(
                    game.engine._numeric_stat(team.object_id, key) for key in ("power", "toughness")
                ))
                self.assertIn("trample", game.engine._combat_keywords(game.state.cards[team.object_id]))
                self.assertNotIn("trample", game.engine._combat_keywords(game.state.cards[other.object_id]))
                self.assertEqual("battlefield" if shield else "graveyard", game.state.cards[target.object_id].zone)
                self.replay(game, load=True)
