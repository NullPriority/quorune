"""CR603.2: scheduled and public occurrences independently trigger one ability."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_source_attack_alone as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.fixed_public_multi_event_bindings import fixed_public_multi_event_binding_spec
from scripts.build_test_database import build_fixture_database

CAPABILITY='trigger.subscription.fixed_multi_event'
TEXT='At the beginning of your upkeep and whenever you cast a green spell, put a charge counter on this artifact.'
ENTRY='When this enchantment enters and at the beginning of your first main phase, exile target creature an opponent controls until this enchantment leaves the battlefield.'


class ScheduledPairCompilerTests(unittest.TestCase):
    def test_paid_ordered_body_binds_the_nested_sequence_and_rejects_malformed_children(self):
        from quorune.oracle_ir import generated_programs
        from quorune.rules.closed_effect_program_shapes import closed_effect_program_runtime_dependencies
        from quorune.card_programs.binding import bind_semantic_program_runtime
        registry=load_default_capability_registry()
        class NoRulings:
            def rulings(self,record):return ()
        record=replace(query_record('Whenever another artifact you control enters or leaves the battlefield, you may pay {1}. If you do, target player loses 1 life and you gain 1 life.'),type_line='Artifact Creature — Construct')
        programs=tuple(generated_programs(NoRulings(),record,trust_level='trusted',capability_registry=registry,capability_profile='commander_review'))
        program=next(p for p in programs if p.event_condition and 'event_subscriptions' in p.event_condition)
        self.assertEqual(1,len(program.effects));self.assertEqual(2,len(program.effects[0]['effects']))
        owned=closed_effect_program_runtime_dependencies(program);self.assertIn('resolution.effect_program.closed_components',owned)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=registry,profile='commander_review')['strict'])
        for effects in ([],[{'op':'draw','player':'$controller','count':1}],deepcopy(program.effects)):
            changed=deepcopy(effects)
            if changed and changed[0].get('op')=='offer_optional_mana_payment':changed[0]['effects'][1]={'op':'unknown'}
            with self.assertRaises(ValueError):closed_effect_program_runtime_dependencies(replace(program,effects=changed))
        for cap in ('resolution.effect_program.closed_components','resolution.effect_program.bound_references','effect.choice.optional_fixed_mana_payment'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_schedule_and_cast_keep_separate_complete_predicates_and_one_source_span(self):
        binding=fixed_public_multi_event_binding_spec(TEXT,card_name='Generic Query Observer')
        self.assertEqual([
            {'event':'step.begin','condition':{'all':[{'field':'player','op':'eq','value':'$source.controller'},{'field':'step','op':'eq','value':'upkeep'}]}},
            {'event':'spell.cast','condition':{'all':[{'field':'controller','op':'eq','value':'$source.controller'},{'field':'colors','op':'contains_any','value':['G']}]}}
        ],binding.condition['event_subscriptions'])
        ir=compile_oracle_card(replace(query_record(TEXT),type_line='Artifact'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
        self.assertEqual((TEXT,0,len(TEXT)),(node.text,node.span.start,node.span.end));self.assertEqual(1,len(node.effects))
        self.assertTrue({CAPABILITY,'trigger.event.normalized_spell_cast'}<=set(node.capability_dependencies))

    def test_entry_and_first_main_retain_source_identity_phase_and_independent_result_scope(self):
        registry=load_default_capability_registry();ir=compile_oracle_card(replace(query_record(ENTRY),type_line='Enchantment'),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
        entry,step=node.event_condition['event_subscriptions'];self.assertEqual('permanent.enter',entry['event'])
        self.assertEqual({'field':'card','op':'eq','value':'$source.ref'},entry['condition'])
        self.assertEqual('step.begin',step['event']);self.assertIn({'field':'phase','op':'eq','value':'precombat_main'},step['condition']['all'])
        self.assertIn('zone.exile_until_source_departure.fixed',node.capability_dependencies)
        for text in ('When this creature enters and at the beginning of your upkeep, surveil 1.',
                'At the beginning of your upkeep and when this creature enters, draw a card.'):
            self.assertEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_nested_unknown_relative_mixed_zone_and_duplicate_event_pairs_remain_residual(self):
        registry=load_default_capability_registry()
        for text in (
            'At the beginning of your upkeep and whenever you cast an unknown spell, draw a card.',
            'At the beginning of your upkeep and when this creature dies, draw a card.',
            'At the beginning of your upkeep and when you cycle this card, draw a card.',
            'At the beginning of your upkeep and at the beginning of your end step, draw a card.',
            'When this creature enters and at the beginning of your upkeep and whenever you cast a green spell, draw a card.',
            'At the beginning of your upkeep and whenever you cast a creature spell, put a +1/+1 counter on that creature.',
            TEXT+' Rewrite history.',
        ):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def test_verified_each_event_dependency_withdrawal_keeps_single_upkeep_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in (CAPABILITY,'trigger.event.normalized_spell_cast'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['scheduled child authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(query_record('At the beginning of your upkeep, draw a card.'),capability_registry=registry).status)


class ScheduledPairNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.SourceAttackAloneNativeTests.session
    add=native.SourceAttackAloneNativeTests.add
    prepare=native.SourceAttackAloneNativeTests.prepare
    cast=native.SourceAttackAloneNativeTests.cast
    cast_for=native.SourceAttackAloneNativeTests.cast_for
    action=native.SourceAttackAloneNativeTests.action
    priority=native.SourceAttackAloneNativeTests.priority
    checkpoint=native.SourceAttackAloneNativeTests.checkpoint
    resolve=native.SourceAttackAloneNativeTests.resolve
    resolve_top=native.SourceAttackAloneNativeTests.resolve_top
    replay=native.SourceAttackAloneNativeTests.replay
    assert_closed=native.SourceAttackAloneNativeTests.assert_closed
    until=native.SourceAttackAloneNativeTests.until
    trigger_count=native.SourceAttackAloneNativeTests.trigger_count

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'scheduled-pairs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/scheduled-event-pair-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scheduled pairs',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def step(self,session,seat,phase,step):
        from quorune.engine import TURN_STEPS
        # A prepared checkpoint immediately before the boundary isolates the
        # actual pass-driven scheduler transition without four unrelated turns.
        index=TURN_STEPS.index((phase,step));self.assertGreater(index,0)
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.active_player=seat;session.state.phase,session.state.step=TURN_STEPS[index-1]
        session.state.phase_index=index-1;session.state.priority_passes=[];engine._grant_priority(seat);engine.pump();self.checkpoint(session)
        self.until(session,lambda s:s.state.phase==phase and s.state.step==step)

    def test_actual_growth_uses_controller_step_and_color_then_pays_its_sacrificed_counter_amount(self):
        session=self.session(603644001);source=self.add(session.engine,'Shrine of Boundless Growth',zone='hand')
        green=self.add(session.engine,'Giant Growth',zone='hand');red=self.add(session.engine,'Shock',zone='hand');body=self.add(session.engine,'Grizzly Bears')
        foreign=self.add(session.engine,'Giant Growth',seat='B',zone='hand',ref='foreign-green-spell');self.add(session.engine,'Forest',seat='B')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'G':1,'R':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'G':1,'R':1});self.assertIsNone(self.resolve(session));self.assertEqual({},session.state.cards[source.object_id].counters)
        self.cast_for(session,'A',green,[body.ref],reserve={'R':1});self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[source.object_id].counters.get('charge',0))
        self.cast(session,'B',foreign,[body.ref]);self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[source.object_id].counters.get('charge',0))
        self.cast(session,'A',red,['B']);self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[source.object_id].counters.get('charge',0))
        self.step(session,'B','beginning','upkeep');self.assertEqual(0,self.trigger_count(session,source));self.assertEqual(1,session.state.cards[source.object_id].counters.get('charge',0))
        self.step(session,'A','beginning','upkeep');self.assertEqual(1,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(2,session.state.cards[source.object_id].counters.get('charge',0))
        self.priority(session,'A');offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+session.state.cards[source.object_id].ref+':'))
        before=authoritative_state_hash(session.state);command={'action_id':offered['id'],'pay':'auto'};self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(2,session.state.players['A'].mana_pool['C']);self.replay(session,load=True)

    def test_actual_legions_queued_upkeep_survives_sacrifice_and_creates_exact_typed_tokens(self):
        session=self.session(603644010);source=self.add(session.engine,'Shrine of Loyal Legions',zone='hand');white=self.add(session.engine,'Healing Salve',zone='hand')
        for i in range(3):self.add(session.engine,'Plains',ref='legions-payment-'+str(i))
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='legions-foreign-private')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'W':1});self.assertIsNone(self.resolve(session))
        self.priority(session,'A');result=session.act('pilot:A',{'action_id':'cast:'+white.ref,'modes':['mode_1'],'targets':['A'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[source.object_id].counters.get('charge',0))
        self.step(session,'A','beginning','upkeep');self.assertEqual(1,self.trigger_count(session,source));self.priority(session,'A')
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+session.state.cards[source.object_id].ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));token=tokens[0];self.assertEqual('A',token.controller);self.assertEqual('token',token.object_kind)
        self.assertEqual((1,1),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
        types,subtypes,_=session.engine._type_parts(session.engine._effective_card_data(token)['type_line']);self.assertTrue({'creature','artifact'}<=set(types));self.assertTrue({'phyrexian','myr'}<=set(subtypes))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def choose_target(self,session,target):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        before=authoritative_state_hash(session.state);command={'action_id':'choose','targets':[session.state.cards[target.object_id].ref]}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_crack_keeps_both_exiles_until_source_leaves_and_skips_other_main_phases(self):
        session=self.session(603644020);source=self.add(session.engine,'Crack in Time',zone='hand');first=self.add(session.engine,'Grizzly Bears',seat='B',ref='crack-first');second=self.add(session.engine,'Grizzly Bears',seat='C',ref='crack-second')
        unrelated=self.add(session.engine,'Grizzly Bears',zone='exile',ref='crack-unlinked');removal=self.add(session.engine,'Disenchant',zone='hand')
        for i in range(2):self.add(session.engine,'Plains',ref='crack-removal-payment-'+str(i))
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session);self.cast(session,'A',source);self.choose_target(session,first);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[first.object_id].zone);self.step(session,'A','postcombat_main','main');self.assertEqual(0,self.trigger_count(session,source))
        self.step(session,'B','precombat_main','main');self.assertEqual(0,self.trigger_count(session,source))
        self.step(session,'A','precombat_main','main');self.choose_target(session,second)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual('exile',session.state.cards[second.object_id].zone)
        self.cast(session,'A',removal,[session.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(session))
        for card in (first,second):self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
        self.assertEqual('exile',session.state.cards[unrelated.object_id].zone);self.replay(session,load=True)

    def test_actual_borrowed_shrine_uses_controller_upkeep_and_keeps_queued_actor_after_removal(self):
        session=self.session(603644025);source=self.add(session.engine,'Shrine of Boundless Growth');removal=self.add(session.engine,'Disenchant',zone='hand')
        source.owner='A';session.engine.change_control(source.object_id,'B',reason='Prepared foreign-owned Shrine')
        self.add(session.engine,'Plains');self.add(session.engine,'Plains',ref='borrowed-shrine-payment')
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='borrowed-shrine-private');self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.step(session,'A','beginning','upkeep');self.assertEqual(0,self.trigger_count(session,source));self.assertEqual(0,session.state.cards[source.object_id].counters.get('charge',0))
        self.step(session,'B','beginning','upkeep');trigger=next(i for i in session.state.stack if i.source_object_id==source.object_id)
        self.assertEqual('B',trigger.controller);self.cast(session,'A',removal,[source.ref]);self.resolve_top(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertIn(source.object_id,session.state.players['A'].zones['graveyard'])
        self.assertEqual('B',next(i for i in session.state.stack if i.source_object_id==source.object_id).controller)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(0,session.state.cards[source.object_id].counters.get('charge',0))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_crack_does_not_exile_after_its_source_left_before_resolution(self):
        session=self.session(603644027);source=self.add(session.engine,'Crack in Time',zone='hand');target=self.add(session.engine,'Grizzly Bears',seat='B')
        removal=self.add(session.engine,'Disenchant',seat='B',zone='hand');self.add(session.engine,'Plains',seat='B');self.add(session.engine,'Plains',seat='B',ref='crack-foreign-payment')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session);self.cast(session,'A',source);self.choose_target(session,target)
        self.assertEqual(1,self.trigger_count(session,source));self.cast(session,'B',removal,[session.state.cards[source.object_id].ref]);self.resolve_top(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone);self.replay(session,load=True)

    def test_actual_sludge_retains_paid_entry_and_declined_departure_results(self):
        session=self.session(603644029);source=self.add(session.engine,'Sludge Strider');artifact=self.add(session.engine,'Sol Ring',zone='hand');removal=self.add(session.engine,'Disenchant',zone='hand')
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='sludge-private');self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session);self.cast_for(session,'A',artifact,reserve={'C':2,'W':1})
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        result=session.act('pilot:A',{'action_id':'choose','targets':['C']});self.assertTrue(result.ok,result.summary);decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        life={s:session.state.players[s].life for s in 'ABCD'};before=authoritative_state_hash(session.state);command={'action_id':'choose','pay':True}
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(life['A']+1,session.state.players['A'].life);self.assertEqual(life['C']-1,session.state.players['C'].life)
        self.cast(session,'A',removal,[session.state.cards[artifact.object_id].ref]);self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        result=session.act('pilot:A',{'action_id':'choose','targets':['B']});self.assertTrue(result.ok,result.summary);decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        before_life={s:session.state.players[s].life for s in 'ABCD'};result=session.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(before_life,{s:session.state.players[s].life for s in 'ABCD'});self.assertEqual('graveyard',session.state.cards[artifact.object_id].zone)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_runtime_multi_subscription_dependency_is_inferred_without_coverage_or_declared_capability(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603644030);source=self.add(session.engine,'Shrine of Boundless Growth');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            for cap in (CAPABILITY,'trigger.event.normalized_spell_cast'):
                checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=cap)
                changed=replace(program,coverage=tuple(c for c in program.coverage if c!='fixed_multi_event_subscription'),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])
        self.assertGreater(checked,0)

    def test_omitted_schedule_controller_predicate_mutant_is_killed(self):
        import quorune.compiler.fixed_public_multi_event_bindings as owner
        original=owner._scheduled_pair_spec
        def witness():
            session=self.session(603644040);source=self.add(session.engine,'Shrine of Boundless Growth',seat='B')
            self.prepare(session);self.step(session,'A','beginning','upkeep')
            self.assertEqual(0,self.trigger_count(session,source),'A foreign upkeep must not trigger the Shrine')
        witness()
        def omitted(*args,**kwargs):
            value=original(*args,**kwargs)
            if value is None:return value
            condition=deepcopy(value.condition)
            for child in condition['event_subscriptions']:
                if child['event']=='step.begin':child['condition']['all']=[c for c in child['condition']['all'] if c.get('field')!='player']
            return replace(value,condition=condition)
        with patch.object(owner,'_scheduled_pair_spec',side_effect=omitted),self.assertRaises(AssertionError):witness()
