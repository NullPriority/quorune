from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls
import test_ability_label_composition as journeys

NAMES=('Gravecrawler','Seedborn Muse','Ichor Wellspring','Delighted Halfling')


class EmptyPermanentViewTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.StaticPlayerRestrictionRuntimeTests.session
    add=controls.StaticPlayerRestrictionRuntimeTests.add
    prepare=controls.StaticPlayerRestrictionRuntimeTests.prepare
    checkpoint=controls.StaticPlayerRestrictionRuntimeTests.checkpoint
    resolve=controls.StaticPlayerRestrictionRuntimeTests.resolve
    replay=controls.StaticPlayerRestrictionRuntimeTests.replay
    private_replay=controls.StaticPlayerRestrictionRuntimeTests.private_replay
    priority=controls.StaticPlayerRestrictionRuntimeTests.priority
    actions=controls.StaticPlayerRestrictionRuntimeTests.actions
    cast=controls.StaticPlayerRestrictionRuntimeTests.cast
    reject=controls.StaticPlayerRestrictionRuntimeTests.reject
    activate=controls.StaticPlayerRestrictionRuntimeTests.activate
    binding=controls.StaticPlayerRestrictionRuntimeTests.binding
    advance=journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'empty-permanent.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/empty-permanent-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Native empty permanent views',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_all_originals_have_normal_complete_binding_and_single_current_cast_authority(self):
        for i,name in enumerate(NAMES):
            s=self.session(822000+i);source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
            old={p.key for p in SemanticRegistry().programs_for_oracle(source.oracle_id) if p.ability_id=='spell:front' and not p.effects and not p.handlers}
            self.assertTrue(old,name);self.assertFalse(old & set(s.engine.semantics.keys()))

    def test_supersession_requires_complete_current_source_and_every_generated_dependency(self):
        from quorune.card_programs.empty_permanent_equivalence import shadowed_reviewed_empty_permanent_keys as shadow
        s=self.session(822010);source=self.add(s.engine,NAMES[2]);record=self.db.lookup(NAMES[2])
        current=s.engine.semantics.programs_for_oracle(source.oracle_id)
        reviewed=SemanticRegistry().programs_for_oracle(source.oracle_id);old=next(p for p in reviewed if p.ability_id=='spell:front')
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        def check(g=current,r=reviewed,card=record,proof=ir,caps=self.registry):
            return shadow(card,g,r,capability_registry=caps,capability_profile='commander_review',oracle_ir=proof)
        self.assertIn(old.key,check())
        for malformed in (replace(old,effects=[{'op':'draw','player':'$controller','count':1}]),replace(old,target_schema={'zones':['battlefield'],'count':1,'creature':True}),replace(old,cost_schema={'cost_text':'{1}'}),replace(old,event_condition={'field':'controller','op':'eq','value':'A'}),replace(old,destination='hand'),replace(old,provenance={**old.provenance,'source_rulings_hash':'0'*64})):
            self.assertNotIn(old.key,check(r=[malformed]))
        self.assertFalse(check(g=[replace(p,trust_level='provisional') for p in current]))
        self.assertFalse(check(g=[],caps=None));self.assertFalse(check(card=replace(record,layout='transform')))
        partial=SimpleNamespace(oracle_id=record.oracle_id,oracle_hash=ir.oracle_hash,status='partial',material_residuals=())
        self.assertFalse(check(proof=partial))
        changed=list(current);changed[0]=replace(changed[0],capability_dependencies=[],capability_closure=None)
        self.assertFalse(check(g=changed))
        stale=replace(current[0],provenance={**current[0].provenance,'source_oracle_hash':'0'*64})
        self.assertFalse(check(g=[stale,*current[1:]]))

    def test_real_gravecrawler_hand_and_graveyard_casts_require_current_owned_zombie(self):
        s=self.session(822020);crawler=self.add(s.engine,NAMES[0],zone='hand');zombie=self.add(s.engine,'Walking Corpse',zone='hand')
        self.add(s.engine,'Walking Corpse',seat='B',ref='opponent_walking_corpse');first=self.add(s.engine,'Village Rites',zone='hand',ref='crawler_first_rites');second=self.add(s.engine,'Village Rites',zone='hand',ref='crawler_second_rites')
        bounce=self.add(s.engine,'Unsummon',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'B':6,'C':1,'U':1});self.checkpoint(s)
        self.cast(s,crawler,payment={'B':1});self.assertIsNone(self.resolve(s))
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id']=='cast:'+first.ref)
        result=s.act('pilot:A',{'action_id':row['id'],'sacrifice_cards':[crawler.ref],'pay':'manual','payment':{'B':1}});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[crawler.object_id].zone);self.assertIsNone(self.resolve(s));old=s.state.cards[crawler.object_id].logical_object_id
        self.priority(s,'A');self.assertNotIn('cast:'+crawler.ref,{r['id'] for r in self.actions(s)})
        self.reject(s,{'action_id':'cast:'+crawler.ref,'pay':'manual','payment':{'B':1}})
        self.cast(s,zombie,payment={'C':1,'B':1});self.assertIsNone(self.resolve(s))
        self.cast(s,crawler,payment={'B':1});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertNotEqual(old,s.state.cards[crawler.object_id].logical_object_id)
        self.cast(s,bounce,[zombie.ref],payment={'U':1});self.assertIsNone(self.resolve(s))
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id']=='cast:'+second.ref)
        result=s.act('pilot:A',{'action_id':row['id'],'sacrifice_cards':[crawler.ref],'pay':'manual','payment':{'B':1}});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.priority(s,'A');self.assertNotIn('cast:'+crawler.ref,{r['id'] for r in self.actions(s)});self.reject(s,{'action_id':'cast:'+crawler.ref,'pay':'manual','payment':{'B':1}});self.private_replay(s,private)

    def test_real_gravecrawler_cannot_be_submitted_as_a_blocker(self):
        s=self.session(822030);crawler=self.add(s.engine,NAMES[0]);body=self.add(s.engine,'Generic Bound Body')
        attacker=self.add(s.engine,'Fugitive Wizard',seat='B');haste=self.add(s.engine,'Expedite',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,actor='B',active='B',mana={'R':1});self.checkpoint(s);self.cast(s,haste,[attacker.ref],seat='B',payment={'R':1});self.assertIsNone(self.resolve(s))
        self.advance(s,lambda x:x.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:B',{'action_id':'attack','atk':{attacker.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.pending_decision.kind=='combat.blockers')
        self.reject(s,{'action_id':'block','blocks':{crawler.ref:attacker.ref}})
        result=s.act('pilot:A',{'action_id':'block','blocks':{body.ref:attacker.ref}});self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.combat.attackers);self.private_replay(s,private)

    def test_real_seedborn_cast_other_player_untap_and_source_departure(self):
        s=self.session(822040);source=self.add(s.engine,NAMES[1],zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand')
        ring=self.add(s.engine,'Sol Ring');land=self.add(s.engine,'Mountain');island=self.add(s.engine,'Island');body=self.add(s.engine,'Fugitive Wizard')
        enemy=self.add(s.engine,'Forest',seat='B');foreign=self.add(s.engine,'Sol Ring',seat='C',ref='foreign_sol_ring')
        for card in (ring,land,island,body,enemy,foreign):card.tapped=True
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s);self.cast(s,source,payment={'C':3,'G':2});self.assertIsNone(self.resolve(s));self.assertTrue(s.state.cards[ring.object_id].tapped)
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.step=='upkeep' and x.state.pending_decision.kind=='priority')
        self.assertFalse(any(s.state.cards[c.object_id].tapped for c in (ring,land,island,body,enemy)));self.assertTrue(s.state.cards[foreign.object_id].tapped)
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main' and x.state.pending_decision.kind=='priority')
        self.cast(s,bounce,[source.ref]);self.assertIsNone(self.resolve(s));self.activate(s,ring);self.activate(s,land)
        self.advance(s,lambda x:x.state.active_player=='C' and x.state.step=='upkeep' and x.state.pending_decision.kind=='priority')
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in (ring,land,island)));self.assertFalse(s.state.cards[foreign.object_id].tapped);self.private_replay(s,private)

    def test_real_ichor_entry_and_foreign_controller_death_draw_are_private(self):
        s=self.session(822050);source=self.add(s.engine,NAMES[2],zone='hand');steal=self.add(s.engine,'Steal Artifact',seat='B',zone='hand');destroy=self.add(s.engine,'Disenchant',zone='hand')
        self.add(s.engine,'Plains');self.add(s.engine,'Sol Ring')
        for i in range(4):self.add(s.engine,'Island',seat='B',ref='ichor_enemy_island'+str(i))
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');before=len(s.state.players['A'].zones['hand'])
        self.prepare(s,mana={'C':2});self.checkpoint(s);self.cast(s,source,payment={'C':2});self.assertIsNone(self.resolve(s));self.assertEqual(before,len(s.state.players['A'].zones['hand']))
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main' and x.state.pending_decision.kind=='priority')
        self.cast(s,steal,[source.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        a=set(s.state.players['A'].zones['hand']);b=set(s.state.players['B'].zones['hand'])
        self.cast(s,destroy,[source.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(len(a)-1,len(s.state.players['A'].zones['hand']))
        drawn=set(s.state.players['B'].zones['hand'])-b;self.assertEqual(1,len(drawn));ref=s.state.cards[next(iter(drawn))].ref
        for seat in 'ACD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.assertIn(ref,json.dumps(s.packet('pilot:B',full=True)));self.private_replay(s,private)

    def halfling_mana(self,s,source,*,restricted,color=None):
        self.priority(s,'A')
        ability=next(a for a in s.engine._activated_abilities(s.state.cards[source.object_id]) if a.mana_ability and bool(a.mana_spend_restriction)==restricted)
        row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':') and r['id'].endswith(':'+ability.ability_id))
        command={'action_id':row['id'],'pay':'auto'}
        if color:command['mana_choice']=color
        self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_real_halfling_actual_spending_limits_and_counter_attempts_survive_source_loss(self):
        s=self.session(822060);source=self.add(s.engine,NAMES[3],zone='hand');haste=self.add(s.engine,'Expedite',zone='hand');untap=self.add(s.engine,'Burst of Strength',zone='hand')
        ordinary=self.add(s.engine,'Hope of Ghirapur',zone='hand');protected=self.add(s.engine,'Isamaru, Hound of Konda',zone='hand');forbidden=self.add(s.engine,'Thraben Inspector',zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand')
        counter1=self.add(s.engine,'Counterspell',seat='B',zone='hand',ref='halfling_counter1');counter2=self.add(s.engine,'Counterspell',seat='B',zone='hand',ref='halfling_counter2');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U']=4;self.prepare(s,mana={'G':2,'R':1,'U':1});self.checkpoint(s)
        self.cast(s,source,payment={'G':1});self.assertIsNone(self.resolve(s));self.cast(s,haste,[source.ref],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.halfling_mana(s,source,restricted=False);self.cast(s,ordinary,payment={'C':1});ordinary_stack=s.state.stack[-1].ref
        self.cast(s,counter1,[ordinary_stack],seat='B',payment={'U':2});self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[ordinary.object_id].zone)
        self.cast(s,untap,[source.ref],payment={'G':1});self.assertIsNone(self.resolve(s));self.halfling_mana(s,source,restricted=True,color='W')
        self.assertNotIn('cast:'+forbidden.ref,{r['id'] for r in self.actions(s)});self.reject(s,{'action_id':'cast:'+forbidden.ref,'pay':'manual','payment':{'W':1}})
        self.cast(s,protected,payment={'W':1});protected_stack=s.state.stack[-1].ref;self.cast(s,bounce,[source.ref],payment={'U':1})
        while s.state.stack and s.state.cards[source.object_id].zone=='battlefield':
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[source.object_id].zone);s=self.replay(s,load=True)
        self.cast(s,counter2,[protected_stack],seat='B',payment={'U':2});self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[protected.object_id].zone);self.assertEqual('graveyard',s.state.cards[counter2.object_id].zone);self.private_replay(s,private)

    def test_fresh_supersession_omission_keeps_original_cards_unclosed(self):
        with patch('quorune.card_programs.empty_permanent_equivalence.shadowed_reviewed_empty_permanent_keys',return_value=set()):
            with self.assertRaises(AssertionError):type(self)('test_all_originals_have_normal_complete_binding_and_single_current_cast_authority').test_all_originals_have_normal_complete_binding_and_single_current_cast_authority()
