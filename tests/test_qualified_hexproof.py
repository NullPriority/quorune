from __future__ import annotations

"""CR 702.11d-g: Hexproof variants filter the actual opposing source."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
from quorune.rules.evidence_storage import read_evidence_document


class QualifiedHexproofTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ProvokeTests.session
    add=controls.ProvokeTests.add
    prepare=controls.ProvokeTests.prepare
    checkpoint=controls.ProvokeTests.checkpoint
    actions=controls.ProvokeTests.actions
    resolve=controls.ProvokeTests.resolve
    replay=controls.ProvokeTests.replay
    binding=controls.ProvokeTests.binding
    advance=controls.ProvokeTests.advance
    private_replay=controls.ProvokeTests.private_replay
    priority=controls.ProvokeTests.priority
    attack=controls.ProvokeTests.attack
    blockers=controls.ProvokeTests.blockers
    block=controls.ProvokeTests.block

    def cast(self,session,seat,source,targets=(),payment=None):
        self.priority(session,seat)
        command={'action_id':'cast:'+source.ref,'targets':list(targets),'pay':'manual' if payment else 'auto'}
        if payment:command['payment']=payment
        result=session.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def top(self,session,remaining=1):
        for _ in range(24):
            if len(session.state.stack)<=remaining:return
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Expected top stack item to finish')

    def tokens(self,session,subtype):
        return [c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token
            and subtype in session.engine._type_parts(session.engine._effective_card_data(c)['type_line'])[1]]

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'qualified-hexproof.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-hexproof-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified Hexproof',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_black_opponent_spell_is_rejected_while_blue_spell_still_resolves(self):
        session=self.session(750100);target=self.add(session.engine,'Knight of Grace')
        matching=self.add(session.engine,'Murder',seat='B',zone='hand')
        other=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'B':2,'U':2,'C':1});self.checkpoint(session)
        command={'action_id':'cast:'+matching.ref,'targets':[target.ref],'pay':'auto'}
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:B',command)
        self.assertFalse(result.ok,'A black opposing Murder must not target Knight of Grace')
        self.assertEqual(before,authoritative_state_hash(session.state))
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        murder=next((r for r in actions if r['id']==command['action_id']),None)
        self.assertTrue(murder is None or target.ref not in murder['target_schema']['legal_refs'])
        allowed=next((r for r in actions if r['id']=='cast:'+other.ref),None);self.assertIsNotNone(allowed)
        self.assertIn(target.ref,allowed['target_schema']['legal_refs'],'A nonmatching blue source remains a legal target choice')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':allowed['id'],'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':allowed['id'],'targets':[target.ref],'pay':'manual','payment':{'U':2}});self.assertTrue(result.ok,result.summary)
        self.assertTrue(self.binding(session,target)['strict_capability_ready'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[target.object_id].zone)
        self.assertEqual(2,session.state.players['B'].mana_pool['B']);self.private_replay(session,private)

    def test_fresh_keyword_producer_and_actual_quality_guard_omissions_are_killed(self):
        method='test_actual_black_opponent_spell_is_rejected_while_blue_spell_still_resolves'
        for owner in ('quorune.compiler.keyword_templates.qualified_hexproof_keywords','quorune.target_protection.qualified_hexproof_prohibits'):
            with patch(owner,return_value=None):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_six_originals_have_complete_source_spans_and_distinct_qualified_keywords(self):
        cases={'Knight of Grace':{'hexproof from black'},'Knight of Malice':{'hexproof from white'},
            'Sporeweb Weaver':{'hexproof from blue'},'General Ferrous Rokiric':{'hexproof from monocolored'},
            'Eradicator Valkyrie':{'hexproof from planeswalkers'},
            'Breaker of Creation':{'hexproof from '+c for c in ('white','blue','black','red','green')}}
        for i,(name,expected) in enumerate(cases.items()):
            with self.subTest(name=name):
                session=self.session(750110+i);source=self.add(session.engine,name)
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals);self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                for face in ir.faces:
                    for node in face.nodes:self.assertEqual(node.text,face.oracle_text[node.span.start:node.span.end])
                keywords=session.engine._combat_keywords(source)
                from quorune.qualified_hexproof import QUALIFIED_HEXPROOF_KEYWORDS
                self.assertEqual(expected,set(keywords).intersection(QUALIFIED_HEXPROOF_KEYWORDS));self.assertNotIn('hexproof',keywords)

    def test_actual_weaver_allows_red_damage_then_gains_life_and_creates_saproling(self):
        session=self.session(750120);weaver=self.add(session.engine,'Sporeweb Weaver')
        bolt=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        blue=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'R':1,'U':2});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+blue.ref,'targets':[weaver.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',bolt,[weaver.ref],{'R':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((3,41),(session.state.cards[weaver.object_id].marked_damage,session.state.players['A'].life))
        token,=self.tokens(session,'saproling');data=session.engine._effective_card_data(token)
        self.assertEqual(('1','1',['G']),(data['power'],data['toughness'],data['colors']));self.replay(session)

    def test_actual_general_triggers_only_multicolored_casts_and_keeps_complete_token(self):
        session=self.session(750121);source=self.add(session.engine,'General Ferrous Rokiric')
        helix=self.add(session.engine,'Lightning Helix',zone='hand');bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        self.prepare(session,mana={'R':2,'W':1});self.checkpoint(session)
        self.cast(session,'A',helix,['B'],{'R':1,'W':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        token,=self.tokens(session,'golem');data=session.engine._effective_card_data(token)
        self.assertEqual(('4','4'),(data['power'],data['toughness']));self.assertEqual({'R','W'},set(data['colors']))
        self.assertEqual((43,37),(session.state.players['A'].life,session.state.players['B'].life))
        self.cast(session,'A',bolt,['B'],{'R':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(1,len(self.tokens(session,'golem')));self.assertEqual(34,session.state.players['B'].life);self.replay(session)

    def test_both_knights_keep_current_color_condition_and_first_strike_combat(self):
        for i,(name,other) in enumerate((('Knight of Grace','Knight of Malice'),('Knight of Malice','Knight of Grace'))):
            with self.subTest(name=name):
                session=self.session(750130+i);source=self.add(session.engine,name);source.acquired_control_turn_count=-1
                opposite=self.add(session.engine,other,seat='C');bear=self.add(session.engine,'Grizzly Bears',seat='B')
                bounce=self.add(session.engine,'Boomerang',zone='hand')
                for j in range(2):self.add(session.engine,'Island',ref='knight-island-'+str(j))
                self.prepare(session);self.checkpoint(session)
                self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
                self.attack(session,{source.ref:'B'});self.blockers(session);self.block(session,{bear.ref:[source.ref]})
                self.advance(session,lambda s:s.state.phase=='postcombat_main')
                self.assertEqual(('battlefield','graveyard'),(session.state.cards[source.object_id].zone,session.state.cards[bear.object_id].zone))
                self.assertEqual(0,session.state.cards[source.object_id].marked_damage)
                self.cast(session,'A',bounce,[opposite.ref]);self.assertIsNone(self.resolve(session))
                self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'));self.replay(session)

    def test_actual_breaker_cast_life_counts_colorless_permanents_before_entry(self):
        session=self.session(750140);source=self.add(session.engine,'Breaker of Creation',zone='hand')
        self.add(session.engine,'Icy Manipulator')
        for j in range(2):self.add(session.engine,'Plains',ref='breaker-plains-'+str(j))
        self.prepare(session,mana={'C':8});self.checkpoint(session)
        self.cast(session,'A',source,payment={'C':8});self.assertEqual(2,len(session.state.stack))
        self.top(session);self.assertEqual(43,session.state.players['A'].life)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.assertEqual(43,session.state.players['A'].life);self.replay(session)

    def test_actual_breaker_annihilator_and_colored_blocker_still_follow_combat_rules(self):
        session=self.session(750141);source=self.add(session.engine,'Breaker of Creation');source.acquired_control_turn_count=-1
        bear=self.add(session.engine,'Grizzly Bears',seat='B')
        lands=[self.add(session.engine,'Plains',seat='B',ref='annihilator-land-'+str(j)) for j in range(2)]
        self.prepare(session);self.checkpoint(session);self.attack(session,{source.ref:'B'})
        self.assertIsNotNone(self.resolve(session));session=self.replay(session,load=True)
        result=session.act('pilot:B',{'action_id':'choose','cards':[c.ref for c in lands]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in lands))
        self.blockers(session);self.block(session,{bear.ref:[source.ref]});self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(('battlefield','graveyard'),(session.state.cards[source.object_id].zone,session.state.cards[bear.object_id].zone))
        self.assertEqual((2,40),(session.state.cards[source.object_id].marked_damage,session.state.players['B'].life));self.replay(session)

    def test_actual_planeswalker_ability_is_blocked_but_colorless_artifact_ability_is_allowed(self):
        session=self.session(750150);target=self.add(session.engine,'Eradicator Valkyrie')
        walker=self.add(session.engine,'Generic Qualified Hexproof Planeswalker',seat='B')
        icy=self.add(session.engine,'Icy Manipulator',seat='B')
        self.prepare(session,actor='B',active='B',mana={'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,walker)['strict_capability_ready'])
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        ability=next(r for r in actions if r['id'].startswith('activate:'+walker.ref+':'))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref],'pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        ability=next(r for r in actions if r['id'].startswith('activate:'+icy.ref+':'))
        self.assertIn(target.ref,ability['target_schema']['legal_refs'])
        result=session.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[target.object_id].tapped);self.assertEqual(0,session.state.cards[target.object_id].marked_damage);self.replay(session)

    def test_actual_own_matching_spell_remains_legal_and_layer_six_loss_removes_variants(self):
        session=self.session(750151);target=self.add(session.engine,'Knight of Malice')
        humble=self.add(session.engine,'Humble',zone='hand');answer=self.add(session.engine,'Defiant Strike',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':1,'C':1});session.state.players['B'].mana_pool.update({'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[target.ref],{'W':1,'C':1});self.assertIsNone(self.resolve(session))
        self.assertNotIn('hexproof from white',session.engine._combat_keywords(session.state.cards[target.object_id]))
        library=len(session.state.players['B'].zones['library'])
        self.cast(session,'B',answer,[target.ref],{'W':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual((1,1),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.assertEqual(library-1,len(session.state.players['B'].zones['library']));self.private_replay(session,private)

    def test_keyword_type_queries_removal_and_current_quality_matrix_are_closed(self):
        from dataclasses import replace
        from quorune.qualified_hexproof import qualified_hexproof_prohibits,keyword_type_terms,keyword_is_removed
        from quorune.target_source import TargetingSource
        from quorune.target_protection import TargetProtectionSnapshot,TargetProtectionVerdict,target_protection_verdict,TargetProtectionError
        from quorune.targets import TargetGroup
        from test_bound_effect_programs import record
        from quorune.compiler.qualified_hexproof_keywords import qualified_hexproof_keywords
        source=TargetingSource('ability',frozenset({'B'}),frozenset({'creature'}),frozenset(),'A')
        base=TargetProtectionSnapshot('B','A',target_keywords=frozenset({'hexproof from black'}),source_qualities=source)
        self.assertEqual(TargetProtectionVerdict.HEXPROOF,target_protection_verdict(base))
        self.assertEqual(TargetProtectionVerdict.ALLOWED,target_protection_verdict(replace(base,acting_controller='A')))
        self.assertEqual(TargetProtectionVerdict.ALLOWED,target_protection_verdict(replace(base,source_qualities=replace(source,colors=frozenset({'R'})))))
        self.assertTrue(qualified_hexproof_prohibits({'hexproof from black'},None))
        for colors,mono,multi in (((),False,False),(('R',),True,False),(('R','W'),False,True)):
            facts=replace(source,colors=frozenset(colors))
            self.assertEqual(mono,qualified_hexproof_prohibits({'hexproof from monocolored'},facts))
            self.assertEqual(multi,qualified_hexproof_prohibits({'hexproof from multicolored'},facts))
        for name in ('Hexproof from black','Hexproof from each color','Hexproof from blue and from black'):
            self.assertTrue(qualified_hexproof_keywords(name))
            ir=compile_oracle_card(replace(record(name,'Creature'),keywords=('Hexproof',)),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
        for text in ('Hexproof from purple','Hexproof from black except on Tuesdays','Hexproof from your choice of colors'):
            self.assertIsNone(qualified_hexproof_keywords(text))
        self.assertIn('hexproof',keyword_type_terms(('hexproof from black',)))
        self.assertTrue(keyword_is_removed('Hexproof From Black','Hexproof'));self.assertFalse(keyword_is_removed('Flying','Hexproof'))
        self.assertTrue(TargetGroup.from_mapping({'keywords_all':['hexproof']}).matches_keyword_characteristics(keywords=('hexproof from blue',)))
        self.assertFalse(TargetGroup.from_mapping({'keywords_none':['hexproof']}).matches_keyword_characteristics(keywords=('hexproof from blue',)))
        with self.assertRaises(TargetProtectionError):replace(base,source_qualities={})

    def test_actual_valkyrie_retains_flying_lifelink_and_paid_once_after_attack_boast(self):
        session=self.session(750160);source=self.add(session.engine,'Eradicator Valkyrie');source.acquired_control_turn_count=-1
        fodder=self.add(session.engine,'Grizzly Bears',ref='boast-cost-creature')
        opponents=[self.add(session.engine,'Grizzly Bears',seat=s,ref='boast-opponent-'+s) for s in 'BCD']
        for i in range(2):self.add(session.engine,'Swamp',ref='boast-swamp-'+str(i))
        self.prepare(session);self.checkpoint(session)
        early=next((r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':')),None)
        if early is not None:
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':early['id'],'cost_cards':[fodder.ref],'pay':'auto'})
            self.assertFalse(result.ok,'Boast must reject activation before this creature attacks')
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.attack(session,{source.ref:'B'});self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual((44,36),(session.state.players['A'].life,session.state.players['B'].life))
        self.priority(session,'A');ability=next(r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':ability['id'],'cost_cards':[fodder.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[fodder.object_id].zone)
        for card in opponents:
            self.assertIsNotNone(self.resolve(session));seat=card.owner
            result=session.act('pilot:'+seat,{'action_id':'choose','cards':[card.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in opponents))
        self.priority(session,'A');self.assertFalse(any(r['id']==ability['id'] for r in self.actions(session)));self.replay(session)

    def test_actual_hexproof_prohibition_and_ability_loss_revalidate_an_inflight_target(self):
        session=self.session(750161);target=self.add(session.engine,'General Ferrous Rokiric',seat='B')
        archetype=self.add(session.engine,'Archetype of Endurance',zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand');humble=self.add(session.engine,'Humble',zone='hand')
        self.prepare(session,mana={'G':2,'C':7,'W':1,'R':1});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+bolt.ref,'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',archetype,payment={'G':2,'C':6});self.assertIsNone(self.resolve(session))
        self.assertNotIn('hexproof from monocolored',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.cast(session,'A',bolt,[target.ref],{'R':1});self.cast(session,'A',humble,[archetype.ref],{'W':1,'C':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn('hexproof from monocolored',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone);self.assertEqual(0,session.state.cards[target.object_id].marked_damage);self.replay(session)

    def test_actual_changed_source_uses_departure_lki_and_not_a_returned_red_incarnation(self):
        for i,return_source in enumerate((False,True)):
            with self.subTest(return_source=return_source):self.source_lki_witness(return_source,750170+i)

    def source_lki_witness(self,return_source,seed):
        session=self.session(seed);target=self.add(session.engine,'Sporeweb Weaver')
        source=self.add(session.engine,'Prodigal Pyromancer',seat='B');source.acquired_control_turn_count=-1
        frog=self.add(session.engine,'Turn to Frog',zone='hand');bounce=self.add(session.engine,'Boomerang',zone='hand')
        blink=self.add(session.engine,'Cloudshift',seat='B',zone='hand');self.add(session.engine,'Plains',seat='B')
        for j in range(4):self.add(session.engine,'Island',ref='lki-island-'+str(j))
        self.prepare(session,actor='B',active='B');self.checkpoint(session)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        ability=next(r for r in actions if r['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        incarnation=source.logical_object_id
        self.cast(session,'A',frog,[source.ref]);self.top(session)
        self.assertEqual(['U'],session.engine._effective_card_data(session.state.cards[source.object_id])['colors'])
        self.cast(session,'B' if return_source else 'A',blink if return_source else bounce,[source.ref]);self.top(session)
        self.assertEqual('battlefield' if return_source else 'hand',session.state.cards[source.object_id].zone)
        self.assertNotEqual(incarnation,session.state.cards[source.object_id].logical_object_id)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((0,40),(session.state.cards[target.object_id].marked_damage,session.state.players['A'].life))
        self.assertEqual([],self.tokens(session,'saproling'));self.replay(session)

    def test_actual_departure_source_quality_is_blue_after_the_original_red_source_changes(self):
        self.source_lki_witness(False,750175)

    def test_actual_clone_copies_the_qualified_keyword_without_gaining_plain_hexproof(self):
        session=self.session(750180);source=self.add(session.engine,'Knight of Grace',seat='B')
        clone=self.add(session.engine,'Clone',zone='hand');red=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        black=self.add(session.engine,'Murder',seat='B',zone='hand')
        self.prepare(session,mana={'U':1,'C':3});session.state.players['B'].mana_pool.update({'R':1,'B':2,'C':1});self.checkpoint(session)
        self.cast(session,'A',clone,payment={'U':1,'C':3});decision=self.resolve(session);self.assertIsNotNone(decision)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        option=next(r for r in options if source.ref in r['id'] and not r['id'].startswith('decline:'))
        result=session.act('pilot:A',{'action_id':'choose','replacement':option['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));copied=session.state.cards[clone.object_id]
        self.assertIn('hexproof from black',session.engine._combat_keywords(copied));self.assertNotIn('hexproof',session.engine._combat_keywords(copied))
        self.priority(session,'B');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+black.ref,'targets':[copied.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',red,[copied.ref],{'R':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[clone.object_id].zone);self.replay(session)

    def test_actual_qualified_grant_adds_one_variant_and_keeps_nonmatching_red_targeting(self):
        session=self.session(750190);target=self.add(session.engine,'Knight of Grace')
        grant=self.add(session.engine,'Generic Qualified Hexproof Grant',zone='hand')
        blue=self.add(session.engine,'Boomerang',seat='B',zone='hand');red=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        self.assertTrue(self.binding(session,grant)['strict_capability_ready'])
        self.prepare(session,mana={'G':1});session.state.players['B'].mana_pool.update({'U':2,'R':1});self.checkpoint(session)
        self.cast(session,'A',grant,[target.ref],{'G':1});self.assertIsNone(self.resolve(session))
        keywords=session.engine._combat_keywords(session.state.cards[target.object_id])
        self.assertTrue({'hexproof from black','hexproof from blue'}.issubset(keywords));self.assertNotIn('hexproof',keywords)
        self.priority(session,'B');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+blue.ref,'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',red,[target.ref],{'R':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.replay(session)

    def test_literal_hexproof_removal_removes_qualified_variants(self):
        session=self.session(750191);target=self.add(session.engine,'General Ferrous Rokiric',seat='B')
        remove=self.add(session.engine,'Generic Hexproof Removal',seat='B',zone='hand');red=self.add(session.engine,'Lightning Bolt',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'G':1});session.state.players['A'].mana_pool['R']=1;self.checkpoint(session)
        self.cast(session,'B',remove,[target.ref],{'G':1});self.assertIsNone(self.resolve(session))
        self.assertNotIn('hexproof from monocolored',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.cast(session,'A',red,[target.ref],{'R':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.replay(session)

    def test_later_plain_hexproof_grant_invalidates_a_previously_legal_blue_spell(self):
        session=self.session(750192);target=self.add(session.engine,'Knight of Grace')
        guile=self.add(session.engine,"Ranger's Guile",zone='hand');blue=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'U':2});session.state.players['A'].mana_pool['G']=1;self.checkpoint(session)
        self.cast(session,'B',blue,[target.ref],{'U':2});self.cast(session,'A',guile,[target.ref],{'G':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        current=session.state.cards[target.object_id];self.assertEqual('battlefield',current.zone)
        self.assertTrue({'hexproof','hexproof from black'}.issubset(session.engine._combat_keywords(current)))
        self.assertEqual((3,3),(session.engine._numeric_stat(current.object_id,'power'),session.engine._numeric_stat(current.object_id,'toughness')));self.replay(session)

    def test_printed_plain_and_qualified_hexproof_coexist_through_normal_registration(self):
        session=self.session(750193);target=self.add(session.engine,'Generic Plain and Qualified Guard')
        blue=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        self.assertTrue(self.binding(session,target)['strict_capability_ready'])
        self.assertTrue({'hexproof','hexproof from black'}.issubset(session.engine._combat_keywords(target)))
        self.prepare(session,actor='B',active='B',mana={'U':2});self.checkpoint(session);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+blue.ref,'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.replay(session,load=True)

    def test_monocolored_hexproof_allows_actual_colorless_ability_and_multicolored_spell(self):
        session=self.session(750194);target=self.add(session.engine,'General Ferrous Rokiric')
        murder=self.add(session.engine,'Murder',seat='B',zone='hand');helix=self.add(session.engine,'Lightning Helix',seat='B',zone='hand')
        icy=self.add(session.engine,'Icy Manipulator',seat='B')
        self.prepare(session,actor='B',active='B',mana={'B':2,'C':1,'R':1,'W':1});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+murder.ref,'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        ability=next(r for r in actions if r['id'].startswith('activate:'+icy.ref+':'))
        result=session.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref],'pay':'manual','payment':{'C':1}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertTrue(session.state.cards[target.object_id].tapped)
        self.cast(session,'B',helix,[target.ref],{'R':1,'W':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual(43,session.state.players['B'].life)
        self.assertEqual([],self.tokens(session,'golem'));self.replay(session)

    def test_qualified_keyword_is_hexproof_for_both_shared_query_owners(self):
        from quorune.object_query import ObjectQuerySpec,object_query_result,object_matches_query
        from quorune.targets import TargetGroup
        session=self.session(750195);source=self.add(session.engine,'Knight of Grace')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        data=session.engine._effective_card_data(source)
        row=object_query_result(source,data,type_parts=session.engine._type_parts(data['type_line']),known_to_actor=True,attached_to_ref=None)
        self.assertTrue(object_matches_query(row,ObjectQuerySpec(zones=('battlefield',),keywords_all=('hexproof',))))
        self.assertFalse(object_matches_query(row,ObjectQuerySpec(zones=('battlefield',),keywords_none=('hexproof',))))
        self.assertTrue(TargetGroup.from_mapping({'keywords_all':['hexproof']}).matches_keyword_characteristics(keywords=row.keywords))

    def test_fresh_lki_capture_query_family_and_actual_removal_omissions_are_killed(self):
        method='test_actual_departure_source_quality_is_blue_after_the_original_red_source_changes'
        with patch('quorune.target_source.pin_targeting_source_departures',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_literal_hexproof_removal_removes_qualified_variants'
        with patch('quorune.continuous_effects.keyword_is_removed',side_effect=lambda actual,requested:str(actual).casefold()==str(requested).casefold()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_qualified_keyword_is_hexproof_for_both_shared_query_owners'
        for owner in ('quorune.object_query.keyword_type_terms','quorune.targets.keyword_type_terms'):
            with patch(owner,side_effect=lambda values:{str(v).casefold() for v in values}):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_own_matching_destruction_and_destination_replacement_preserve_the_protected_incarnation(self):
        session=self.session(750197);target=self.add(session.engine,'Knight of Grace')
        replacements=[self.add(session.engine,'Dauthi Voidwalker',seat='B',ref='qualified-replacement-'+key) for key in '12']
        matching=self.add(session.engine,'Murder',seat='B',zone='hand')
        own=self.add(session.engine,'Murder',seat='A',zone='hand',ref='own-qualified-murder')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'B':2,'C':1})
        session.state.players['A'].mana_pool.update({'B':2,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,target)['strict_capability_ready'])
        for card in replacements:
            programs=session.engine.semantics.programs_for_oracle(card.oracle_id)
            self.assertTrue(programs)
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in programs))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+matching.ref,'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',own,[target.ref],{'B':2,'C':1})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual(('pilot:A',),tuple(session.pending_principals()))
        session=self.replay(session,load=True)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertEqual(2,len(options));before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'choose','replacement':options[0]['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','replacement':options[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(('exile',1),(session.state.cards[target.object_id].zone,session.state.cards[target.object_id].counters.get('void',0)))
        self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertEqual(('pilot:A',),tuple(session.pending_principals()))
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        result=session.act('pilot:A',{'action_id':'choose','replacement':options[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));moved=session.state.cards[target.object_id]
        self.assertEqual(('exile',1),(moved.zone,moved.counters.get('void',0)))
        self.assertNotIn(target.object_id,session.state.players['A'].zones['graveyard'])
        self.assertEqual((2,1),(session.state.players['B'].mana_pool['B'],session.state.players['B'].mana_pool['C']))
        self.assertEqual((0,0),(session.state.players['A'].mana_pool['B'],session.state.players['A'].mana_pool['C']))
        self.assertEqual(('exile',1),(session.state.cards[own.object_id].zone,session.state.cards[own.object_id].counters.get('void',0)))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in replacements))
        self.private_replay(session,private)

    def test_verified_withdrawals_and_runtime_minimum_stripping_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        path=ROOT/'quorune/rules/capability-registry.json';baseline=json.loads(path.read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        def verified(raw):
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Knight of Grace'),capability_registry=verified(baseline),capability_profile='commander_review').status)
        cap='target.protection.hexproof_qualified'
        raw=json.loads(path.read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn qualified Hexproof authority'])
        ir=compile_oracle_card(self.db.lookup('Knight of Grace'),capability_registry=verified(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750196)
        for name in ('Knight of Grace','Generic Qualified Hexproof Grant'):
            source=self.add(session.engine,name,zone='hand')
            program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
