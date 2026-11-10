"""Original optional numeric searches through their ordinary accepted actions."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class LibrarySearchDispatchNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'numeric-search-dispatch.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/library-search-dispatch-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Numeric search dispatch',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_original_basic_land_type_searches_offer_typed_duals_and_keep_other_cards_private(self):
        for index, name in enumerate(("Nervous Gardener", "Spoils of Victory", "Perilous Forays")):
            with self.subTest(name=name):
                session = self.session(305600 + index)
                permanent = name == "Perilous Forays"
                source = self.add(session.engine, name, zone="battlefield" if permanent else "hand")
                dual = self.add(session.engine, "Volcanic Island", zone="library", ref="basic-type-dual")
                wastes = self.add(session.engine, "Wastes", zone="library", ref="basic-type-wastes")
                other = self.add(session.engine, "Terramorphic Expanse", zone="library", ref="basic-type-untyped")
                mountain = self.add(session.engine, "Mountain", zone="library", ref="basic-type-mountain")
                if permanent:
                    fodder = self.add(session.engine, "Generic Bound Body")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 4, "G": 1})
                self.checkpoint(session)
                if name == "Nervous Gardener":
                    rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    cast = next(row for row in rows if row["id"] == f"cast-disguise:{source.ref}")
                    result = session.act("pilot:A", {"action_id": cast["id"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(session))
                    self.assertTrue(session.state.cards[source.object_id].face_down)
                    self.priority(session, "A")
                    rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    turn_up = next(row for row in rows if row["id"] == f"turn-face-up:{source.ref}")
                    result = session.act("pilot:A", {"action_id": turn_up["id"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                elif permanent:
                    rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    activate = next(row for row in rows if row["id"].startswith(f"activate:{source.ref}:"))
                    result = session.act("pilot:A", {"action_id": activate["id"], "cost_cards": [fodder.ref], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("graveyard", session.state.cards[fodder.object_id].zone)
                else:
                    self.cast_for(session, "A", source)
                self.assertEqual("semantic.search", self.resolve(session).kind)
                packet = session.packet("pilot:A", full=True)["decision"]
                schema = packet["legal_actions"][0]["choice_schema"]
                refs = schema["legal_refs"]
                self.assertIn(dual.ref, refs)
                self.assertIn(mountain.ref, refs)
                self.assertNotIn(wastes.ref, refs)
                self.assertNotIn(other.ref, refs)
                self.assertEqual(0, schema["minimum"])
                self.assertTrue(schema["rules_may_fail_to_find"])
                for seat in "BCD":
                    projected = json.dumps(session.packet(f"pilot:{seat}", full=True))
                    self.assertNotIn(dual.ref, projected)
                    self.assertNotIn(wastes.ref, projected)
                self.reject(session, "A", {"action_id": "choose", "search_cards": [wastes.ref]})
                self.reject(session, "A", {"action_id": "choose", "search_cards": [other.ref]})
                self.reject(session, "B", {"action_id": "choose", "search_cards": [dual.ref]})
                before = session.state.players["A"].stats.get("shuffle_count", 0)
                session = self.replay(session, load=True)
                result = session.act("pilot:A", {"action_id": "choose", "search_cards": [dual.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[dual.object_id]
                self.assertEqual("hand" if name == "Nervous Gardener" else "battlefield", current.zone)
                self.assertEqual(permanent, current.tapped)
                self.assertEqual(before + 1, session.state.players["A"].stats["shuffle_count"])
                for seat in "BCD":
                    projected = json.dumps(session.packet(f"pilot:{seat}", full=True))
                    self.assertIn(dual.ref, projected)
                    self.assertNotIn(wastes.ref, projected)
                    self.assertNotIn(other.ref, projected)
                self.replay(session)

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def begin(self,name,seed):
        s=self.session(seed);mage=self.add(s.engine,name,zone='hand')
        cards={name:self.add(s.engine,name,zone='library',ref='numeric-search-'+str(index))
            for index,name in enumerate(('Sol Ring','Arcane Signet','Stonecoil Serpent','Stone Golem','Counterspell'))}
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='numeric-search-other-private')
        self.assert_closed(s,mage);self.prepare(s,mana={'U':1,'C':2});self.checkpoint(s)
        self.cast(s,'A',mage,[]);decision=self.resolve(s)
        self.assertEqual('semantic.choice',decision.kind)
        for viewer in 'BCD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            for card in cards.values():self.assertNotIn(card.ref,packet)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        return s,mage,cards

    def test_original_mages_offer_the_complete_numeric_predicate_and_reveal_only_the_chosen_card(self):
        cases=(('Trinket Mage',{'Sol Ring','Stonecoil Serpent'},'Stonecoil Serpent'),
               ('Tribute Mage',{'Arcane Signet'},'Arcane Signet'))
        for index,(name,eligible,chosen_name) in enumerate(cases):
            with self.subTest(name=name):
                s,mage,cards=self.begin(name,701230810+index)
                self.reject(s,'B',{'action_id':'choose','choice':'apply'})
                s=self.replay(s,load=True)
                result=s.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
                self.assertEqual('semantic.search',s.state.pending_decision.kind)
                schema=s.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']
                self.assertEqual(eligible,{name for name,card in cards.items() if card.ref in schema['legal_refs']})
                self.assertEqual(0,schema['minimum']);self.assertTrue(schema['rules_may_fail_to_find'])
                for viewer in 'BCD':
                    packet=json.dumps(s.packet('pilot:'+viewer,full=True))
                    for card in cards.values():self.assertNotIn(card.ref,packet)
                self.reject(s,'A',{'action_id':'choose','search_cards':[cards['Stone Golem'].ref]})
                self.reject(s,'A',{'action_id':'choose','search_cards':[cards['Counterspell'].ref]})
                chosen=cards[chosen_name];self.reject(s,'D',{'action_id':'choose','search_cards':[chosen.ref]})
                before=s.state.players['A'].stats.get('shuffle_count',0);s=self.replay(s,load=True)
                result=s.act('pilot:A',{'action_id':'choose','search_cards':[chosen.ref]});self.assertTrue(result.ok,result.summary)
                self.assertEqual('hand',s.state.cards[chosen.object_id].zone)
                self.assertEqual(before+1,s.state.players['A'].stats['shuffle_count'])
                for viewer in 'ABCD':
                    packet=json.dumps(s.packet('pilot:'+viewer,full=True));self.assertIn(chosen.ref,packet)
                    for other in cards.values():
                        if other.object_id!=chosen.object_id:self.assertNotIn(other.ref,packet)
                self.replay(s,load=True)

    def test_declining_search_does_not_shuffle_but_searching_and_finding_nothing_does(self):
        for decline in (True,False):
            with self.subTest(decline=decline):
                s,mage,cards=self.begin('Trinket Mage',701230820+int(decline))
                before=s.state.players['A'].stats.get('shuffle_count',0)
                library=tuple(s.state.players['A'].zones['library']);s=self.replay(s,load=True)
                result=s.act('pilot:A',{'action_id':'choose','choice':'decline' if decline else 'apply'});self.assertTrue(result.ok,result.summary)
                if not decline:
                    self.assertEqual('semantic.search',s.state.pending_decision.kind)
                    result=s.act('pilot:A',{'action_id':'choose','search_cards':[]});self.assertTrue(result.ok,result.summary)
                self.assertEqual(before+int(not decline),s.state.players['A'].stats.get('shuffle_count',0))
                if decline:self.assertEqual(library,tuple(s.state.players['A'].zones['library']))
                self.assertTrue(all(s.state.cards[c.object_id].zone=='library' for c in cards.values()))
                self.replay(s,load=True)
