"""Invoke Calamity's as-cast budget, paid costs, targeting, and exact replay."""
from dataclasses import replace
import json
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
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Invoke Calamity"


class BudgetedResolvingCastTests(InheritedNativeFixture, unittest.TestCase):
    @staticmethod
    def make_deck():
        commander="Isamaru, Hound of Konda"
        return DeckDefinition("Budgeted resolving cast",[DeckEntry(commander,1,"commander"),DeckEntry("Plains",30)],[commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary=TemporaryDirectory()
        path=Path(cls.temporary.name)/"invoke-calamity.sqlite3"
        build_fixture_database([ROOT/"tests/fixtures/budgeted-resolving-cast-cards.json"],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=cls.make_deck()

    def command(self,game,seat,request,*,succeeds=True):
        before=authoritative_state_hash(game.state)
        response=game.act("pilot:"+seat,request)
        self.assertEqual(succeeds,response.ok,response.summary)
        if not succeeds:self.assertEqual(before,authoritative_state_hash(game.state))

    def cast_invoke(self,game,source):
        self.assert_closed(game,source)
        self.prepare(game,mana={"R":6})
        game.state.players["B"].mana_pool.update({"U":3,"C":1})
        self.checkpoint(game)
        self.cast_for(game,"A",source)
        return self.resolve(game)

    def options(self,game):
        return game.packet("pilot:A",full=True)["decision"]["ctx"]["cast_options"]

    def select(self,game,option,*,succeeds=True,**fields):
        payload={"card":option["card"],"cost_option":option["id"],**fields}
        self.command(game,"A",{"action_id":"choose","choice":"cast","cast":payload},succeeds=succeeds)

    def test_actual_two_zone_cast_budget_keeps_mandatory_costs_and_exiles_both_resolutions(self):
        game=self.session(159700101)
        host=game.engine
        source=self.add(host,SOURCE,zone="hand",ref="invoke-main")
        first=self.add(host,"Cathartic Reunion",zone="hand",ref="invoke-paid-hand")
        last=self.add(host,"Harmonize",zone="graveyard",ref="invoke-graveyard")
        excess=self.add(host,"Time Warp",zone="hand",ref="invoke-over-budget-after-payment")
        alien=self.add(host,"Harmonize",zone="graveyard",seat="B",ref="invoke-foreign")
        prices=[self.add(host,"Plains",zone="hand",ref=f"invoke-price-{index}") for index in range(2)]
        for card in (first,last,excess):self.assert_closed(game,card)
        pending=self.cast_invoke(game,source)
        self.assertEqual(("A",),tuple(pending.actors))
        self.assertNotIn(first.ref,json.dumps(game.packet("pilot:B",full=True)))
        choices={row["card"]:row for row in self.options(game)}
        self.assertEqual({first.ref,last.ref,excess.ref},set(choices))
        self.assertEqual((2,4,5),tuple(choices[card.ref]["mana_value"] for card in (first,last,excess)))
        self.assertNotIn(alien.ref,choices)
        self.select(game,choices[first.ref],discard_cards=[prices[0].ref],succeeds=False)
        game=self.replay(game,load=True)
        self.select(game,choices[first.ref],discard_cards=[card.ref for card in prices])
        self.assertTrue(all(game.state.cards[card.object_id].zone=="graveyard" for card in prices))
        remaining,=self.options(game)
        self.assertEqual((last.ref,4),(remaining["card"],remaining["mana_value"]))
        self.select(game,choices[excess.ref],succeeds=False)
        game=self.replay(game,load=True)
        self.select(game,remaining)
        self.assertEqual("exile",game.state.cards[source.object_id].zone)
        self.assertEqual([first.object_id,last.object_id],[item.card_object_id for item in game.state.stack])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual(("exile",)*2,tuple(game.state.cards[card.object_id].zone for card in (first,last)))
        self.replay(game,load=True)

    def test_actual_second_spell_targets_first_and_graveyard_replacement_stays_on_that_spell(self):
        game=self.session(159700102)
        host=game.engine
        source=self.add(host,SOURCE,zone="hand")
        first=self.add(host,"Harmonize",zone="graveyard")
        counter=self.add(host,"Counterspell",zone="hand")
        self.cast_invoke(game,source)
        opts={row["card"]:row for row in self.options(game)}
        self.select(game,opts[first.ref])
        spell=next(row for row in game.state.stack if row.card_object_id==first.object_id)
        counter_offer,=self.options(game)
        self.assertEqual(counter.ref,counter_offer["card"])
        self.select(game,counter_offer,targets=[spell.ref])
        game=self.replay(game,load=True)
        self.resolve_top(game)
        self.assertEqual(("exile","exile","exile"),tuple(game.state.cards[card.object_id].zone
                         for card in (source,first,counter)))
        self.assertFalse(game.state.stack)
        self.replay(game,load=True)

    def test_decline_terminal_move_and_non_graveyard_destination(self):
        game=self.session(159700103)
        host=game.engine
        source=self.add(host,SOURCE,zone="hand")
        first=self.add(host,"Blaze",zone="graveyard")
        modal=self.add(host,"Jwari Disruption // Jwari Ruins",zone="hand")
        bounce=self.add(host,"Remand",zone="hand",seat="B")
        self.assert_closed(game,bounce)
        self.assert_closed(game,first)
        self.assert_closed(game,modal)
        self.cast_invoke(game,source)
        opts=self.options(game)
        self.assertEqual([("Jwari Disruption",2)],[(row["face"],row["mana_value"]) for row in opts if row["card"]==modal.ref])
        blaze,=[row for row in opts if row["card"]==first.ref]
        self.assertEqual(1,blaze["mana_value"])
        self.select(game,blaze,targets=["C"],x=1,succeeds=False)
        self.select(game,blaze,targets=["C"],x=0)
        self.command(game,"A",{"action_id":"choose","choice":"decline"})
        self.assertEqual("exile",game.state.cards[source.object_id].zone)
        parent,=game.state.stack
        self.assertEqual(0,parent.x_value)
        self.cast_for(game,"B",bounce,[parent.ref])
        self.resolve_top(game)
        self.assertEqual("hand",game.state.cards[first.object_id].zone)
        self.assertEqual("hand",game.state.cards[modal.object_id].zone)
        self.replay(game,load=True)

    def test_original_requires_terminal_permission_and_rejects_a_widened_budget(self):
        card=self.db.lookup(SOURCE,fuzzy=False)
        args={"capability_registry":self.registry,"capability_profile":"commander_review"}
        ir=compile_oracle_card(card,**args)
        self.assertEqual(("exact",()),(ir.status,ir.material_residuals))
        report,semantics=register_card(self.db,self.registry,SOURCE)
        self.assertTrue(report["strict_capability_ready"],report)
        program,=semantics.programs_for_oracle(card.oracle_id)
        effect,=thaw_value(program.effects)
        for changed in (replace(program,destination="graveyard"),replace(program,effects=({**effect,"mana_value_limit":7},))):
            check=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile="commander_review")
            self.assertFalse(check["strict"],check)
        with patch("quorune.compiler.resolution_effect_templates._budgeted_hand_graveyard_cast", return_value=None):
            self.assertNotEqual("exact",compile_oracle_card(card,**args).status)
