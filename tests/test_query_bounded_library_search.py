"""Pinned search count is resolved once, then shared by private offer and command."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.library_search_model import (
    QueryBoundLibrarySearchSpec,QUERY_BOUND_SEARCH_VERSION as VERSION,
    QUERY_BOUND_SEARCH_CAPABILITY as CAP,QUERY_BOUND_SEARCH_RESOLVED as RESOLVED,
    counted_search_bound_dependencies,
)
from quorune.compiler.query_bound_search_templates import query_bound_library_search_template
from quorune.rules.query_bound_library_search import materialize_query_bound_search
from quorune.rules.library_search_capability_shapes import fixed_counted_library_search_node_capabilities
from quorune.model import CardInstance
from quorune.errors import GameRuleError

FIXTURE=ROOT/'tests/fixtures/query-bounded-library-search-cards.json'
PREFIX='Search your library for up to X basic land cards, where X is '
BF=', put those cards onto the battlefield tapped, then shuffle.'
HAND='. Reveal those cards, put them into your hand, then shuffle.'

def template(quantity='the number of lands you control',hand=False):
    return query_bound_library_search_template(PREFIX+quantity+(HAND if hand else BF))

class QueryBoundSearchModelTests(unittest.TestCase):
    def test_exact_supported_quantities_and_placements_keep_unknown_siblings_closed(self):
        for quantity in ('the number of lands you control','the number of tapped creatures you control',
                'the number of creatures attacking you','the number of Shrines you control'):
            spec=template(quantity);self.assertIsInstance(spec,QueryBoundLibrarySearchSpec)
            effect=spec.effect();self.assertEqual(VERSION,effect['schema_version'])
            self.assertEqual({'minimum':0,'maximum':None},effect['count'])
            self.assertTrue(effect['enters_tapped_override']);self.assertEqual('battlefield',effect['destination'])
            self.assertEqual(effect,QueryBoundLibrarySearchSpec.from_effect(json.loads(json.dumps(effect))).effect())
        hand=template('the number of colors among permanents you control',True)
        self.assertEqual(('hand',True,False),(hand.destination,hand.reveal,hand.enters_tapped))
        for bad in (PREFIX+'the number of lands you control'+BF+' Draw a card.',
                (PREFIX+'the number of lands you control'+BF).replace('basic land','nonbasic land'),
                (PREFIX+'the number of lands you control'+BF).replace('basic land','creature'),
                PREFIX+'the greatest power among creatures you control'+BF,
                PREFIX+'the number of cards in your hand'+BF):
            self.assertIsNone(query_bound_library_search_template(bad))
    def test_raw_transport_has_no_client_literal_limit_or_unowned_snapshot(self):
        effect=template().effect()
        for change in ({'schema_version':True},{'schema_version':7},{'count':{'minimum':0,'maximum':3}},
                {'selector':{'types':['land']}},{'shuffle_after':False},{'reveal':True},
                {'destination':'exile'},{'extra':1},{RESOLVED:{}}):
            with self.assertRaises((ValueError,TypeError,KeyError)):QueryBoundLibrarySearchSpec.from_effect({**effect,**change})
    def host(self,count=3):
        cards={str(i):CardInstance(object_id=str(i),ref='public-land-'+str(i),oracle_id='fixture:query-land',
            printed_name='Public land',owner='A',controller='A',zone='battlefield') for i in range(count)}
        player=SimpleNamespace(zones={'battlefield':list(cards)},in_game=True)
        host=SimpleNamespace(state=SimpleNamespace(players={'A':player,'B':SimpleNamespace(zones={'battlefield':[]},in_game=True)},cards=cards),
            _effective_card_data=lambda card,**kwargs:{'type_line':'Basic Land — Forest','colors':[],
                'keywords':[],'power':None,'toughness':None},
            _type_parts=lambda text:({'land'},{'forest'},{'basic'}))
        return host,SimpleNamespace(ref='STACK',controller='A',context={})
    def test_count_is_frozen_once_and_large_or_empty_bounds_are_not_fixed_search_limits(self):
        for count in (0,3,13):
            host,item=self.host(count);resolved=materialize_query_bound_search(host,item,template().effect(),0)
            self.assertEqual(count,resolved['count']['maximum'])
            self.assertEqual(resolved,materialize_query_bound_search(host,item,json.loads(json.dumps(resolved)),0))
            host.state.players['A'].zones['battlefield'].clear()
            self.assertEqual(count,materialize_query_bound_search(host,item,resolved,0)['count']['maximum'])
            self.assertEqual(count,materialize_query_bound_search(host,item,template().effect(),0)['count']['maximum'])
    def test_saved_count_rejects_changed_stack_cache_controller_pointer_quantity_or_literal(self):
        host,item=self.host();resolved=materialize_query_bound_search(host,item,template().effect(),2)
        for change in ({'count':{'minimum':0,'maximum':4}},
                {RESOLVED:{**resolved[RESOLVED],'binding_id':'query-search:3'}},
                {'bound':template('the number of tapped creatures you control').effect()['bound']}):
            with self.assertRaises((GameRuleError,ValueError)):materialize_query_bound_search(host,item,{**resolved,**change},2)
        for changed in (SimpleNamespace(ref='OTHER',controller='A',context={}),
                SimpleNamespace(ref='STACK',controller='B',context=item.context)):
            with self.assertRaises(GameRuleError):materialize_query_bound_search(host,changed,resolved,2)
        with self.assertRaises(GameRuleError):materialize_query_bound_search(host,item,resolved,3)
    def test_payload_minimum_is_independent_of_coverage_and_resolved_internal_data_is_not_a_template(self):
        spec=template();effect=spec.effect()
        self.assertIn(CAP,counted_search_bound_dependencies(effect))
        compiled=spec.compiled();self.assertIn(CAP,fixed_counted_library_search_node_capabilities(
            effects=compiled[1],target_schema=None,mechanic_ids=compiled[3]))
        host,item=self.host();resolved=materialize_query_bound_search(host,item,effect,0)
        with self.assertRaises(ValueError):counted_search_bound_dependencies(resolved)
        self.assertEqual((),fixed_counted_library_search_node_capabilities(effects=(resolved,),target_schema=None,mechanic_ids=compiled[3]))


from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as compiler_controls
import test_public_condition_self_replacements as controls

CANDIDATES=('Boundless Realms','Harvest Season',"Jaheira's Respite",'Kyoshi Island Plaza','Prismatic Undercurrents')

class QueryBoundSearchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()
    def test_complete_source_bound_and_sibling_sequence_compile_without_weakening(self):
        for text in (PREFIX+'the number of lands you control'+BF,
                PREFIX+'the number of creatures attacking you'+BF+' Prevent all combat damage that would be dealt this turn.'):
            ir=compile_oracle_card(compiler_controls.record(text),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertIn(CAP,node.capability_dependencies)
            self.assertEqual(VERSION,node.effects[0]['schema_version'])
        for text in (PREFIX+'the greatest power among creatures you control'+BF,
                PREFIX+'the number of lands you control'+BF+' Exile the top ten cards of your library.'):
            self.assertNotEqual('exact',compile_oracle_card(compiler_controls.record(text),capability_registry=self.registry).status)
    def test_lowering_and_new_capability_withdrawals_keep_old_fixed_search_supported(self):
        text=PREFIX+'the number of lands you control'+BF
        with patch('quorune.compiler.query_bound_search_templates.query_bound_library_search_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(compiler_controls.record(text),capability_registry=self.registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['query bound withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual('exact',compile_oracle_card(compiler_controls.record(text),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(compiler_controls.record('Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.'),capability_registry=registry).status)

class QueryBoundSearchRegistrationTests(unittest.TestCase):
    def test_five_complete_originals_retain_every_sibling_and_unknown_quantity_neighbors(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'complete-query-search.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,'Traverse the Outlands',"Surveyor's Scope",'Cultivate',"Kodama's Reach",'Explosive Vegetation','Rampant Growth'):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name not in ('Traverse the Outlands',"Surveyor's Scope"),bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])

class QueryBoundSearchRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    play_land=controls.PublicConditionSelfReplacementTests.play_land
    advance=controls.PublicConditionSelfReplacementTests.advance
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'query-search.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bounded basic search',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def world(self,name,seed,lands=3,*,controls=()):
        s=self.start(seed);spell=self.add(s.engine,name,zone='hand')
        own=[self.add(s.engine,'Forest',ref='query-controlled-'+str(i)) for i in range(lands)]
        choices=[self.add(s.engine,n,zone='library',ref='query-choice-'+str(i)) for i,n in enumerate(('Forest','Island','Plains','Mountain','Swamp'))]
        nonbasic=self.add(s.engine,'Command Tower',zone='library',ref='query-not-basic')
        secret=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='query-private-c')
        for i,n in enumerate(controls):self.add(s.engine,n,seat='B',ref='query-control-'+str(i))
        self.assertTrue(self.binding(s,spell)['strict_capability_ready'],self.binding(s,spell)['blockers'])
        return s,spell,own,choices,nonbasic,secret
    def begin(self,s,spell):
        self.ready(s);self.cast(s,spell);d=self.resolve(s)
        self.assertIsNotNone(d);self.assertEqual('semantic.search',d.kind)
        return s.packet('pilot:A',full=True)['decision']['ctx']
    def take(self,s,cards):
        result=s.act('pilot:A',{'action_id':'choose','search_cards':[c.ref for c in cards]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
    def test_actual_boundless_search_freezes_count_rejects_overflow_and_preserves_private_replay(self):
        s,spell,own,choices,nonbasic,secret=self.world('Boundless Realms',927810)
        before=s.state.players['A'].stats.get('shuffle_count',0);ctx=self.begin(s,spell)
        schema=ctx['legal_actions'][0]['choice_schema'];self.assertEqual(3,schema['maximum'])
        self.assertNotIn(nonbasic.ref,schema['legal_refs'])
        for seat in 'BCD':
            packet=str(s.packet('pilot:'+seat,full=True));self.assertNotIn(choices[0].ref,packet)
        self.assertNotIn(secret.ref,str(s.packet('pilot:A',full=True)))
        command={'action_id':'choose','search_cards':[c.ref for c in choices[:3]]};self.reject(s,command,'C')
        self.reject(s,{'action_id':'choose','search_cards':[c.ref for c in choices[:4]]},'A')
        self.reject(s,{'action_id':'choose','search_cards':[nonbasic.ref]},'A')
        s=self.replay(s,load=True);self.take(s,choices[:3])
        for card in choices[:3]:
            current=s.state.cards[card.object_id];self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
        self.assertEqual(6,len([oid for oid in s.state.players['A'].zones['battlefield'] if 'Land' in s.engine._effective_card_data(s.state.cards[oid])['type_line']]))
        self.assertEqual(before+1,s.state.players['A'].stats.get('shuffle_count',0));self.replay(s,load=True)
    def test_payload_minimum_remains_required_when_coverage_and_declaration_are_stripped(self):
        s,spell,*_=self.world('Boundless Realms',927811)
        p=next(p for p in s.engine.semantics.programs_for_oracle(spell.oracle_id) if CAP in p.capability_dependencies)
        deps=tuple(c for c in p.capability_dependencies if c!=CAP)
        changed=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_actual_zero_and_thirteen_bounds_still_search_once_and_shuffle_once(self):
        for count in (0,13):
            with self.subTest(count=count):
                s,spell,own,choices,nonbasic,secret=self.world('Boundless Realms',927820+count,lands=count)
                before=s.state.players['A'].stats.get('shuffle_count',0);ctx=self.begin(s,spell)
                schema=ctx['legal_actions'][0]['choice_schema'];self.assertEqual(count,schema['maximum'])
                refs=schema['legal_refs'][:count]
                s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','search_cards':refs});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s));self.assertEqual(before+1,s.state.players['A'].stats.get('shuffle_count',0))
                self.assertEqual(2*count,len(s.state.players['A'].zones['battlefield']))
                for ref in refs:
                    card=next(c for c in s.state.cards.values() if c.ref==ref);self.assertTrue(card.tapped)
                self.replay(s,load=True)
    def test_actual_harvest_counts_tapped_own_creatures_at_resolution_after_response(self):
        s,spell,own,choices,nonbasic,secret=self.world('Harvest Season',927840)
        tapped=[self.add(s.engine,'Grizzly Bears',ref='harvest-tapped-'+str(i)) for i in range(2)]
        for c in tapped:c.tapped=True
        self.add(s.engine,'Llanowar Elves',ref='harvest-untapped')
        foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='harvest-opponent');foreign.tapped=True
        bounce=self.add(s.engine,'Unsummon',zone='hand');self.ready(s);self.cast(s,spell);self.cast(s,bounce,[tapped[0].ref])
        d=self.resolve(s);self.assertEqual('semantic.search',d.kind)
        self.assertEqual('hand',s.state.cards[tapped[0].object_id].zone)
        schema=s.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema'];self.assertEqual(1,schema['maximum'])
        self.reject(s,{'action_id':'choose','search_cards':[c.ref for c in choices[:2]]},'A')
        s=self.replay(s,load=True);self.take(s,choices[:1]);self.assertTrue(s.state.cards[choices[0].object_id].tapped);self.replay(s,load=True)
    def test_actual_shrine_entry_includes_itself_and_other_shrine_has_separate_fixed_search(self):
        s,spell,own,choices,nonbasic,secret=self.world('Kyoshi Island Plaza',927850)
        other=self.add(s.engine,'Honden of Cleansing Fire',zone='hand');self.assertTrue(self.binding(s,other)['strict_capability_ready'])
        ctx=self.begin(s,spell);self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.assertEqual(1,ctx['legal_actions'][0]['choice_schema']['maximum']);self.take(s,choices[:1])
        self.cast(s,other);d=self.resolve(s);self.assertEqual('semantic.search',d.kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(1,ctx['legal_actions'][0]['choice_schema']['maximum'])
        self.take(s,choices[1:2]);self.assertEqual(2,s.state.players['A'].stats.get('shuffle_count',0))
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in choices[:2]));self.replay(s,load=True)
    def test_actual_vivid_distinct_colors_reveal_hand_lands_and_offer_two_land_plays(self):
        s,spell,own,choices,nonbasic,secret=self.world('Prismatic Undercurrents',927860)
        for name in ('Fervor','Honden of Cleansing Fire'):self.add(s.engine,name)
        ctx=self.begin(s,spell);self.assertEqual(3,ctx['legal_actions'][0]['choice_schema']['maximum'])
        self.take(s,choices[:3])
        for card in choices[:3]:
            self.assertEqual('hand',s.state.cards[card.object_id].zone)
            for seat in 'ABCD':self.assertIn(card.ref,str(s.packet('pilot:'+seat,full=True)))
        self.play_land(s,choices[0]);self.play_land(s,choices[1]);self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r.get('card')==choices[2].ref and r.get('kind')!='cast' for r in actions))
        self.reject(s,{'action_id':'play_land:'+choices[2].ref},'A');self.replay(s,load=True)
    def test_actual_search_window_and_prohibition_preserve_required_whole_library_shuffle(self):
        for control in ('Aven Mindcensor','Mindlock Orb'):
            with self.subTest(control=control):
                s,spell,own,choices,nonbasic,secret=self.world('Boundless Realms',927870+(control=='Mindlock Orb'),controls=(control,))
                if control=='Aven Mindcensor':
                    for i in range(4):self.add(s.engine,'Grizzly Bears',zone='library',ref='query-top-nonland-'+str(i))
                self.ready(s);before=s.state.players['A'].stats.get('shuffle_count',0);self.cast(s,spell);d=self.resolve(s)
                if control=='Mindlock Orb':self.assertIsNone(d)
                else:
                    self.assertEqual('semantic.search',d.kind)
                    ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual([],ctx['search_cards'])
                    self.assertNotIn(choices[0].ref,str(ctx));self.reject(s,{'action_id':'choose','search_cards':[choices[0].ref]},'A');self.take(s,[])
                self.assertEqual(before+1,s.state.players['A'].stats.get('shuffle_count',0))
                self.assertEqual('library',s.state.cards[choices[0].object_id].zone);self.replay(s,load=True)
    def test_fault_after_multi_land_movement_and_shuffle_rolls_back_then_retries(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        s,spell,own,choices,nonbasic,secret=self.world('Boundless Realms',927880);self.begin(s,spell)
        before=authoritative_state_hash(s.state);original=ZoneTransitionOwner.shuffle_library;partial=[]
        def fault(owner,*args,**kwargs):
            value=original(owner,*args,**kwargs)
            partial.append(tuple(s.state.cards[c.object_id].zone for c in choices[:3]));raise GameRuleError('Injected search post-shuffle fault')
        command={'action_id':'choose','search_cards':[c.ref for c in choices[:3]]}
        with patch.object(ZoneTransitionOwner,'shuffle_library',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok,result.summary)
        self.assertEqual([('battlefield',)*3],partial);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.take(s,choices[:3]);self.replay(s,load=True)
    def test_fresh_inflated_maximum_mutation_is_killed_by_actual_overflow_rejection(self):
        import quorune.rules.query_bound_library_search as owner
        original=owner.materialize_query_bound_search
        def mutant(*args,**kwargs):
            effect=original(*args,**kwargs)
            return {**effect,'count':{**effect['count'],'maximum':effect['count']['maximum']+1}}
        with patch.object(owner,'materialize_query_bound_search',side_effect=mutant):
            with self.assertRaises(AssertionError):self.test_actual_boundless_search_freezes_count_rejects_overflow_and_preserves_private_replay()

    def test_actual_jaheira_counts_direct_attacks_then_applies_fog_after_search(self):
        s,spell,own,choices,nonbasic,secret=self.world("Jaheira's Respite",927900,lands=5)
        import test_combat_prevention_source_scopes as combat
        self.reach=lambda game,kind,seat=None:combat.CombatPreventionSourceRuntimeTests.reach(self,game,kind,seat)
        direct=[self.add(s.engine,'Grizzly Bears',seat='B',ref='respite-direct-'+str(i)) for i in range(2)]
        other=self.add(s.engine,'Grizzly Bears',seat='B',ref='respite-other-player')
        flyer=self.add(s.engine,'Wind Drake',seat='B');walker=self.add(s.engine,'Jace Beleren');walker.counters['loyalty']=3
        pinger=self.add(s.engine,'Prodigal Pyromancer',seat='B');self.add(s.engine,'Fervor',seat='B')
        self.ready(s);combat.CombatPreventionSourceRuntimeTests.attack(self,s,'B',[(c,'A') for c in direct]+[(other,'C'),(flyer,walker.ref)])
        self.cast(s,spell);d=self.resolve(s);self.assertEqual('semantic.search',d.kind)
        self.assertFalse(s.state.damage_prevention_shields)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(2,ctx['legal_actions'][0]['choice_schema']['maximum'])
        s=self.replay(s,load=True);self.take(s,choices[:2]);self.assertEqual(1,len(s.state.damage_prevention_shields))
        self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r['id'].startswith('activate:'+pinger.ref+':'))
        result=s.act('pilot:B',{'action_id':row['id'],'targets':['A'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(39,s.state.players['A'].life)
        self.advance(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual(39,s.state.players['A'].life);self.assertEqual(40,s.state.players['C'].life);self.assertEqual(3,s.state.cards[walker.object_id].counters['loyalty'])
        self.assertEqual(1,s.state.players['A'].stats.get('shuffle_count',0));self.replay(s,load=True)
    def test_actual_vivid_extra_land_permission_disappears_after_source_destruction(self):
        s,spell,own,choices,nonbasic,secret=self.world('Prismatic Undercurrents',927910)
        destroy=self.add(s.engine,'Naturalize',zone='hand')
        extra=self.add(s.engine,'Island',zone='hand',ref='query-extra-land')
        self.begin(s,spell);self.take(s,choices[:1]);self.play_land(s,choices[0]);self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(r for r in actions if r.get('card')==extra.ref and r.get('kind')!='cast')
        self.cast(s,destroy,[spell.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r.get('card')==extra.ref and r.get('kind')!='cast' for r in actions))
        self.reject(s,{'action_id':offered['id']},'A');self.replay(s,load=True)
    def test_actual_conflicting_entry_replacements_suspend_entire_land_batch_and_preserve_frozen_bound(self):
        s,spell,own,choices,nonbasic,secret=self.world('Boundless Realms',927920)
        untapped=self.add(s.engine,'Archelos, Lagoon Mystic',ref='entry-untapped')
        tapped=self.add(s.engine,'Archelos, Lagoon Mystic',seat='B',ref='entry-tapped');tapped.tapped=True
        self.assertTrue(self.binding(s,untapped)['strict_capability_ready']);self.assertTrue(self.binding(s,tapped)['strict_capability_ready'])
        self.begin(s,spell);result=s.act('pilot:A',{'action_id':'choose','search_cards':[c.ref for c in choices[:3]]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone=='library' for c in choices[:3]))
        s=self.replay(s,load=True);seen=0
        while s.state.pending_decision.kind=='replacement.order':
            seen+=1;self.assertLessEqual(seen,6);ctx=s.packet('pilot:A',full=True)['decision']['ctx']
            option=ctx['options'][0]['id'];command={'action_id':'choose','replacement':option};self.reject(s,command,'D')
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertTrue(all(s.state.cards[c.object_id].zone=='battlefield' for c in choices[:3]))
        self.assertEqual(1,s.state.players['A'].stats.get('shuffle_count',0));self.replay(s,load=True)
