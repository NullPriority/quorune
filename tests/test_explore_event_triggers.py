"""Completed creature exploration is distinct from reveal, counters and new objects."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.explore_action_model import CREATURE_EXPLORE_CAPABILITY, CREATURE_EXPLORE_EVENT
from quorune.compiler.explore_event_bindings import explore_event_binding_spec
from quorune.semantic_choices.library_and_hand import ExploreChoiceHandler
from quorune.semantic_runtime.intents import ExploreCompletedIntent
from quorune.semantic_runtime.context import SemanticSourceContext
import quorune.semantic_runtime.explore as owner
from test_explore_rules import _context, _row


class ExploreEventOwnerTests(unittest.TestCase):
    def test_all_outcomes_publish_only_after_complete_process(self):
        explorer = _row('P1', zone='battlefield', logical='logical:explorer', types=('creature',))
        handler = ExploreChoiceHandler()
        for kind in ('land', 'instant', None):
            with self.subTest(kind=kind):
                top = _row('C1', zone='library', logical='logical:top', types=(kind,)) if kind else None
                context = _context(explorer, top)
                prepared = handler.prepare({'op':'explore','player':'A','card':'P1'}, context)
                completed = [intent for intent in prepared.preparation_intents if isinstance(intent, ExploreCompletedIntent)]
                if kind == 'instant':
                    self.assertFalse(completed)
                    for choice in ('top', 'graveyard'):
                        result = handler.complete(SimpleNamespace(effect=prepared.continuation_effect),
                            {'choice':choice}, context.query)
                        self.assertEqual(('creature',), result.intents[-1].explorer_types)
                else:
                    self.assertEqual(1, len(completed))
                    self.assertEqual(('creature',), completed[0].explorer_types)

    def source_host(self):
        from quorune.target_source import TargetingSourceBinding, TARGETING_SOURCE_CONTEXT
        source = SimpleNamespace(object_id='object:P1',ref='P1',logical_object_id='new-incarnation',
            zone='battlefield',controller='A',owner='A')
        binding = TargetingSourceBinding('object:P1','P1','old-incarnation','battlefield','B','Creature — Scout',('G',))
        item = SimpleNamespace(kind='triggered_ability',source_object_id=source.object_id,card_object_id=None,
            context={'source_logical_object_id':'old-incarnation', TARGETING_SOURCE_CONTEXT:binding.to_dict()})
        host = SimpleNamespace(state=SimpleNamespace(cards={source.object_id:source}),
            _effective_card_data=lambda card:{'type_line':'Land — Forest','colors':[]})
        return host,item,source

    def test_current_or_lki_types_keep_old_identity(self):
        host,item,source = self.source_host()
        self.assertEqual(('creature',), owner.materialize_explore_source_types(host,item,{'op':'explore'}))
        item.context['source_logical_object_id'] = 'new-incarnation'
        from quorune.target_source import TARGETING_SOURCE_CONTEXT
        item.context.pop(TARGETING_SOURCE_CONTEXT)
        self.assertEqual(('land',), owner.materialize_explore_source_types(host,item,{'op':'explore'}))
        self.assertIsNone(owner.materialize_explore_source_types(host,item,{'op':'draw'}))
        row = _row('P1', zone='battlefield', logical='new-incarnation', types=('land',))
        query = replace(_context(row,None).query,
            resolution_source=SemanticSourceContext('S1',object_id='object:P1',
                logical_object_id='old-incarnation',card_ref='P1'), explore_source_types=('creature',))
        self.assertEqual(('creature',), owner.explored_object_types(query,'P1','old-incarnation'))
        self.assertEqual(('land',), owner.explored_object_types(query,'P1','new-incarnation'))
        self.assertIsNone(owner.explored_object_types(query,'P2','old-incarnation'))

    def test_qualified_event_and_legacy_payload_share_one_batch(self):
        for types, expected in ((('creature',),2), (('artifact',),1), ((),1), (None,1)):
            with self.subTest(types=types):
                events,logs,batches = [],[],[]
                def dispatch(event, context, *, trigger_batch):
                    events.append((event,dict(context)))
                    trigger_batch.append(event)
                host = SimpleNamespace(state=SimpleNamespace(cards={}),
                    _log=lambda *args,**kwargs:logs.append((args,kwargs)), _dispatch_semantic_event=dispatch)
                intent = ExploreCompletedIntent('A','B','P1','old','empty_library','Explore test',explorer_types=types)
                with patch('quorune.trigger_processing.enqueue_trigger_batch',
                        side_effect=lambda host,batch:batches.append(tuple(batch))):
                    owner.commit_explore_completion(host,intent)
                self.assertEqual(expected,len(events))
                self.assertEqual(1,len(batches))
                self.assertEqual('permanent.explores',events[0][0])
                self.assertEqual({'player':'B','explorer':'P1','explorer_logical_object_id':'old',
                    'result':'empty_library','reason':'Explore test'},events[0][1])
                self.assertEqual(events[0][1],logs[0][0][3])
                if expected == 2:
                    self.assertEqual(CREATURE_EXPLORE_EVENT,events[1][0])
                    self.assertEqual(('B',['creature']),(events[1][1]['controller'],events[1][1]['types']))

    def test_unknown_and_malformed_type_facts_never_become_creature_facts(self):
        row = _row('P1',zone='graveyard',logical='returned',types=('creature',))
        query = _context(row,None).query
        self.assertIsNone(owner.explored_object_types(query,'P1','original'))
        for malformed in (True,'creature',['creature'],('Creature',),('creature','creature'),(1,)):
            with self.subTest(malformed=malformed),self.assertRaises(ValueError):
                ExploreCompletedIntent('A','A','P1','original','empty_library','Explore',explorer_types=malformed)
            with self.assertRaises(ValueError):replace(query,explore_source_types=malformed)
        host,item,_source = self.source_host()
        from quorune.target_source import TARGETING_SOURCE_CONTEXT
        item.context[TARGETING_SOURCE_CONTEXT]['logical_object_id'] = 'unrelated'
        from quorune.errors import GameRuleError
        with self.assertRaises(GameRuleError):owner.materialize_explore_source_types(host,item,{'op':'explore'})

    def test_closed_binding_dependency_and_controller_relation(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from test_granted_skulk import record
        from quorune.trigger_discovery import semantic_event_condition_matches
        line = 'Whenever a creature you control explores, you gain 1 life.'
        binding = explore_event_binding_spec(line)
        self.assertEqual(CREATURE_EXPLORE_EVENT,binding.event)
        self.assertEqual((CREATURE_EXPLORE_CAPABILITY,),binding.capabilities)
        from quorune.semantics import SemanticProgram
        from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
        program = SemanticProgram(key='fixture:explore-event',label='Explore event',
            active_zone='battlefield',event=CREATURE_EXPLORE_EVENT,event_condition=dict(binding.condition))
        required,_blockers = program_dynamic_runtime_requirements(program)
        self.assertIn(CREATURE_EXPLORE_CAPABILITY,required)
        source = SimpleNamespace(object_id='observer',ref='O1',controller='B',owner='A')
        host = SimpleNamespace(state=SimpleNamespace(active_player='D'))
        self.assertTrue(semantic_event_condition_matches(host,binding.condition,source=source,context={'controller':'B'}))
        self.assertFalse(semantic_event_condition_matches(host,binding.condition,source=source,context={'controller':'A'}))
        for text in ('Whenever a permanent you control explores, you gain 1 life.',
            'Whenever one or more creatures you control explore, you gain 1 life.',
            'Whenever a creature explores twice, you gain 1 life.'):
            self.assertIsNone(explore_event_binding_spec(text))
        registry = load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(record(line,'Creature — Human'),
            capability_registry=registry,capability_profile='commander_review').status)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        for capability in (CREATURE_EXPLORE_CAPABILITY,'keyword_action.explore.single','target.ability_source.current_or_lki'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==capability).update(status='blocked',blockers=['explore event owner withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(record(line,'Creature — Human'),
                capability_registry=CapabilityRegistry(changed),capability_profile='commander_review').status)

    def test_current_incarnation_and_owner_substitution_mutants_are_killed(self):
        import quorune.target_source as source_owner
        with patch.object(source_owner,'stack_targeting_source',
                side_effect=lambda host,item:source_owner.ability_targeting_source(host,host.state.cards[item.source_object_id])):
            with self.assertRaises(AssertionError):self.test_current_or_lki_types_keep_old_identity()
        import quorune.trigger_discovery as event_owner
        original = event_owner.semantic_event_value
        with patch.object(event_owner,'semantic_event_value',side_effect=lambda host,value,**kwargs:
                kwargs['source'].owner if value == '$source.controller' else original(host,value,**kwargs)):
            with self.assertRaises(AssertionError):self.test_closed_binding_dependency_and_controller_relation()

    def test_public_source_results_reuse_closed_sequence_and_incarnation_owners(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.rules.node_capability_shapes import temporary_declaration_restriction_node_capabilities
        from test_granted_skulk import record
        registry = load_default_capability_registry()
        for body,kind,operations in (
            ('Put a +1/+1 counter on this Vehicle.','Artifact — Vehicle',('place_counters',)),
            ('Put a +1/+1 counter on this creature and you gain 3 life.','Creature — Human',('place_counters','life')),
            ("This creature gets +1/+0 until end of turn and can't be blocked this turn.",'Creature — Human',
                ('apply_source_characteristics_until_end_of_turn','grant_declaration_restriction_until_end_of_turn')),
        ):
            with self.subTest(body=body):
                ir = compile_oracle_card(record('Whenever a creature you control explores, '+body,kind),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIsNone(node.target_schema)
                self.assertEqual(operations,tuple(effect['op'] for effect in node.effects))
                self.assertTrue(all(effect.get('card')=='$source.zone_object' for effect in node.effects if 'card' in effect))
        for body in ('Put a +1/+1 counter on that Vehicle.',
            'Put a +1/+1 counter on this creature and target player gains 3 life.',
            "It gets +1/+0 until end of turn and can't be blocked this turn.",
            "This creature gets +1/+0 until end of turn and can't be blocked by red creatures this turn."):
            self.assertNotEqual('exact',compile_oracle_card(record('Whenever a creature you control explores, '+body,'Creature — Human'),
                capability_registry=registry,capability_profile='commander_review').status,body)
        effect = {'op':'grant_declaration_restriction_until_end_of_turn','card':'$source.zone_object','restriction':'unblockable'}
        mechanics = ('cr-611-continuous-effects','cr-509-declare-blockers-step')
        self.assertTrue(temporary_declaration_restriction_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=mechanics))
        self.assertFalse(temporary_declaration_restriction_node_capabilities(effects=(effect,),
            target_schema={'zones':['battlefield'],'count':1,'types_any':['creature']},mechanic_ids=mechanics))
        self.assertFalse(temporary_declaration_restriction_node_capabilities(effects=({**effect,'_runtime_source':{}},),target_schema=None,mechanic_ids=mechanics))


class ExploreEventNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'explore-event.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-explore-cards.json',ROOT/'tests/fixtures/explore-event-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Explore completion observers',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_crime import CrimeNativeTests as _crime
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _actions
    from source_explore_fixture import SourceExploreFixture as _explore
    session = _crime.session
    start = _crime.start
    cast = _crime.cast
    priority = _crime.priority_to
    assert_closed = _crime.assert_closed
    add = _actions.add
    checkpoint = _actions.checkpoint
    resolve = _actions.resolve
    replay = _actions.replay
    explore_choice = _explore.explore_choice
    attack = _explore.attack
    until = _explore.until

    def resolve_top(self,session):
        top = session.state.stack[-1].ref
        for _ in range(40):
            if not any(item.ref == top for item in session.state.stack):return
            self.assertEqual('priority',session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Explore witness top did not resolve')

    def settle(self,session,*,target=None):
        from quorune.record import authoritative_state_hash
        for _ in range(160):
            session.engine.pump()
            decision = session.state.pending_decision
            principal = session.pending_principals()[0]
            ctx = session.packet(principal,full=True)['decision']['ctx']
            if decision.kind == 'priority':
                if not session.state.stack:return session
                command = {'action_id':'pass'}
            elif decision.kind == 'trigger.order':command = {'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]}
            elif decision.kind == 'semantic.target':
                if target is None:return session
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act(principal,{'action_id':'choose','targets':['EXPLORE_CAVE']}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                session = self.replay(session,load=True)
                command = {'action_id':'choose','targets':[target]}
            elif decision.kind == 'semantic.choice':return session
            else:self.fail('Unexpected explore event decision '+decision.kind)
            result = session.act(principal,command)
            self.assertTrue(result.ok,result.summary)
        self.fail('Explore observer results did not settle')

    def observers(self,session):
        cards = [self.add(session.engine,name,ref=ref) for name,ref in (
            ('Lurking Chupacabra','EXPLORE_CHUPACABRA'),('Merfolk Cave-Diver','EXPLORE_CAVE'),
            ('Shadowed Caravel','EXPLORE_CARAVEL'),('Wildgrowth Walker','EXPLORE_WALKER'))]
        for card in cards:self.assert_closed(session,card)
        return cards

    def test_actual_nonland_pending_completion_targets_and_combat_consequences(self):
        session = self.session(645001)
        _chupa,cave,caravel,walker = self.observers(session)
        source = self.add(session.engine,'Merfolk Branchwalker',zone='hand',ref='EXPLORE_BRANCHWALKER')
        top = self.add(session.engine,'Sol Ring',zone='library',ref='EXPLORE_PUBLIC_TOP')
        hidden = self.add(session.engine,'Divination',seat='D',zone='hand',ref='EXPLORE_PRIVATE_D')
        bear = self.add(session.engine,'Grizzly Bears',seat='B',ref='EXPLORE_TARGET_BEAR')
        blocker = self.add(session.engine,'Ornithopter',seat='B',ref='EXPLORE_UNBLOCKABLE_CONTROL')
        self.assert_closed(session,source)
        self.start(session)
        self.cast(session,source)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual('explore',session.packet('pilot:A',full=True)['decision']['ctx']['operation'])
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual((0,0,40),(session.state.cards[walker.object_id].counters.get('+1/+1',0),
            session.state.cards[caravel.object_id].counters.get('+1/+1',0),session.state.players['A'].life))
        self.assertFalse(any(event.code == 'explore.complete' for event in session.state.events))
        for viewer in 'ABCD':
            packet = json.dumps(session.packet('pilot:'+viewer,full=True))
            self.assertTrue(top.ref in packet,viewer)
            self.assertEqual(viewer=='D',hidden.ref in packet,viewer)
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        before = authoritative_state_hash(session.state)
        original_commit = owner.commit_explore_completion
        def fail_after_completion(host,intent):
            original_commit(host,intent)
            raise GameRuleError('injected after explore completion')
        with patch.object(owner,'commit_explore_completion',side_effect=fail_after_completion):
            failed = session.act('pilot:A',{'action_id':'choose','choice':'graveyard'})
        self.assertFalse(failed.ok)
        self.assertIn('injected after explore completion',failed.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session = self.explore_choice(session,'A','graveyard')
        session = self.settle(session,target=bear.ref)
        self.assertEqual('graveyard',session.state.cards[top.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[bear.object_id].zone)
        self.assertEqual((1,1,43),(session.state.cards[walker.object_id].counters.get('+1/+1',0),
            session.state.cards[caravel.object_id].counters.get('+1/+1',0),session.state.players['A'].life))
        power = int(self.db.lookup('Merfolk Cave-Diver').power)+1
        self.assertEqual(power,session.engine._numeric_stat(cave.object_id,'power'))
        self.attack(session,[(session.state.cards[cave.object_id],'B'),(session.state.cards[walker.object_id],'B')])
        self.until(session,lambda s:s.state.pending_decision.kind=='combat.blockers')
        from quorune.record import authoritative_state_hash
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:cave.ref}})
        self.assertFalse(denied.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result = session.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:walker.ref}})
        self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.players['B'].life==40-power)
        self.assertEqual(40-power,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_actual_land_then_empty_each_publish_one_completed_exploration(self):
        session = self.session(645002)
        _chupa,_cave,caravel,walker = self.observers(session)
        sources = [self.add(session.engine,'Merfolk Branchwalker',zone='hand',ref='EXPLORE_REPEAT_'+str(i)) for i in range(2)]
        for oid in list(session.state.players['A'].zones['library']):
            session.engine.move_card(oid,'exile',reason='Prepared explore library')
        top = self.add(session.engine,'Forest',zone='library',ref='EXPLORE_LAST_LAND')
        bears = [self.add(session.engine,'Grizzly Bears',seat='B',ref='EXPLORE_REPEAT_BEAR_'+str(i)) for i in range(2)]
        self.start(session)
        for i,source in enumerate(sources):
            self.assert_closed(session,source)
            self.cast(session,source)
            session = self.settle(session,target=bears[i].ref)
            self.assertEqual(1 if i else 0,session.state.cards[source.object_id].counters.get('+1/+1',0))
            self.assertEqual(i+1,session.state.cards[walker.object_id].counters.get('+1/+1',0))
            self.assertEqual(i+1,session.state.cards[caravel.object_id].counters.get('+1/+1',0))
            self.assertEqual(40+3*(i+1),session.state.players['A'].life)
            self.assertEqual(i+1,sum(e.code=='explore.complete' for e in session.state.events))
        self.assertEqual('hand',session.state.cards[top.object_id].zone)
        self.replay(session,load=True)

    def test_actual_departed_returned_and_noncreature_explorers_use_sealed_facts(self):
        session = self.session(645003)
        walkers = [self.add(session.engine,'Wildgrowth Walker',seat=seat,ref='EXPLORE_LKI_WALKER_'+seat) for seat in 'AB']
        source = self.add(session.engine,'Merfolk Branchwalker',zone='hand',ref='EXPLORE_LKI_SOURCE')
        later = self.add(session.engine,"Seekers' Squire",zone='hand',ref='EXPLORE_NONCREATURE_SOURCE')
        theft = self.add(session.engine,'Act of Treason',seat='B',zone='hand',ref='EXPLORE_LKI_THEFT')
        bounce = self.add(session.engine,'Unsummon',zone='hand',ref='EXPLORE_LKI_BOUNCE')
        observer_bounce = self.add(session.engine,'Unsummon',zone='hand',ref='EXPLORE_OBSERVER_BOUNCE')
        song = self.add(session.engine,'Song of the Dryads',zone='hand',ref='EXPLORE_TYPE_CHANGE')
        for seat in 'AB':self.add(session.engine,'Vedalken Orrery',seat=seat,ref='EXPLORE_FLASH_'+seat)
        self.add(session.engine,'Sol Ring',zone='library',ref='EXPLORE_SECOND_NONLAND')
        self.add(session.engine,'Forest',zone='library',ref='EXPLORE_FIRST_LAND')
        other_top = self.add(session.engine,'Sol Ring',seat='B',zone='library',ref='EXPLORE_LKI_B_TOP')
        for card in (*walkers,source,later,theft,bounce,observer_bounce,song):self.assert_closed(session,card)
        self.start(session)
        session.state.players['B'].mana_pool.update(C=4,R=3)
        self.checkpoint(session)
        self.cast(session,source)
        self.resolve_top(session)
        original = session.state.cards[source.object_id].logical_object_id
        self.priority(session,'B')
        self.cast(session,theft,(source.ref,),actor='B')
        self.resolve_top(session)
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.priority(session,'A')
        self.cast(session,bounce,(source.ref,))
        self.resolve_top(session)
        self.priority(session,'A')
        self.cast(session,source)
        self.resolve_top(session)
        self.assertNotEqual(original,session.state.cards[source.object_id].logical_object_id)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual(['B'],session.state.pending_decision.actors)
        self.assertEqual(43,session.state.players['A'].life)
        session = self.explore_choice(session,'B','graveyard')
        self.priority(session,'A')
        self.cast(session,observer_bounce,(walkers[1].ref,))
        self.resolve_top(session)
        self.assertEqual('hand',session.state.cards[walkers[1].object_id].zone)
        self.resolve(session)
        self.assertEqual((43,43),tuple(session.state.players[seat].life for seat in 'AB'))
        self.assertEqual((1,0),tuple(session.state.cards[c.object_id].counters.get('+1/+1',0) for c in walkers))
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual('graveyard',session.state.cards[other_top.object_id].zone)
        self.cast(session,later)
        self.resolve_top(session)
        self.cast(session,song,(later.ref,))
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertNotIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[later.object_id])['type_line'])[0])
        self.assertEqual(1,session.state.cards[later.object_id].counters.get('+1/+1',0))
        session = self.explore_choice(session,'A','top')
        self.resolve(session)
        self.assertEqual((43,43),tuple(session.state.players[seat].life for seat in 'AB'))
        self.assertEqual((1,0),tuple(session.state.cards[c.object_id].counters.get('+1/+1',0) for c in walkers))
        self.assertEqual(3,sum(e.code=='explore.complete' for e in session.state.events))
        self.replay(session,load=True)
