"""Original Oracle/rulings: additional whole-card source and damage compositions."""
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SourceCompositionClosureNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-compositions.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/source-composition-closure-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source composition closures',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def finish(self,session,private):
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def activation(self,session,source):
        self.priority(session,'A')
        return next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+source.ref+':'))

    def test_actual_trawler_and_sphinx_hexproof_fizzle_opponent_bounce_but_allow_controller_target(self):
        for trawler in (False,True):
            with self.subTest(trawler=trawler):
                session=self.session(996100+trawler)
                if trawler:source=self.add(session.engine,'Dream Trawler')
                else:source=self.add(session.engine,'Prognostic Sphinx')
                bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                blink=self.add(session.engine,'Cloudshift',zone='hand')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='closure-hexproof-private')
                cost=session.state.cards[next(oid for oid in session.state.players['A'].zones['hand'] if oid!=blink.object_id)]
                self.assert_closed(session,source);self.prepare(session,mana={'W':1});self.checkpoint(session)
                self.cast(session,'B',bounce,targets=[source.ref])
                offered=self.activation(session,source);command={'action_id':offered['id'],'cost_cards':[cost.ref],'pay':'auto'}
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard',session.state.cards[cost.object_id].zone);self.resolve_top(session)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertIn('hexproof',session.engine._combat_keywords(session.state.cards[source.object_id]))
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                self.assertEqual('graveyard',session.state.cards[bounce.object_id].zone)
                identity=session.state.cards[source.object_id].logical_object_id
                self.cast(session,'A',blink,targets=[source.ref]);self.assertIsNone(self.resolve(session))
                self.assertNotEqual(identity,session.state.cards[source.object_id].logical_object_id)
                self.assertFalse(session.state.cards[source.object_id].tapped)
                self.assertNotIn('hexproof',session.engine._combat_keywords(session.state.cards[source.object_id]));self.finish(session,private)

    def test_actual_battalion_uses_declaration_and_survives_another_attacker_leaving(self):
        for enough in (False,True):
            with self.subTest(enough=enough):
                session=self.session(996110+enough);source=self.add(session.engine,'Nav Squad Commandos')
                first=self.add(session.engine,'Grizzly Bears',ref='first-battalion-partner')
                second=self.add(session.engine,'Grizzly Bears',ref='second-battalion-partner');self.add(session.engine,'Fervor')
                bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='closure-battalion-private')
                self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
                assignments=[(source,'D'),(first,'D')]
                if enough:assignments.append((second,'D'))
                self.attack(session,assignments);self.assertEqual(int(enough),self.trigger_count(session,source))
                if enough:
                    self.cast(session,'B',bounce,targets=[second.ref]);self.resolve_top(session)
                    self.assertEqual('hand',session.state.cards[second.object_id].zone)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(not enough,session.state.cards[source.object_id].tapped)
                self.assertEqual(4 if enough else 3,session.engine._numeric_stat(source.object_id,'power'))
                self.assertEqual(6 if enough else 5,session.engine._numeric_stat(source.object_id,'toughness'));self.finish(session,private)

    def test_actual_forge_devil_mandatory_self_target_deals_controller_damage_before_death(self):
        session=self.session(996120);source=self.add(session.engine,'Forge Devil',zone='hand')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='closure-forge-private')
        self.assert_closed(session,source);self.prepare(session,mana={'R':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.target',self.resolve(session).kind)
        session=self.replay(session,load=True);self.target(session,'A',[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(39,session.state.players['A'].life)
        self.assertEqual([40,40,40],[session.state.players[seat].life for seat in 'BCD']);self.finish(session,private)

    def test_actual_blistercoil_untaps_and_grows_before_own_instant_with_nonmatching_controls(self):
        session=self.session(996130);source=self.add(session.engine,'Blistercoil Weird');source.tapped=True
        artifact=self.add(session.engine,'Ornithopter',zone='hand');other=self.add(session.engine,'Grizzly Bears')
        growth=self.add(session.engine,'Giant Growth',zone='hand')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='closure-blistercoil-private')
        self.assert_closed(session,source);self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.cast(session,'A',artifact);self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
        self.cast(session,'B',bounce,targets=[other.ref]);self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',growth,targets=[artifact.ref]);self.assertEqual(1,self.trigger_count(session,source));self.resolve_top(session)
        self.assertTrue(any(item.card_object_id==growth.object_id for item in session.state.stack))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(3,session.engine._numeric_stat(artifact.object_id,'power'));self.finish(session,private)

    def test_actual_icehide_uses_snow_sources_and_rejects_ordinary_green_sources(self):
        accepted_id=None
        for snow in (True,False):
            with self.subTest(snow=snow):
                session=self.session(996140+snow);source=self.add(session.engine,'Icehide Troll',ref='closure-icehide-source')
                if snow:
                    first=self.add(session.engine,'Snow-Covered Forest',ref='first-snow-payer')
                    second=self.add(session.engine,'Snow-Covered Forest',ref='second-snow-payer')
                else:
                    first=self.add(session.engine,'Forest',ref='first-ordinary-payer')
                    second=self.add(session.engine,'Forest',ref='second-ordinary-payer')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='closure-snow-private')
                self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
                rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                offered=[row for row in rows if row['id'].startswith('activate:'+source.ref+':')]
                if snow:
                    self.assertEqual(1,len(offered));accepted_id=offered[0]['id']
                    result=session.act('pilot:A',{'action_id':accepted_id,'pay':'auto'});self.assertTrue(result.ok,result.summary)
                    self.assertTrue(session.state.cards[first.object_id].tapped);self.assertTrue(session.state.cards[second.object_id].tapped)
                    self.assertFalse(session.state.cards[source.object_id].tapped)
                    session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                    self.assertTrue(session.state.cards[source.object_id].tapped)
                    self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
                    self.assertIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
                else:
                    self.assertEqual([],offered);self.assertIsNotNone(accepted_id)
                    before=authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A',{'action_id':accepted_id,'pay':'auto'}).ok)
                    self.assertEqual(before,authoritative_state_hash(session.state))
                    self.assertFalse(session.state.cards[first.object_id].tapped);self.assertFalse(session.state.cards[second.object_id].tapped)
                self.finish(session,private)
