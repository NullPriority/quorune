"""The final chapter privately chooses nonlands and publicly awards one token per matched card."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.colored_hand_reveal_model import MATCHED_MANA_REVEAL_CAPABILITY, MatchingManaValueHandRevealSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Vault 21: House Gambit"
PROFILE = "commander_review"


class VaultManaValueRevealTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "vault-21.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "entry-discard-and-death-reanimation-cards.json",
            fixtures / "vault-21-matched-reveal-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Vault 21", [
            DeckEntry(commander,1,"commander"), DeckEntry("Plains",30),
        ], [commander])

    def test_closes_the_actual_chapter_and_rejects_other_reveal_or_result_shapes(self):
        card = self.db.lookup(NAME,fuzzy=False)
        report, semantics = register_card(self.db,self.registry,NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        chapter, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if MATCHED_MANA_REVEAL_CAPABILITY in program.capability_dependencies)
        self.assertEqual("saga.chapter.3", chapter.event)
        self.assertEqual((MatchingManaValueHandRevealSpec().effect(),), tuple(chapter.effects))
        changed = replace(chapter,event="saga.chapter.2")
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        for before, after in (("up to five", "up to six"), ("nonland cards", "cards"),
                              ("mana value", "card type"), ("a Treasure token", "a Food token")):
            source = replace(card,oracle_text=card.oracle_text.replace(before,after,1))
            with self.subTest(after=after):
                self.assertNotEqual("exact",compile_oracle_card(
                    source,capability_registry=self.registry,capability_profile=PROFILE,
                ).status)

    def resolve_choice(self, *, select_cards):
        game = self.session(47840301 + int(select_cards))
        saga = self.add(game.engine,NAME)
        saga.counters["lore"] = 2
        chosen = [self.add(game.engine,"Grizzly Bears",zone="hand",ref=f"two-mana-{index}")
                  for index in range(3)]
        chosen.extend(self.add(game.engine,"Unsummon",zone="hand",ref=f"one-mana-{index}")
                      for index in range(2))
        hidden = self.add(game.engine,"Murder",zone="hand",ref="unselected-nonland")
        land = self.add(game.engine,"Forest",zone="hand",ref="excluded-land")
        foreign = self.add(game.engine,"Unsummon",zone="hand",seat="B",ref="opposing-card")
        included = {card.object_id for card in (*chosen,hidden,land,foreign)}
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                if object_id not in included:
                    game.engine.move_card(object_id,"library",semantic_events=False,log=False)
        previous = game.state.turn_order[game.state.turn_order.index("A") - 1]
        game.state.last_normal_turn_player = previous
        self.assert_closed(game,saga)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game,actor=previous,active=previous)
        self.checkpoint(game)
        self.until(game,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=="semantic.choice")
        self.assertEqual(["A"],game.state.pending_decision.actors)
        self.assertIsNone(game.packet("pilot:B",full=True)["decision"])
        game = self.replay(game,load=True)
        selected_refs = [card.ref for card in chosen] if select_cards else []
        command = {"action_id":"choose","cards":selected_refs}
        before = authoritative_state_hash(game.state)
        proposals = [("B",command), ("A",{**command,"cards":[land.ref]}),
                     ("A",{**command,"cards":[foreign.ref]}),
                     ("A",{**command,"cards":[chosen[0].ref,chosen[0].ref]}),
                     ("A",{**command,"cards":[card.ref for card in (*chosen,hidden)]})]
        for actor, invalid in proposals:
            self.assertFalse(game.act("pilot:"+actor,invalid).ok)
            self.assertEqual(before,authoritative_state_hash(game.state))
        result = game.act("pilot:A",command)
        self.assertTrue(result.ok,result.summary)
        self.settle(game)
        count = sum(card.is_token and card.zone=="battlefield" and card.controller=="A"
                    and "treasure" in game.engine._type_parts(game.engine._effective_card_data(card)["type_line"])[1]
                    for card in game.state.cards.values())
        self.assertEqual(5 if select_cards else 0,count)
        for card in chosen:
            self.assertEqual(select_cards,"B" in game.state.cards[card.object_id].known_to)
        self.assertNotIn("B",game.state.cards[hidden.object_id].known_to)
        self.assertEqual("graveyard",game.state.cards[saga.object_id].zone)
        self.replay(game,load=True)

    def test_actual_reveal_creates_five_tokens_for_two_repeated_values_and_replays(self):
        self.resolve_choice(select_cards=True)

    def test_actual_zero_selection_keeps_every_card_private_and_creates_no_tokens(self):
        self.resolve_choice(select_cards=False)

    def test_native_witness_detects_one_token_per_matching_group_instead_of_card(self):
        with patch.object(MatchingManaValueHandRevealSpec,"matching_count",return_value=2):
            with self.assertRaisesRegex(AssertionError,"5 != 2"):
                self.resolve_choice(select_cards=True)
