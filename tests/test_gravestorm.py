"""Independent Gravestorm count and closed intrinsic keyword contracts."""
import unittest
from types import SimpleNamespace
from quorune.turn_history_model import TurnHistory,TurnHistoryEvent
from quorune.rules.gravestorm import gravestorm_copy_count


class GravestormCountTests(unittest.TestCase):
    def state(self,events=()):
        players={seat:SimpleNamespace(in_game=seat!='D') for seat in 'ABCD'}
        return SimpleNamespace(players=players,turn_sequence=8,
            turn_history=TurnHistory(turn_sequence=8,permanent_death_history_version=1,events=list(events)))

    def death(self,seat,identity,types=('creature',),token=False):
        return TurnHistoryEvent('permanent_died',actor=seat,object_incarnation=identity,
            types=tuple(sorted(types)),creature_subtypes=(),token=token)

    def test_counts_all_permanent_types_tokens_and_departed_player_history_without_current_zone_lookup(self):
        events=[self.death('A','creature@1'),self.death('B','land@1',('land',)),
            self.death('C','clue@1',('artifact',),True),self.death('D','walker@1',('planeswalker',)),
            self.death('A','creature@2')]
        state=self.state(events)
        self.assertEqual(5,gravestorm_copy_count(state,'B'))
        self.assertEqual(5,gravestorm_copy_count(state,'A'))
        self.assertFalse(hasattr(state,'cards'))

    def test_complete_zero_and_new_deaths_at_resolution_are_distinct_from_prior_spells_or_discard(self):
        state=self.state([TurnHistoryEvent('spell_cast',actor='A'),
            TurnHistoryEvent('card_discarded',actor='B'),TurnHistoryEvent('creature_died',actor='C')])
        self.assertEqual(0,gravestorm_copy_count(state,'A'))
        state.turn_history.events.append(self.death('B','late-permanent@1'))
        self.assertEqual(1,gravestorm_copy_count(state,'A'))

    def test_unknown_or_incomplete_historical_journal_rejects_instead_of_assuming_zero(self):
        for history in (None,TurnHistory(turn_sequence=8),
                TurnHistory(turn_sequence=7,permanent_death_history_version=1)):
            state=self.state();state.turn_history=history
            with self.subTest(history=history),self.assertRaises(ValueError):gravestorm_copy_count(state,'A')
        for controller in ('UNKNOWN','',None,True):
            with self.subTest(controller=controller),self.assertRaises(ValueError):gravestorm_copy_count(self.state(),controller)

    def test_turn_history_round_trip_preserves_count_and_does_not_alias_original_events(self):
        state=self.state([self.death('C','token@1',('artifact',),True)])
        payload=state.turn_history.to_dict();copy=TurnHistory.from_dict(payload)
        state.turn_history.events.clear();state.turn_history=copy
        self.assertEqual(1,gravestorm_copy_count(state,'A'))

    def test_fresh_nontoken_only_query_mutant_is_killed_by_token_and_land_contrast(self):
        from unittest.mock import patch
        import quorune.rules.gravestorm as owner
        witness=type(self)('test_counts_all_permanent_types_tokens_and_departed_player_history_without_current_zone_lookup')
        baseline=unittest.TestResult();witness.run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=owner.public_history_query_value
        def wrong(state,controller,spec):
            return 0 if spec.token is True else original(state,controller,spec)
        result=unittest.TestResult()
        with patch.object(owner,'public_history_query_value',wrong):
            type(self)('test_counts_all_permanent_types_tokens_and_departed_player_history_without_current_zone_lookup').run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class GravestormCompilerTests(unittest.TestCase):
    def test_complete_printed_reminders_and_old_shortened_reminder_consume_no_unknown_rider(self):
        from quorune.compiler.gravestorm_nodes import is_gravestorm_line
        for text in ('Gravestorm',
                'Gravestorm (When you cast this spell, copy it for each permanent put into a graveyard from the battlefield this turn.)',
                'Gravestorm (When you cast this spell, copy it for each permanent put into a graveyard this turn. You may choose new targets for the copies.)'):
            with self.subTest(text=text):self.assertTrue(is_gravestorm_line(text))
        for text in ('Gravestorm 2','Gravestorm. Draw a card.',
                'Spells you cast have gravestorm.',
                'Gravestorm (When you cast this spell, copy it for each creature put into a graveyard this turn.)'):
            with self.subTest(text=text):self.assertFalse(is_gravestorm_line(text))

    def test_raw_and_program_minima_require_intrinsic_source_cast_complete_journal_and_copy_owner(self):
        from quorune.rules.gravestorm import gravestorm_instruction,gravestorm_condition,gravestorm_payload_dependencies,gravestorm_program_dependencies
        effect=gravestorm_instruction();deps=gravestorm_payload_dependencies(effect)
        self.assertIn('trigger.keyword.gravestorm',deps);self.assertIn('zone.turn_history.permanent_death',deps)
        self.assertIn('stack.copy.cast_event_spell',deps)
        program=SimpleNamespace(effects=(effect,),event='spell.cast',active_zone='stack',
            target_schema=None,event_condition=gravestorm_condition())
        self.assertEqual(deps,gravestorm_program_dependencies(program))
        for changes in ({'gravestorm':{'schema_version':True}},{'player':'B'},
                {'stack':'$source'},{'copy_source':{}},{'count':2}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):gravestorm_payload_dependencies({**effect,**changes})
        for changes in ({'event':'spell.cast_or_copy'},{'active_zone':'battlefield'},{'event_condition':None}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):gravestorm_program_dependencies(SimpleNamespace(**{**vars(program),**changes}))

    def test_source_spanned_keyword_stays_blocked_without_registered_owner(self):
        from quorune.compiler.gravestorm_nodes import gravestorm_keyword_node
        from quorune.compiler.ir_model import SourceSpan
        residuals=[];span=SourceSpan(4,14,2)
        node=gravestorm_keyword_node(node_id='n2',line='Gravestorm',material_line='Gravestorm',span=span,
            capability_registry=None,capability_profile='commander_review',residuals=residuals,mechanics=('gravestorm',))
        self.assertEqual(span,node.span);self.assertEqual('spell.cast',node.event);self.assertEqual('stack',node.active_zone)
        self.assertFalse(node.exact);self.assertTrue(node.lowerable);self.assertTrue(residuals)


    def test_multiple_printed_instances_keep_separate_actual_source_cast_nodes(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        card=replace(query_record('Gravestorm\nGravestorm\nInvestigate.'),
            type_line='Sorcery',keywords=('Gravestorm',),power=None,toughness=None)
        ir=compile_oracle_card(card,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=[node for node in ir.faces[0].nodes if node.event=='spell.cast']
        self.assertEqual(2,len(nodes));self.assertEqual(2,len({node.node_id for node in nodes}))
        self.assertTrue(all(node.active_zone=='stack' and 'gravestorm' in node.effects[0] for node in nodes))


class GravestormChoiceTests(unittest.TestCase):
    def fixture(self,count=2):
        from quorune.event_spell_copy_model import EventSpellCopySource
        from quorune.model import StackItem
        from quorune.rules.gravestorm import gravestorm_instruction
        source=EventSpellCopySource(StackItem(stack_id='original',ref='SPELL',kind='spell',
            controller='A',label='Original',card_object_id='original-card',semantic_key='test:spell').to_dict(),
            {'name':'Original','type_line':'Sorcery'},None,'original-card@1')
        state=GravestormCountTests().state([GravestormCountTests().death('A','dead-'+str(i)) for i in range(count)])
        effect={**gravestorm_instruction(),'player':'B','stack':'SPELL','copy_source':source.to_dict()}
        context=SimpleNamespace(actor='B',stack_controller='B',stack_ref='TRIGGER',query=SimpleNamespace(active_seats=('A','B','C','D')))
        return SimpleNamespace(state=state),SimpleNamespace(controller='B',ref='TRIGGER'),effect,context

    def test_count_is_frozen_at_resolution_and_canonical_copies_use_current_controller_and_original(self):
        from quorune.rules.gravestorm import bind_gravestorm_instruction
        from quorune.semantic_choices.gravestorm import prepare_gravestorm
        from quorune.event_spell_copy_model import EventSpellCopySource
        host,item,effect,context=self.fixture();bound=bind_gravestorm_instruction(host,item,effect)
        host.state.turn_history.events.append(GravestormCountTests().death('D','later-land',('land',)))
        prepared=prepare_gravestorm(bound,context)
        self.assertIsNone(prepared.request);self.assertEqual((),prepared.preparation_intents)
        copies=prepared.auto_continue.prepend_effects;self.assertEqual(2,len(copies))
        for copy in copies:
            self.assertEqual(('copy_stack_item','B','SPELL'),(copy['op'],copy['player'],copy['stack']))
            self.assertNotIn('targets',copy)
            self.assertEqual('A',EventSpellCopySource.from_dict(copy['copy_source']).item().controller)

    def test_zero_and_unavailable_history_and_changed_actor_or_count_are_distinct(self):
        from quorune.rules.gravestorm import bind_gravestorm_instruction
        from quorune.semantic_choices.gravestorm import prepare_gravestorm
        from quorune.semantic_choices.model import SemanticChoiceError
        host,item,effect,context=self.fixture(0);bound=bind_gravestorm_instruction(host,item,effect)
        self.assertEqual((),prepare_gravestorm(bound,context).auto_continue.prepend_effects)
        for changes in ({'_gravestorm_copies':True},{'_gravestorm_copies':-1},{'player':'A'},{'stack':'OTHER'},{'extra':True}):
            with self.subTest(changes=changes),self.assertRaises(SemanticChoiceError):prepare_gravestorm({**bound,**changes},context)
        with self.assertRaises(SemanticChoiceError):prepare_gravestorm(effect,context)
        with self.assertRaises(ValueError):bind_gravestorm_instruction(host,item,bound)
        host.state.turn_history=TurnHistory(turn_sequence=8)
        with self.assertRaises(ValueError):bind_gravestorm_instruction(host,item,effect)
        ordinary={'op':'draw','player':'B','amount':1};self.assertIs(ordinary,bind_gravestorm_instruction(host,item,ordinary))

    def test_default_keyword_dispatch_classifies_bare_gravestorm_without_redundant_card_keyword_tags(self):
        from quorune.compiler.keyword_templates import keyword_mechanics
        self.assertEqual(('gravestorm',),keyword_mechanics('Gravestorm',('Gravestorm',)))
        self.assertIsNone(keyword_mechanics('Gravestorm 2',('Gravestorm',)))
        self.assertEqual(('gravestorm',),keyword_mechanics('Gravestorm',()))


from inherited_composition_fixture import InheritedNativeFixture

class GravestormNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import json,tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=root/'base.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/gravestorm-cards.json'],root/'gravestorm.sqlite3')
        cls.db=CardDatabase(root/'gravestorm.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Gravestorm',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])


class GravestormNativeTests(GravestormNativeFixture,unittest.TestCase):
    def choose_copy(self,s,**response):
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',{'action_id':'choose',**response}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',{'action_id':'choose',**response});self.assertTrue(result.ok,result.summary)

    def prepared_deaths(self,name,seed):
        s=self.session(seed);source=self.add(s.engine,name,zone='hand')
        raise_alarm=self.add(s.engine,'Raise the Alarm',zone='hand')
        wrath=self.add(s.engine,'Wrath of God',zone='hand')
        shatter=self.add(s.engine,'Shatter',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears',seat='B',ref='gravestorm-dead-bear')
        ring=self.add(s.engine,'Sol Ring',seat='D',ref='gravestorm-late-artifact')
        secret=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='gravestorm-unrevealed-secret')
        for card in (source,raise_alarm,wrath,shatter):self.assert_closed(s,card)
        color='U' if name=='Follow the Bodies' else 'B'
        self.prepare(s,mana={'C':6,'W':3,color:1,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',raise_alarm,reserve={'C':5,'W':2,color:1,'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(2,sum(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        self.cast_for(s,'A',wrath,reserve={'C':3,color:1,'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[bear.object_id].zone)
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        targets=[] if name=='Follow the Bodies' else ['C']
        self.cast_for(s,'A',source,targets,reserve={'C':1,'R':1});self.assertEqual(1,self.trigger_count(s,source))
        # This fourth death happens after the original cast, before its
        # Gravestorm trigger resolves; it is a noncreature artifact.
        self.cast_for(s,'A',shatter,[ring.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[ring.object_id].zone)
        self.assertEqual(1,self.trigger_count(s,source))
        self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        return s,source,secret

    def test_two_complete_originals_bind_normally_and_independent_four_owner_minima_cannot_be_stripped(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.program_generation import register_generated_programs
        from quorune.semantics import SemanticRegistry
        for name in ('Follow the Bodies','Ominous Harvest'):
            card=self.db.lookup(name)
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            semantics=SemanticRegistry()
            register_generated_programs(self.db,semantics,[card],capability_registry=self.registry,
                capability_profile='commander_review',promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True,promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True)
            program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,
                capability_registry=self.registry,capability_profile='commander_review')
            result=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(result['strict_capability_ready'],(name,result['blockers']))
            trigger,=[p for p in semantics.programs_for_oracle(card.oracle_id)
                if any('gravestorm' in effect for effect in p.effects)]
            for cap in ('trigger.keyword.gravestorm','stack.copy.cast_event_spell',
                    'zone.turn_history.permanent_death','condition.history.fixed_public_query'):
                dependencies=tuple(value for value in trigger.capability_dependencies if value!=cap)
                changed=replace(trigger,capability_dependencies=dependencies,coverage=(),
                    capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
                binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(binding['strict'],(cap,binding))

    def test_actual_paid_follow_bodies_counts_tokens_and_late_artifact_then_four_uncast_copies_and_printed_clues_privately_replay(self):
        import json
        s,source,secret=self.prepared_deaths('Follow the Bodies',702690101)
        self.resolve_top(s)
        self.assertEqual(4,sum(item.kind=='spell_copy' for item in s.state.stack))
        self.assertEqual(0,self.trigger_count(s,source))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack)
        clues=[card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield'
            and 'clue' in s.engine._effective_card_data(card)['type_line'].casefold()]
        self.assertEqual(5,len(clues));self.assertTrue(all(card.controller=='A' for card in clues))
        for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_paid_ominous_harvest_each_copy_chooses_own_target_and_keeps_draw_life_sequence_private_replay(self):
        import json
        s,source,secret=self.prepared_deaths('Ominous Harvest',702690102)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        for index,seat in enumerate('ABCD'):
            self.choose_copy(s,targets=[seat])
            if index==0:s=self.replay(s,load=True)
        self.assertEqual(4,sum(item.kind=='spell_copy' for item in s.state.stack))
        self.assertEqual(0,self.trigger_count(s,source))
        hands={seat:set(s.state.players[seat].zones['hand']) for seat in 'ABCD'}
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack)
        expected={'A':1,'B':1,'C':2,'D':1}
        for seat,count in expected.items():
            self.assertEqual(40-count,s.state.players[seat].life)
            self.assertEqual(len(hands[seat])+count,len(s.state.players[seat].zones['hand']))
            drawn=set(s.state.players[seat].zones['hand'])-hands[seat]
            for viewer in 'ABCD':
                if viewer!=seat:
                    packet=json.dumps(s.packet('pilot:'+viewer,full=True))
                    for object_id in drawn:self.assertNotIn(s.state.cards[object_id].ref,packet)
        for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)


    def clues(self,s,seat=None):
        return [card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield'
            and (seat is None or card.controller==seat)
            and 'clue' in s.engine._effective_card_data(card)['type_line'].casefold()]

    def test_actual_countered_original_uses_departure_snapshot_and_countered_trigger_leaves_only_original(self):
        for counter_trigger in (False,True):
            with self.subTest(counter_trigger=counter_trigger):
                s,source,secret=self.prepared_deaths('Follow the Bodies',702690120+counter_trigger)
                reply=self.add(s.engine,'Stifle' if counter_trigger else 'Counterspell',seat='B',zone='hand')
                s.state.players['B'].mana_pool.update({'U':1 if counter_trigger else 2})
                # The checkpoint isolates the response interaction over the
                # real paid source/death history already prepared above.
                self.checkpoint(s)
                item=next(item for item in s.state.stack
                    if item.kind==('triggered_ability' if counter_trigger else 'spell')
                    and (item.source_object_id if counter_trigger else item.card_object_id)==source.object_id)
                self.cast_for(s,'B',reply,[item.ref]);self.resolve_top(s)
                if counter_trigger:
                    self.assertEqual(0,self.trigger_count(s,source))
                    self.assertIsNone(self.resolve(s));self.assertEqual(1,len(self.clues(s,'A')))
                else:
                    self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                    self.resolve_top(s)
                    self.assertEqual(4,sum(item.kind=='spell_copy' for item in s.state.stack))
                    self.assertFalse(any(item.kind=='spell' for item in s.state.stack))
                    self.assertIsNone(self.resolve(s));self.assertEqual(4,len(self.clues(s,'A')))
                self.assertFalse(s.state.stack);self.replay(s,load=True)

    def test_canonical_opponent_trigger_copy_uses_own_controller_and_shared_death_count_without_recasting(self):
        s,source,secret=self.prepared_deaths('Follow the Bodies',702690130)
        trigger=next(item for item in s.state.stack if item.kind=='triggered_ability')
        snapshot=trigger.context['event_spell_copy_source']
        # Exercise the existing canonical copy interface, without admitting
        # an unsupported utility card that could request this interaction.
        copied=s.engine._copy_stack_item(controller='B',target=trigger,targets=[],target_groups={},
            reason='Gravestorm copied-controller owner interaction')
        self.assertEqual(snapshot,copied.context['event_spell_copy_source']);self.checkpoint(s)
        self.resolve_top(s)
        copies=[item for item in s.state.stack if item.kind=='spell_copy']
        self.assertEqual(4,len(copies));self.assertTrue(all(item.controller=='B' for item in copies))
        self.assertEqual(1,self.trigger_count(s,source))
        for _ in range(4):self.resolve_top(s)
        self.assertEqual(4,len(self.clues(s,'B')))
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack)
        self.assertEqual((5,4),(len(self.clues(s,'A')),len(self.clues(s,'B'))))
        self.replay(s,load=True)

    def test_actual_second_copy_fault_restores_entire_pass_then_loads_and_retries_without_duplicate_copies(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,secret=self.prepared_deaths('Follow the Bodies',702690140)
        commit=s.engine._copy_stack_item;observed=[];failed_actor=None;before=None
        def fault(*args,**kwargs):
            copied=commit(*args,**kwargs);observed.append(copied.ref)
            if len(observed)==2:raise GameRuleError('Injected second Gravestorm copy fault')
            return copied
        with patch.object(s.engine,'_copy_stack_item',side_effect=fault):
            for _ in range(16):
                before=authoritative_state_hash(s.state);actor=s.pending_principals()[0]
                result=s.act(actor,{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected second Gravestorm copy fault',result.summary)
                    failed_actor=actor;break
            else:self.fail('The injected second-copy fault was not reached')
        self.assertEqual(2,len(observed));self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(any(item.kind=='spell_copy' for item in s.state.stack))
        s=self.replay(s,load=True)
        result=s.act(failed_actor,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(4,sum(item.kind=='spell_copy' for item in s.state.stack))
        self.assertIsNone(self.resolve(s));self.assertEqual(5,len(self.clues(s,'A')))
        self.replay(s,load=True)

    def test_fresh_native_token_omission_mutant_is_killed_after_positive_paid_original(self):
        from unittest.mock import patch
        import quorune.rules.gravestorm as owner
        method='test_actual_paid_follow_bodies_counts_tokens_and_late_artifact_then_four_uncast_copies_and_printed_clues_privately_replay'
        baseline=unittest.TestResult();type(self)(method).run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=owner.public_history_query_value
        def wrong(state,controller,spec):
            return 0 if spec.token is True else original(state,controller,spec)
        result=unittest.TestResult()
        with patch.object(owner,'public_history_query_value',wrong):type(self)(method).run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class GravestormSourceAssuranceTests(GravestormNativeFixture,unittest.TestCase):
    def test_actual_source_capture_delegates_existing_owner_and_rejects_other_spells_uncast_copies_and_old_incarnations(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.event_spell_copy_model import EventSpellCopySource
        from quorune.rules.gravestorm import capture_gravestorm_cast_source
        host,item,effect,context=GravestormChoiceTests().fixture()
        snapshot=EventSpellCopySource.from_dict(effect['copy_source'])
        host.state.stack=[snapshot.item()]
        source=SimpleNamespace(zone='stack',ref='CARD',object_id='original-card',controller='A',logical_object_id='original-card@1')
        event={'card':'CARD','stack':'SPELL','controller':'A','logical_object_id':'original-card@1'}
        with patch('quorune.rules.event_spell_copy.capture_event_spell_copy_source',return_value=snapshot) as capture:
            self.assertEqual(snapshot,capture_gravestorm_cast_source(host,source,event))
            capture.assert_called_once_with(host,host.state.stack[0])
        for changes in ({'card':'OTHER'},{'stack':'MISSING'},{'controller':'B'},{'logical_object_id':'original-card@2'}):
            with self.subTest(changes=changes),patch('quorune.rules.event_spell_copy.capture_event_spell_copy_source') as capture:
                with self.assertRaises(ValueError):capture_gravestorm_cast_source(host,source,{**event,**changes})
                capture.assert_not_called()
        host.state.stack[0].kind='spell_copy'
        with self.assertRaises(ValueError):capture_gravestorm_cast_source(host,source,event)

    def test_genuine_verified_fine_death_query_and_copy_owner_withdrawals_block_both_originals_and_keep_unrelated_controls(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        import tempfile
        from pathlib import Path
        from scripts.build_test_database import build_fixture_database
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'old-owner-controls.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/event-spell-copy-cards.json',
                ROOT/'tests/fixtures/ripple-cards.json'],path)
            with CardDatabase(path) as db:
                old_copy=db.lookup('Doublecast');old_ripple=db.lookup('Surging Flame')
        for cap in ('trigger.keyword.gravestorm','zone.turn_history.permanent_death',
                'condition.history.fixed_public_query','stack.copy.cast_event_spell'):
            value=deepcopy(raw);next(row for row in value['capabilities'] if row['id']==cap).update(
                status='blocked',blockers=['Withdrawn Gravestorm or critical shared authority'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertTrue(registry.evidence_fingerprint)
            for name in ('Follow the Bodies','Ominous Harvest'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers={b for residual in ir.material_residuals for b in residual.blockers}
                self.assertIn('capability:status:'+cap+':blocked',blockers)
                self.assertFalse(any('unverified' in blocker for blocker in blockers))
            for card in (self.db.lookup('Grizzly Bears'),old_ripple):
                self.assertEqual('exact',compile_oracle_card(card,capability_registry=registry,
                    capability_profile='commander_review').status,(cap,card.name))
            copied=compile_oracle_card(old_copy,capability_registry=registry,capability_profile='commander_review')
            if cap=='stack.copy.cast_event_spell':self.assertNotEqual('exact',copied.status)
            else:self.assertEqual('exact',copied.status,(cap,'old next-cast copy control'))
