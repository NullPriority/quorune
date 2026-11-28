"""Original loyalty activation, attached Mask protection, and both graveyard entry phases."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.enchantment_loyalty_results import (
    MASK_AURA_CAPABILITY, ORDERED_ENCHANTMENT_RETURN_CAPABILITY, ordered_enchantment_return_effects,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement


NAME = "Estrid, the Masked"
PROFILE = "commander_review"


class EstridLoyaltyResultTests(InheritedNativeFixture, unittest.TestCase):
    activate = movement.FixedOneCounterMovementTests.activate
    reject = movement.FixedOneCounterMovementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "estrid-loyalty.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "entry-discard-and-death-reanimation-cards.json",
            fixtures / "event-player-public-count-cards.json",
            fixtures / "public-graveyard-entry-cards.json",
            fixtures / "estrid-masked-loyalty-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Estrid", [
            DeckEntry(commander,1,"commander"), DeckEntry("Plains",30),
        ], [commander])

    def prepare_estrid(self, game, *cards):
        for card in cards:
            self.assert_closed(game,card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game,actor="A",active="A")
        self.checkpoint(game)

    def card(self, game, original):
        return game.state.cards[original.object_id]

    def test_original_mask_and_return_envelopes_reject_wrong_price_target_or_order(self):
        record = self.db.lookup(NAME,fuzzy=False)
        report, semantics = register_card(self.db,self.registry,NAME)
        self.assertTrue(report["strict_capability_ready"],report["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        mask, = (program for program in programs if MASK_AURA_CAPABILITY in program.capability_dependencies)
        returning, = (program for program in programs if ORDERED_ENCHANTMENT_RETURN_CAPABILITY in program.capability_dependencies)
        self.assertEqual(-1,mask.cost_schema["loyalty_delta"])
        self.assertEqual(-7,returning.cost_schema["loyalty_delta"])
        self.assertEqual(ordered_enchantment_return_effects(),tuple(returning.effects))
        for changed in (
            replace(mask,target_schema={**mask.target_schema,"source_exclusion":False}),
            replace(mask,cost_schema={**mask.cost_schema,"loyalty_delta":-2}),
            replace(returning,effects=[returning.effects[0],*reversed(returning.effects[1:])]),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed,capability_registry=self.registry,profile=PROFILE,
            )["strict"])

    def test_actual_plus_two_untaps_only_current_controlled_enchanted_permanents(self):
        game = self.session(47840401)
        source = self.add(game.engine,NAME)
        source.counters["loyalty"] = 3
        chosen = self.add(game.engine,"Grizzly Bears",ref="controlled-enchanted")
        unenchanted = self.add(game.engine,"Grizzly Bears",ref="unenchanted")
        foreign = self.add(game.engine,"Grizzly Bears",seat="B",ref="opposing-enchanted")
        for subject in (chosen,unenchanted,foreign):
            subject.tapped = True
        for subject in (chosen,foreign):
            aura = self.add(game.engine,"Rancor",seat=subject.controller,ref=f"aura-{subject.controller}")
            attach_objects(game.state.cards,aura,subject,source_timestamp=game.engine._next_zone_timestamp(),
                           players=game.state.players)
        self.prepare_estrid(game,source)
        self.activate(game,source,"ab1",[])
        self.settle(game)
        self.assertEqual(5,self.card(game,source).counters["loyalty"])
        self.assertFalse(self.card(game,chosen).tapped)
        self.assertTrue(self.card(game,unenchanted).tapped)
        self.assertTrue(self.card(game,foreign).tapped)
        self.replay(game,load=True)

    def test_actual_mask_enters_attached_and_umbra_armor_protects_its_permanent(self):
        game = self.session(47840402)
        source = self.add(game.engine,NAME)
        source.counters["loyalty"] = 3
        protected = self.add(game.engine,"Grizzly Bears")
        murder = self.add(game.engine,"Murder",seat="B",zone="hand")
        for index in range(3):
            self.add(game.engine,"Swamp",seat="B",ref=f"mask-swamp-{index}")
        self.prepare_estrid(game,source,murder)
        self.assertNotIn(murder.ref,str(game.packet("pilot:A",full=True)))
        self.reject(game,"A",{"action_id":f"activate:{source.ref}:ab2","targets":[source.ref],"pay":"auto"})
        self.activate(game,source,"ab2",[protected.ref])
        self.settle(game)
        self.assertEqual(2,self.card(game,source).counters["loyalty"])
        mask, = (card for card in game.state.cards.values() if card.is_token and card.zone=="battlefield")
        self.assertEqual(protected.object_id,mask.attached_to)
        data = game.engine._effective_card_data(mask)
        self.assertEqual("Mask",data["name"])
        self.assertEqual(("W",),tuple(data["colors"]))
        game = self.replay(game,load=True)
        self.cast(game,"B",murder,[protected.ref])
        self.settle(game)
        self.assertEqual("battlefield",self.card(game,protected).zone)
        remainder = game.state.cards.get(mask.object_id)
        self.assertTrue(remainder is None or remainder.zone != "battlefield")
        self.replay(game,load=True)

    def test_actual_minus_seven_mills_then_returns_nonauras_before_choosing_aura_attachment(self):
        game = self.session(47840403)
        source = self.add(game.engine,NAME)
        source.counters["loyalty"] = 7
        body = self.add(game.engine,"Eidolon of Blossoms",zone="graveyard")
        aura = self.add(game.engine,"Rancor",zone="graveyard")
        self.prepare_estrid(game,source,body,aura)
        available = len(game.state.players["A"].zones["library"])
        self.activate(game,source,"ab3",[])
        self.until(game,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=="aura.entry")
        self.assertEqual("graveyard",self.card(game,source).zone)
        self.assertEqual(available-7,len(game.state.players["A"].zones["library"]))
        self.assertEqual("battlefield",self.card(game,body).zone)
        self.assertEqual("graveyard",self.card(game,aura).zone)
        packet = game.packet("pilot:A",full=True)["decision"]["ctx"]
        self.assertEqual([body.ref],packet["target_schema"]["legal_refs"])
        game = self.replay(game,load=True)
        command = {"action_id":"choose","aura_target":body.ref}
        self.reject(game,"B",command)
        result = game.act("pilot:A",command)
        self.assertTrue(result.ok,result.summary)
        self.settle(game)
        self.assertEqual(("battlefield",body.object_id),
                         (self.card(game,aura).zone,self.card(game,aura).attached_to))
        self.replay(game,load=True)

    def test_mask_witness_detects_omitting_umbra_armor(self):
        with patch("quorune.destruction_replacement_adapter.current_umbra_armor_protections",return_value=()):
            with self.assertRaisesRegex(AssertionError,"graveyard"):
                self.test_actual_mask_enters_attached_and_umbra_armor_protects_its_permanent()

    def test_ordered_return_witness_detects_milling_six_instead_of_seven(self):
        from quorune.milling import mill_cards

        def mill_six(host, request, **kwargs):
            changed = replace(request,count=6) if request.count==7 else request
            return mill_cards(host,changed,**kwargs)

        with patch("quorune.milling.mill_cards",new=mill_six):
            with self.assertRaisesRegex(AssertionError,r"\d+ != \d+"):
                self.test_actual_minus_seven_mills_then_returns_nonauras_before_choosing_aura_attachment()
