from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.rules.reveal_additional_cost import FixedRevealAdditionalCost,REVEAL_ADDITIONAL_COST_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as bound


CLAUSE='As an additional cost to cast this spell, reveal a Kithkin card from your hand or pay {3}.'
NAMES=['Goldmeadow Stalwart','Daring Buccaneer','Squeaking Pie Sneak','Sadistic Skymarcher',
    'Flamekin Bladewhirl','Silvergill Adept',"Wren's Run Vanquisher",'Thunderherd Migration']


class FixedRevealCostCompilerTests(unittest.TestCase):
    def test_generic_reveal_or_mana_price_is_source_pinned_and_closed(self):
        ir=compile_oracle_card(record(CLAUSE+'\nDraw a card.','Sorcery'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertIn(REVEAL_ADDITIONAL_COST_CAPABILITY,node.capability_dependencies)
        self.assertEqual(CLAUSE+'\nDraw a card.',node.text)
        reveal,mana=node.cost['additional_costs'][0]['options']
        self.assertEqual({'schema_version':1,'kind':'reveal_from_hand','subtype':'kithkin'},reveal)
        self.assertEqual(3,mana['requirements']['GENERIC'])

    def test_malformed_unknown_optional_and_linked_reveals_remain_closed(self):
        value=FixedRevealAdditionalCost('kithkin').to_descriptor()
        self.assertEqual(value,FixedRevealAdditionalCost.from_descriptor(value).to_descriptor())
        for change in ({'schema_version':True},{'subtype':'unknown tribe'},{'count':2},{'kind':'discard'},{'subtype':'Kithkin'}):
            with self.subTest(change=change),self.assertRaises(ValueError):FixedRevealAdditionalCost.from_descriptor({**value,**change})
        for clause in (CLAUSE.replace('Kithkin','unknown tribe'),CLAUSE.replace('reveal a','you may reveal a'),
                CLAUSE.replace('from your hand','from your graveyard'),CLAUSE.replace('{3}','{X}')):
            self.assertNotEqual('exact',compile_oracle_card(record(clause+'\nDraw a card.','Sorcery'),capability_registry=load_default_capability_registry()).status)


class FixedRevealCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=bound.BoundEffectProgramRuntimeTests.session
    add=bound.BoundEffectProgramRuntimeTests.add
    checkpoint=bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve=bound.BoundEffectProgramRuntimeTests.resolve
    replay=bound.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name)/'reveal-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-reveal-additional-cost-cards.json'],p)
        cls.db=CardDatabase(p);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Reveal cost witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fund(self,session,mana,actor='A'):
        e=session.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_passes=[]
        e.state.active_player='A';e.state.phase='precombat_main';e.state.step='main'
        e.state.players[actor].mana_pool.update(mana);e._grant_priority(actor);e.pump();self.checkpoint(session)

    def offered(self,session,source,actor='A'):
        return next(a for a in session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)

    def reject(self,session,actor='A',**response):
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(session.state);result=session.act('pilot:'+actor,response)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_all_originals_have_complete_independent_bindings(self):
        for name in NAMES:
            with self.subTest(name=name):
                card=self.db.lookup(name);program=compile_best_available_card_program(self.db,card,
                    semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

    def test_actual_stalwart_reveal_and_mana_prices_use_one_native_cast_and_replay(self):
        for reveal in (True,False):
            with self.subTest(reveal=reveal):
                session=self.session(601130);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='stalwart-spell')
                other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='stalwart-reveal')
                other.revealed_to=[]
                self.fund(session,{'W':1,'C':3});offered=self.offered(session,source)
                self.assertEqual(2,len(offered['cost_options']))
                option=next(o for o in offered['cost_options'] if ('Reveal' in o['label'])==reveal)
                response={'action_id':offered['id'],'cost_option':option['id'],'pay':'auto'}
                if reveal:
                    self.assertNotIn(source.ref,option['choice_schema']['reveal_cards']['legal_refs'])
                    self.reject(session,**{**response,'reveal_cards':[source.ref]})
                    response['reveal_cards']=[other.ref]
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary)
                self.assertEqual(3 if reveal else 0,session.state.players['A'].mana_pool['C'])
                self.assertEqual('hand',session.state.cards[other.object_id].zone)
                self.assertEqual(set('ABCD') if reveal else set(),set(session.state.cards[other.object_id].revealed_to))
                session=self.replay(session,load=True)
                self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                self.assertFalse(session.state.cards[other.object_id].revealed_to)
                if reveal:self.assertEqual(set('ABCD'),set(session.state.cards[other.object_id].known_to))
                self.replay(session,load=True)

    def test_reveal_choices_reject_foreign_stale_wrong_subtype_and_duplicate_cards_before_mutation(self):
        session=self.session(601131);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='choice-source')
        other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='choice-other');other.revealed_to=[]
        foreign=self.add(session.engine,'Goldmeadow Stalwart',seat='B',zone='hand',ref='choice-foreign')
        wrong=self.add(session.engine,'Generic Bound Plains',zone='hand',ref='choice-wrong')
        self.fund(session,{'W':1});offer=self.offered(session,source)
        option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
        fields=option['choice_schema']['reveal_cards'];self.assertEqual([other.ref],fields['legal_refs'])
        base={'action_id':offer['id'],'cost_option':option['id'],'pay':'auto'}
        for refs in ([],[foreign.ref],[wrong.ref],[other.ref,other.ref],[source.ref],['missing']):
            self.reject(session,**base,reveal_cards=refs)
        self.reject(session,'B',**base,reveal_cards=[other.ref])
        for viewer in 'BCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True));self.assertNotIn(other.ref,packet);self.assertNotIn(wrong.ref,packet)
        session.engine.move_card(other.object_id,'graveyard',reason='Stale reveal selection boundary')
        self.reject(session,**base,reveal_cards=[other.ref]);self.assertFalse(session.state.cards[other.object_id].zone=='hand')

    def test_revealed_card_stays_public_through_saved_stack_and_countering_ends_only_the_duration(self):
        session=self.session(601132);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='countered-stalwart')
        other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='countered-reveal');other.revealed_to=[]
        private=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='unselected-private')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='reveal-counterspell')
        session.state.players['B'].mana_pool.update({'U':2});self.fund(session,{'W':1})
        session=self.replay(session,load=True);offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
        result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);spell=session.state.stack[-1]
        for viewer in 'BCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True));self.assertIn(other.ref,packet);self.assertNotIn(private.ref,packet)
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        result=session.act('pilot:B',{'action_id':'cast:'+counter.ref,'targets':[spell.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual('hand',session.state.cards[other.object_id].zone)
        self.assertFalse(session.state.cards[other.object_id].revealed_to)
        self.assertEqual(set('ABCD'),set(session.state.cards[other.object_id].known_to))
        self.assertFalse(session.state.players['A'].mana_pool['W']);self.replay(session,load=True)

    def test_overlapping_native_spells_keep_the_same_hand_card_revealed_until_both_leave(self):
        session=self.session(601133)
        self.add(session.engine,'Leyline of Anticipation',ref='overlap-flash')
        first=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='overlap-first')
        second=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='overlap-second')
        other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='overlap-reveal');other.revealed_to=[]
        self.fund(session,{'W':2})
        for source in (first,second):
            offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
            result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(session.state.stack));session=self.replay(session,load=True)
        for _ in range(8):
            if len(session.state.stack)==1:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len(session.state.stack));self.assertEqual(set('ABCD'),set(session.state.cards[other.object_id].revealed_to))
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[other.object_id].revealed_to)
        self.replay(session,load=True)

    def test_reveal_and_total_cost_branches_keep_reductions_and_free_base_costs_distinct(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        session=self.session(601134);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='reduced-source')
        self.add(session.engine,'Generic Casting Reveal Reducer',ref='reveal-cost-reducer')
        self.fund(session,{'W':1})
        offer=self.offered(session,source);self.assertEqual(1,len(offer['cost_options']))
        option=offer['cost_options'][0];self.assertEqual(0,option['requirements']['GENERIC']);self.assertEqual(1,option['requirements']['W'])
        result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.replay(session,load=True)
        session=self.session(601135);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='free-base-source')
        other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='free-base-reveal');self.fund(session,{'C':3})
        from quorune.rules.casting.proposal import _spell_semantic_key
        program=session.engine.semantics.get(_spell_semantic_key(self.db.lookup('Goldmeadow Stalwart'),None))
        options=build_cast_cost_options(session.engine,'A',source,program,hint=True,force_without_mana_cost=True)
        self.assertEqual(2,len(options));requirements=[o.to_dict()['requirements'] for o in options]
        self.assertEqual([0,3],sorted(r['GENERIC'] for r in requirements));self.assertTrue(all(r['W']==0 for r in requirements))

    def test_compiler_runtime_and_disclosure_omission_mutants_are_killed(self):
        with patch('quorune.compiler.spell_additional_cost_templates._fixed_alternative_leaf_template',return_value=None):
            ir=compile_oracle_card(record(CLAUSE+'\nDraw a card.','Sorcery'),capability_registry=self.registry)
            self.assertNotEqual('exact',ir.status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==REVEAL_ADDITIONAL_COST_CAPABILITY).update(status='blocked',blockers=['Reveal cost omitted'])
        self.assertNotEqual('exact',compile_oracle_card(record(CLAUSE+'\nDraw a card.','Sorcery'),capability_registry=CapabilityRegistry(raw)).status)
        with patch('quorune.rules.casting.reveal_costs.begin_stack_cost_hand_reveal',return_value=None):
            with self.assertRaises(AssertionError):self.test_revealed_card_stays_public_through_saved_stack_and_countering_ends_only_the_duration()

    def test_original_creatures_keep_their_bodies_and_actual_silvergill_entry_draw(self):
        for name in NAMES[1:-1]:
            with self.subTest(name=name):
                session=self.session(601136)
                source=self.add(session.engine,name,zone='hand',ref='body-source')
                other=self.add(session.engine,name,zone='hand',ref='body-reveal');other.revealed_to=[]
                self.fund(session,{'W':4,'U':4,'B':4,'R':4,'G':4,'C':8})
                offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
                hand=len(session.state.players['A'].zones['hand'])
                result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                original=self.db.lookup(name)
                self.assertEqual((int(original.power),int(original.toughness)),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
                required={'Squeaking Pie Sneak':{'fear'},'Sadistic Skymarcher':{'flying','lifelink'},"Wren's Run Vanquisher":{'deathtouch'}}.get(name,set())
                self.assertTrue(required.issubset(session.engine._combat_keywords(session.state.cards[source.object_id])))
                self.assertEqual(hand if name=='Silvergill Adept' else hand-1,len(session.state.players['A'].zones['hand']))
                self.assertFalse(session.state.cards[other.object_id].revealed_to);self.replay(session,load=True)

    def test_actual_migration_reveals_a_dinosaur_then_privately_searches_enters_tapped_and_shuffles(self):
        session=self.session(601137);source=self.add(session.engine,'Thunderherd Migration',zone='hand',ref='migration-source')
        other=self.add(session.engine,'Colossal Dreadmaw',zone='hand',ref='migration-dinosaur');other.revealed_to=[]
        self.fund(session,{'G':1,'C':1});offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
        result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('semantic.search',self.resolve(session).kind)
        refs=[r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['search_cards']]
        self.assertTrue(refs);self.reject(session,'B',action_id='choose',search_cards=refs[:1])
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'choose','search_cards':refs[:1]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        land=next(c for c in session.state.cards.values() if c.ref==refs[0]);self.assertEqual('battlefield',land.zone);self.assertTrue(land.tapped)
        self.assertEqual(1,session.state.players['A'].stats['shuffle_count']);self.assertFalse(session.state.cards[other.object_id].revealed_to)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.replay(session,load=True)

    def test_changeling_is_an_eligible_current_hand_card_and_direct_stack_departure_ends_reveal(self):
        session=self.session(601138);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='changeling-source')
        other=self.add(session.engine,'Universal Automaton',zone='hand',ref='changeling-reveal');other.revealed_to=['A']
        self.fund(session,{'W':1});offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
        result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(set('ABCD'),set(session.state.cards[other.object_id].revealed_to))
        # Directly exercise the canonical non-resolution/non-counter stack departure owner.
        session.engine.move_card(source.object_id,'exile',reason='Canonical stack departure boundary')
        self.assertFalse(session.state.stack);self.assertEqual(['A'],session.state.cards[other.object_id].revealed_to)

    def test_immutable_cast_plan_rejects_changed_reveal_incarnation_without_partial_payment(self):
        from quorune.rules.casting.proposal import build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest
        from quorune.rules.casting.commit import commit_cast
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        session=self.session(601139);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='stale-plan-source')
        other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='stale-plan-reveal')
        self.fund(session,{'W':1});offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
        response={'card':source.ref,'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'}
        proposal=build_cast_proposal(session.engine,CastProposalRequest.from_submission('A',response))
        session.engine.move_card(other.object_id,'graveyard',reason='Selected reveal incarnation left hand')
        before=authoritative_state_hash(session.state)
        with self.assertRaises((GameRuleError,ValueError)),session.engine.transaction():commit_cast(session.engine,proposal,response)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual('hand',session.state.cards[source.object_id].zone)

    def test_copied_stack_context_cannot_end_original_disclosure_and_malformed_frames_fail_closed(self):
        from quorune.effect_runtime.hand_inspection import cleanup_stack_cost_hand_reveals
        from quorune.errors import GameRuleError
        session=self.session(601140);source=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='context-source')
        other=self.add(session.engine,'Goldmeadow Stalwart',zone='hand',ref='context-reveal');other.revealed_to=[]
        self.fund(session,{'W':1});offer=self.offered(session,source);option=next(o for o in offer['cost_options'] if 'Reveal' in o['label'])
        result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'reveal_cards':[other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);original=session.state.stack[-1]
        copied=deepcopy(original);copied.ref+='-COPY'
        cleanup_stack_cost_hand_reveals(session.engine,copied)
        self.assertEqual(set('ABCD'),set(session.state.cards[other.object_id].revealed_to))
        malformed=deepcopy(original);malformed.context['casting_hand_reveals']['schema_version']=True
        with self.assertRaises(GameRuleError):cleanup_stack_cost_hand_reveals(session.engine,malformed)
        self.assertEqual(set('ABCD'),set(session.state.cards[other.object_id].revealed_to))
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[other.object_id].revealed_to)
        self.replay(session,load=True)
