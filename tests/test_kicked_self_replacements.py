from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls
from test_bound_effect_programs import record


class KickedSelfReplacementCompilerTests(unittest.TestCase):
    def compile(self,text,kind='Sorcery',registry=None):
        source=replace(record(text,kind),keywords=('Kicker',))
        return compile_oracle_card(source,capability_registry=registry or load_default_capability_registry(),
            capability_profile='commander_review')

    def test_complete_branches_declare_cast_fact_self_replacement_and_each_child_owner(self):
        text='Kicker {1}\nDraw two cards. If this spell was kicked, draw three cards instead.'
        ir=self.compile(text)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(n for n in ir.faces[0].nodes if n.effects)
        self.assertEqual(node.text,text[node.span.start:node.span.end])
        wrapper=node.effects[0]
        self.assertEqual((4,'$context.kicked'),(wrapper['schema_version'],wrapper['cast_fact']))
        self.assertEqual(3,wrapper['effects'][0]['count'])
        self.assertEqual(2,wrapper['otherwise_effects'][0]['count'])
        self.assertIsNone(node.target_schema)
        for cap in ('resolution.effect.public_condition','resolution.effect.fixed_cast_fact',
                'resolution.effect.fixed_public_self_replacement','zone.draw.library_to_hand'):
            self.assertIn(cap,node.capability_dependencies)

    def test_replacement_residual_owner_retains_exact_unowned_node_contract(self):
        from quorune.compiler.ir_model import SourceSpan
        from quorune.compiler.replacement_residual_nodes import unowned_replacement_node
        from quorune.oracle_ir import _reviewed_effect_template
        for text in ('Skip your next turn.','If you would draw a card, instead rewrite history.',
                'As this creature enters, choose an unrepresented value.'):
            for permanent in (False,True):
                residuals=[];span=SourceSpan(0,len(text),1)
                node=unowned_replacement_node(node_id='n1',line=text,material_line=text,
                    span=span,source_name='Generic Witness',spell=not permanent,permanent=permanent,
                    has_faces=False,compile_effect=_reviewed_effect_template,residuals=residuals)
                self.assertEqual('replacement_effect',node.kind)
                self.assertEqual('battlefield' if permanent else 'stack',node.active_zone)
                self.assertEqual(('replace',False,False,('r1',)),(node.event,node.lowerable,node.exact,node.residual_ids))
                self.assertEqual(('r1',text,span,'replacement/prevention ordering is not compiled'),
                    (residuals[0].residual_id,residuals[0].text,residuals[0].span,residuals[0].reason))
                self.assertEqual(('replacement applicability','affected-player ordering',
                    'self-replacement and prevention ordering'),residuals[0].blockers)
        legacy="Draw two cards. If you have the city's blessing, draw three cards instead."
        kicked='Draw two cards. If this spell was kicked, draw three cards instead.'
        for text in (legacy,kicked,'Draw two cards.'):
            residuals=[]
            self.assertIsNone(unowned_replacement_node(node_id='n1',line=text,material_line=text,
                span=SourceSpan(0,len(text),1),source_name='Generic Witness',spell=True,permanent=False,
                has_faces=False,compile_effect=_reviewed_effect_template,residuals=residuals))
            self.assertEqual([],residuals)

    def test_unsupported_targets_variable_results_and_permanent_trigger_contexts_stay_residual(self):
        for body in ('Target player draws two cards. If this spell was kicked, that player draws three cards instead.',
                'Draw two cards. If this spell was kicked, you may draw three cards instead.',
                'Draw two cards. If this spell was kicked, draw X cards instead.',
                'Draw two cards. If this spell was kicked, draw three cards instead. Invent an unrepresented sibling.',
                'Create two 1/1 green Saproling creature tokens. If this spell was kicked, create countless of those tokens instead.',
                'Draw two cards. If this spell was kicked, draw three cards instead and draw a card.'):
            self.assertNotEqual('exact',self.compile('Kicker {1}\n'+body).status,body)
        trigger='Kicker {1}\nWhen this creature enters, draw two cards. If it was kicked, draw three cards instead.'
        self.assertNotEqual('exact',self.compile(trigger,'Creature — Wizard').status)

    def test_each_required_authority_withdrawal_keeps_both_branch_programs_residual(self):
        text='Kicker {1}\nDraw two cards. If this spell was kicked, create two 1/1 green Saproling creature tokens instead.'
        self.assertEqual('exact',self.compile(text).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('resolution.effect.public_condition','resolution.effect.fixed_cast_fact',
                'resolution.effect.fixed_public_self_replacement','zone.draw.library_to_hand',
                'token.creation.fixed_definition','casting.kicker.fixed_mana'):
            changed=deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn'])
            self.assertNotEqual('exact',self.compile(text,registry=CapabilityRegistry(changed)).status,cap)

    def test_closed_schema_and_unselected_branch_are_validated_before_result_selection(self):
        from quorune.resolution_conditions import validate_resolution_condition_instruction
        from quorune.rules.resolution_condition_shapes import resolution_condition_node_capabilities
        from quorune.semantic_choices import SemanticChoiceContext,SnapshotSemanticChoiceQuery,SemanticChoiceError
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        ir=self.compile('Kicker {1}\nDraw two cards. If this spell was kicked, draw three cards instead.')
        node=next(n for n in ir.faces[0].nodes if n.effects);effect=deepcopy(node.effects[0])
        before=deepcopy(effect);validate_resolution_condition_instruction(effect);self.assertEqual(before,effect)
        for change in ({'schema_version':4.0},{'cast_fact':1},{'otherwise_effects':[]},
                {'otherwise_mechanic_ids':[]},{'unrepresented':True}):
            with self.assertRaises(ValueError):validate_resolution_condition_instruction({**effect,**change})
        unowned=deepcopy(effect);unowned['otherwise_effects'][0]['op']='unowned_effect'
        self.assertFalse(resolution_condition_node_capabilities(effects=(unowned,),target_schema=None,mechanic_ids=node.mechanics))
        query=SnapshotSemanticChoiceQuery(seat_order=tuple('ABCD'),active_order=tuple('ABCD'))
        context=SemanticChoiceContext(actor='A',stack_ref='STACK',stack_controller='A',stack_label='Witness',
            source_ref=None,card_ref=None,semantic_program_id='Witness',semantic_program_version=1,query=query)
        handler=PublicResolutionConditionHandler()
        bound=deepcopy(effect);bound['player']='A'
        for children in (bound['effects'],bound['otherwise_effects']):
            for child in children:child['player']='A'
        for paid in (False,True):
            prepared=handler.prepare({**bound,'cast_fact':paid},context)
            self.assertEqual(3 if paid else 2,prepared.auto_continue.prepend_effects[0]['count'])
        for marker in (None,1,'$context.kicked','true'):
            with self.assertRaises(SemanticChoiceError):handler.prepare({**bound,'cast_fact':marker},context)
        bad=deepcopy(bound);bad['otherwise_effects'][0]['op']='unowned_effect'
        with self.assertRaises(SemanticChoiceError):handler.prepare({**bad,'cast_fact':True},context)
        with self.assertRaises(SemanticChoiceError):handler.prepare({**bound,'cast_fact':True,'player':'B'},context)


class KickedSelfReplacementRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'kicked-replacement.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/kicked-self-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Kicked replacement witnesses',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_field_research_selects_two_or_three_private_draws_never_both(self):
        for kicked in (False,True):
            session=self.session(729601)
            source=self.add(session.engine,'Field Research',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'U':2 if kicked else 1,'C':4 if kicked else 2});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            from quorune.preflight import card_semantic_status
            status=card_semantic_status(self.db.lookup('Field Research'),session.engine.semantics,
                db=self.db,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],status['unresolved'])
            action=self.action(session,source);before=authoritative_state_hash(session.state)
            denied=session.act('pilot:B',{'action_id':action['id'],'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            hand=len(session.state.players['A'].zones['hand']);library=len(session.state.players['A'].zones['library'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            amount=3 if kicked else 2
            self.assertEqual(hand+amount,len(session.state.players['A'].zones['hand']))
            self.assertEqual(library-amount,len(session.state.players['A'].zones['library']))
            for viewer in 'BCD':
                packet=json.dumps(session.packet('pilot:'+viewer,full=True))
                self.assertTrue(all(session.state.cards[oid].ref not in packet for oid in session.state.players['A'].zones['hand']))
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_saproling_migration_reuses_recipe_with_two_or_four_tokens(self):
        for kicked in (False,True):
            session=self.session(729602)
            source=self.add(session.engine,'Saproling Migration',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'G':1,'C':5 if kicked else 1});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            before={c.object_id for c in session.state.cards.values() if c.is_token}
            accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],
                'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            tokens=[c for c in session.state.cards.values() if c.is_token and c.object_id not in before and c.zone=='battlefield']
            self.assertEqual(4 if kicked else 2,len(tokens))
            for token in tokens:
                data=session.engine._effective_card_data(token)
                self.assertEqual(('1','1'),(str(data['power']),str(data['toughness'])))
                self.assertIn('Saproling',data['type_line'])
                self.assertEqual('A',token.controller)
            self.private_replay(session,private)

    def test_original_bold_defense_chooses_one_modifier_set_and_retains_the_keyword(self):
        for kicked in (False,True):
            session=self.session(729603)
            source=self.add(session.engine,'Bold Defense',zone='hand')
            own=self.add(session.engine,'Generic Bound Body',ref='bold-own')
            other=self.add(session.engine,'Generic Bound Body',seat='B',ref='bold-other')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'W':2 if kicked else 1,'C':5 if kicked else 2});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],
                'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            data=session.engine._effective_card_data(session.state.cards[own.object_id])
            self.assertEqual(4 if kicked else 3,int(data['power']))
            self.assertEqual(2,int(session.engine._effective_card_data(session.state.cards[other.object_id])['power']))
            self.assertEqual(kicked,'first strike' in session.engine._combat_keywords(session.state.cards[own.object_id]))
            self.private_replay(session,private)

    def test_original_self_replacement_precedes_external_draw_and_token_doubling(self):
        for name,replacement,mana in (('Field Research','Thought Reflection',{'U':2,'C':4}),
                ('Saproling Migration','Parallel Lives',{'G':1,'C':5})):
            session=self.session(729604+len(name))
            source=self.add(session.engine,name,zone='hand')
            external=self.add(session.engine,replacement)
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana=mana);self.checkpoint(session)
            for card in (source,external):self.assertTrue(self.binding(session,card)['strict_capability_ready'],card.printed_name)
            hand=len(session.state.players['A'].zones['hand'])
            accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            if name=='Field Research':
                self.assertEqual(hand-1+6,len(session.state.players['A'].zones['hand']))
                for seat in 'BCD':
                    packet=json.dumps(session.packet('pilot:'+seat,full=True))
                    self.assertTrue(all(session.state.cards[oid].ref not in packet for oid in session.state.players['A'].zones['hand']))
            else:
                tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
                self.assertEqual(8,len(tokens))
                self.assertTrue(all(c.controller=='A' for c in tokens))
            self.private_replay(session,private)

    def test_original_reverberate_copies_paid_choice_for_new_controller_without_repaying(self):
        session=self.session(729607)
        source=self.add(session.engine,'Field Research',zone='hand')
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'U':2,'C':4});self.checkpoint(session)
        for card in (source,copy):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        original=session.state.stack[-1];self.assertIs(True,original.context['kicked'])
        hand_a=len(session.state.players['A'].zones['hand']);hand_b=len(session.state.players['B'].zones['hand'])
        self.cast(session,'B',copy,[original.ref])
        session=self.replay(session,load=True);pending=self.resolve(session)
        if pending is not None:
            self.assertEqual('semantic.choice',pending.kind)
            session=self.replay(session,load=True)
            accepted=session.act('pilot:B',{'action_id':'choose','targets':[]})
            self.assertTrue(accepted.ok,accepted.summary)
            self.assertIsNone(self.resolve(session))
        self.assertEqual(hand_a+3,len(session.state.players['A'].zones['hand']))
        self.assertEqual(hand_b-1+3,len(session.state.players['B'].zones['hand']))
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        for seat in 'CD':
            packet=json.dumps(session.packet('pilot:'+seat,full=True))
            for owner in 'AB':
                self.assertTrue(all(session.state.cards[oid].ref not in packet for oid in session.state.players[owner].zones['hand']))
        self.private_replay(session,private)

    def test_original_conquerors_pledge_retains_twelve_complete_token_recipes(self):
        session=self.session(729609)
        source=self.add(session.engine,"Conqueror's Pledge",zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':3,'C':8});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(12,len(tokens))
        for token in tokens:
            data=session.engine._effective_card_data(token)
            self.assertEqual(('1','1'),(str(data['power']),str(data['toughness'])))
            self.assertEqual(['W'],data['colors']);self.assertIn('Kor',data['type_line']);self.assertIn('Soldier',data['type_line'])
            self.assertEqual('A',token.controller)
        self.private_replay(session,private)

    def test_countered_kicked_original_keeps_paid_cost_and_has_neither_branch_result(self):
        session=self.session(729608)
        source=self.add(session.engine,'Field Research',zone='hand')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'U':2,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,'B',counter,[session.state.stack[-1].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_fresh_native_field_research_kills_additive_and_unpaid_branch_mutants(self):
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        original=PublicResolutionConditionHandler.prepare
        def additive(handler,effect,context):
            result=original(handler,effect,context)
            if effect.get('schema_version')==4:
                other=effect['otherwise_effects'] if effect['cast_fact'] else effect['effects']
                return replace(result,auto_continue=replace(result.auto_continue,
                    prepend_effects=(*result.auto_continue.prepend_effects,*other)))
            return result
        def unpaid(handler,effect,context):
            return original(handler,{**effect,'cast_fact':False} if effect.get('schema_version')==4 else effect,context)
        method='test_original_field_research_selects_two_or_three_private_draws_never_both'
        for mutant in (additive,unpaid):
            with patch.object(PublicResolutionConditionHandler,'prepare',mutant):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_wild_onslaught_replaces_counter_count_for_current_controller_set(self):
        for kicked in (False,True):
            session=self.session(729610+int(kicked))
            source=self.add(session.engine,'Wild Onslaught',zone='hand')
            own=self.add(session.engine,'Generic Bound Body',ref='onslaught-own')
            other=self.add(session.engine,'Generic Bound Body',seat='B',ref='onslaught-other')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'G':1,'C':7 if kicked else 3});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],
                'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(2 if kicked else 1,session.state.cards[own.object_id].counters.get('+1/+1',0))
            self.assertEqual(0,session.state.cards[other.object_id].counters.get('+1/+1',0))
            self.private_replay(session,private)

    def test_executable_condition_rejects_withdrawn_capability_closure(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(729612)
        source=self.add(session.engine,'Field Research',zone='hand')
        cap='resolution.effect.public_condition'
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn'])
        bound=bind_semantic_program_runtime(program,capability_registry=CapabilityRegistry(raw),profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:closure_untrusted',bound['blockers'])

    def test_compiler_omission_is_killed_by_fresh_native_field_research(self):
        method='test_original_field_research_selects_two_or_three_private_draws_never_both'
        with patch('quorune.compiler.kicked_self_replacement_templates.kicked_self_replacement_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
