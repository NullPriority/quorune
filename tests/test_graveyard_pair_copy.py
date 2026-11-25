"""Native ordered graveyard copy costs and their entrant's counter result."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import GRAVEYARD_PAIR_COPY_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class GraveyardPairCopyTests(InheritedNativeFixture, unittest.TestCase):
    copier="Trial Mire Echo"
    printed_counter="Trial Rising Husk"
    power="Trial Grave Tally"
    other_copy="Trial Tomb Echo"
    ordinary="Trial Mire Bear"
    negative="Trial Withered Sentinel"
    token_spell="Cackling Counterpart"

    @classmethod
    def setUpClass(cls):
        cls.temporary=TemporaryDirectory()
        path=Path(cls.temporary.name)/"graveyard-pair-copy.sqlite3"
        fixtures=ROOT/"tests/fixtures"
        build_fixture_database([
            fixtures/"bound-effect-program-cards.json",
            fixtures/"conditional-copy-value-cards.json",
            fixtures/"graveyard-pair-copy-cards.json",
        ], path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        commander="Generic Bound Commander"
        cls.deck=DeckDefinition("Graveyard entry copy",[DeckEntry(commander,1,"commander"),
            DeckEntry("Generic Bound Plains",30)],[commander])

    def _begin(self,game,source):
        self.assert_closed(game,source)
        self.prepare(game,mana={"C":2,"B":1,"G":1,"U":1})
        self.checkpoint(game)
        self.cast(game,"A",source)
        return self.resolve(game)

    def _option(self,game,*,decline=False):
        pending=game.state.pending_decision
        self.assertEqual(("replacement.order",["A"]),(pending.kind,pending.actors))
        ctx=game.packet("pilot:A",full=True)["decision"]["ctx"]
        choice, = [option for option in ctx["options"] if option.get("decline")==decline]
        if not decline:
            objects=ctx["object_choices"][choice["id"]]
            self.assertEqual((2,2),(objects["minimum"],objects["maximum"]))
        return choice["id"]

    def _apply_pair(self,game,copy,power):
        command={"action_id":"choose","replacement":self._option(game),
                 "replacement_objects":[copy.ref,power.ref]}
        before=authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D",command).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        result=game.act("pilot:A",command)
        self.assertTrue(result.ok,result.summary)
        return self.resolve(game)

    def test_exile_both_roles_before_power_and_merge_printed_entry_counters(self):
        game=self.session(614_120_21)
        source=self.add(game.engine,self.copier,zone="hand")
        copy=self.add(game.engine,self.printed_counter,seat="B",zone="graveyard",ref="GRAVE-COPY")
        quantity=self.add(game.engine,self.power,seat="C",zone="graveyard",ref="GRAVE-POWER")
        spare=self.add(game.engine,self.ordinary,zone="graveyard",ref="GRAVE-SPARE")
        hidden=self.add(game.engine,self.ordinary,seat="D",zone="hand",ref="HIDDEN-COPY")
        self.assertEqual(3,game.engine._numeric_stat(quantity.object_id,"power"))
        self.assertIsNotNone(self._begin(game,source))
        option=self._option(game)
        public=game.packet("pilot:A",full=True)["decision"]["ctx"]["object_choices"][option]
        self.assertEqual({copy.ref,quantity.ref,spare.ref},set(public["legal_refs"]))
        self.assertNotIn(hidden.ref,json.dumps(public))
        before=authoritative_state_hash(game.state)
        for invalid in (copy.ref, hidden.ref, "OLD-GRAVE-REF"):
            result = game.act("pilot:A", {
                "action_id": "choose", "replacement": option,
                "replacement_objects": [copy.ref, invalid],
            })
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game=self.replay(game,load=True)
        self.assertIsNone(self._apply_pair(game,copy,quantity))
        current=game.state.cards[source.object_id]
        self.assertEqual("battlefield",current.zone)
        self.assertEqual(self.printed_counter,game.engine.display_name(current.object_id))
        self.assertEqual(3,current.counters.get("+1/+1"))
        self.assertEqual(("exile","exile","graveyard","hand"),
            tuple(game.state.cards[c.object_id].zone for c in (copy,quantity,spare,hidden)))
        self.assertEqual(1,game.engine._numeric_stat(quantity.object_id,"power"))
        self.replay(game,load=True)

    def test_decline_and_incomplete_pairs_leave_the_graveyards_intact(self):
        for enough in (False,True):
            with self.subTest(enough=enough):
                game=self.session(614_120_22+int(enough))
                source=self.add(game.engine,self.copier,zone="hand")
                cards=[self.add(game.engine,self.ordinary,seat="B",zone="graveyard")]
                if enough:
                    cards.append(self.add(game.engine,self.ordinary,seat="C",zone="graveyard",ref="SECOND-GRAVE-MODEL"))
                pending=self._begin(game,source)
                if enough:
                    self.assertIsNotNone(pending)
                    result=game.act("pilot:A",{"action_id":"choose","replacement":self._option(game,decline=True)})
                    self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(game))
                else:
                    self.assertIsNone(pending)
                self.assertEqual("graveyard",game.state.cards[source.object_id].zone)
                self.assertTrue(all(game.state.cards[card.object_id].zone=="graveyard" for card in cards))
                self.replay(game,load=True)

    def test_later_inherited_copy_keeps_paid_exiles_and_omits_the_prior_power_counters(self):
        game=self.session(614_120_24)
        source=self.add(game.engine,self.copier,zone="hand")
        copy=self.add(game.engine,self.other_copy,seat="B",zone="graveyard",ref="GRAVE-SECOND-COPY")
        power=self.add(game.engine,self.ordinary,seat="C",zone="graveyard",ref="GRAVE-PAID-POWER")
        final=self.add(game.engine,self.ordinary,seat="C",zone="graveyard",ref="GRAVE-FINAL-FORM")
        self._begin(game,source)
        pending=self._apply_pair(game,copy,power)
        self.assertIsNotNone(pending)
        options=game.packet("pilot:A",full=True)["decision"]["ctx"]["options"]
        self.assertFalse(any(row["id"].endswith(":"+power.ref) for row in options))
        selected,=[row for row in options if not row["decline"] and row["id"].endswith(":"+final.ref)]
        result=game.act("pilot:A",{"action_id":"choose","replacement":selected["id"]})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(game))
        current=game.state.cards[source.object_id]
        self.assertEqual(self.ordinary,game.engine.display_name(current.object_id))
        self.assertNotIn("+1/+1",current.counters)
        self.assertEqual(("exile","exile","graveyard"),
            tuple(game.state.cards[c.object_id].zone for c in (copy,power,final)))
        self.replay(game,load=True)

    def test_token_copy_pays_its_own_pair_and_clamps_negative_power(self):
        game = self.session(614_120_25)
        model = self.add(game.engine, self.copier, ref="BATTLEFIELD-COPY-MODEL")
        model.counters["+1/+1"] = 1
        spell = self.add(game.engine, self.token_spell, zone="hand", ref="COPY-TOKEN-SPELL")
        copy = self.add(game.engine, self.printed_counter, seat="B", zone="graveyard", ref="TOKEN-COPY-FORM")
        power = self.add(game.engine, self.negative, seat="C", zone="graveyard", ref="TOKEN-COPY-POWER")
        self.assertEqual(-1, game.engine._numeric_stat(power.object_id, "power"))
        self.assert_closed(game, spell)
        self.assert_closed(game, model)
        self.prepare(game, mana={"C": 1, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell, [model.ref])
        self.assertIsNotNone(self.resolve(game))
        game = self.replay(game, load=True)
        self.assertIsNone(self._apply_pair(game, copy, power))
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(self.printed_counter, game.engine.display_name(token.object_id))
        self.assertEqual(2, token.counters.get("+1/+1"))
        self.assertEqual(self.copier, game.engine.display_name(model.object_id))
        self.assertEqual(1, game.state.cards[model.object_id].counters.get("+1/+1"))
        self.assertEqual(("exile", "exile"), tuple(game.state.cards[card.object_id].zone for card in (copy, power)))
        self.replay(game, load=True)

    def test_paired_copy_authority_withdrawal_preserves_plain_graveyard_copy(self):
        actual=self.db.lookup(self.copier)
        control=self.db.lookup(self.other_copy)
        def compile_card(card, registry):
            return compile_oracle_card(card,capability_registry=registry,capability_profile="commander_review")
        result=compile_card(actual,self.registry)
        self.assertEqual("exact",result.status,result.material_residuals)
        self.assertTrue(any(node.template_id=="intrinsic-entry-copy-v13" for face in result.faces for node in face.nodes))
        raw=json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        cap=next(row for row in raw["capabilities"] if row["id"]==GRAVEYARD_PAIR_COPY_CAPABILITY)
        cap.update(status="blocked",blockers=["Ordered graveyard copy authority withdrawn."])
        withdrawn=CapabilityRegistry(raw)
        declarations=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        index=build_index(registry_value=raw,declaration_source=declarations,
                          discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index,registry=withdrawn))
        self.assertNotEqual("exact",compile_card(actual,withdrawn).status)
        self.assertEqual("exact",compile_card(control,withdrawn).status)
        with patch("quorune.compiler.entry_copy_templates._graveyard_pair_copy_spec",return_value=None):
            self.assertNotEqual("exact",compile_card(actual,self.registry).status)
