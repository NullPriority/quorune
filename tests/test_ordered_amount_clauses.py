from __future__ import annotations

"""CR608.2c/h: later typed quantities observe earlier committed instructions."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_gain_and_get_sets as controls
from quorune.rules.evidence_storage import read_evidence_document


class OrderedAmountClauseTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.GainAndGetSetTests.session
    add=controls.GainAndGetSetTests.add
    prepare=controls.GainAndGetSetTests.prepare
    checkpoint=controls.GainAndGetSetTests.checkpoint
    resolve=controls.GainAndGetSetTests.resolve
    replay=controls.GainAndGetSetTests.replay
    binding=controls.GainAndGetSetTests.binding
    priority=controls.GainAndGetSetTests.priority
    cast=controls.GainAndGetSetTests.cast
    actions=controls.GainAndGetSetTests.actions
    private_replay=controls.GainAndGetSetTests.private_replay
    top=controls.GainAndGetSetTests.top
    advance=controls.GainAndGetSetTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'ordered-amount.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/gain-and-get-cards.json',ROOT/'tests/fixtures/ordered-amount-clause-cards.json',
            ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',ROOT/'tests/fixtures/closed-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-resolution-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ordered amount',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def history_controls(self,session):
        gain=self.add(session.engine,'Lightning Helix',zone='hand',ref='prior-A-gain')
        loss=self.add(session.engine,'Lightning Helix',seat='B',zone='hand',ref='prior-A-loss-B-gain')
        for card in (gain,loss):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        return gain,loss

    def gain_and_lose_three(self,session,controls):
        gain,loss=controls
        self.cast(session,'A',gain,['B'],{'R':1,'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((43,37),tuple(session.state.players[s].life for s in 'AB'))
        self.cast(session,'B',loss,['A'],{'R':1,'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((40,40),tuple(session.state.players[s].life for s in 'AB'))

    def test_actual_bogbeast_attack_gains_before_reading_own_gained_life_not_net_or_opponent_gain(self):
        session=self.session(751000);source=self.add(session.engine,'Blossoming Bogbeast');source.acquired_control_turn_count=-1
        own=self.add(session.engine,'Grizzly Bears',ref='bog-own-body')
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='bog-foreign-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        history=self.history_controls(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Bogbeast must admit')
        session.state.players['B'].mana_pool.update({'R':1,'W':1})
        self.prepare(session,mana={'R':1,'W':1});self.checkpoint(session);self.gain_and_lose_three(session,history)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        command={'action_id':'attack','attacks':{source.ref:'B'}};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,len(session.state.stack));self.assertEqual(40,session.state.players['A'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((42,40),tuple(session.state.players[s].life for s in 'AB'))
        self.assertEqual((8,8),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual((7,7),(session.engine._numeric_stat(own.object_id,'power'),session.engine._numeric_stat(own.object_id,'toughness')))
        for card in (source,own):self.assertIn('trample',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.assertEqual(2,session.engine._numeric_stat(foreign.object_id,'power'))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        self.advance(session,lambda s:s.state.phase=='postcombat_main');self.assertEqual(32,session.state.players['B'].life)
        self.advance(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
        self.assertEqual((3,2),tuple(session.engine._numeric_stat(c.object_id,'power') for c in (source,own)))
        for card in (source,own):self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.private_replay(session,private)

    def test_actual_pulse_keeps_prior_gain_once_across_simultaneous_state_action_replacements(self):
        session=self.session(751002);source=self.add(session.engine,'Essence Pulse',zone='hand')
        bodies=[self.add(session.engine,'Grizzly Bears',seat=seat,ref='pending-pulse-'+seat) for seat in 'BC']
        replacements=[self.add(session.engine,name,ref='pulse-replacement-'+str(i))
            for i,name in enumerate(('Generic Payment Destination Replacement','Generic Payment Competing Replacement'))]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (source,*replacements):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.prepare(session,mana={'B':1,'C':3});self.checkpoint(session);self.cast(session,'A',source,payment={'B':1,'C':3})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        for seat in 'BC':
            self.assertEqual(42,session.state.players['A'].life)
            self.assertEqual('state_based_zone_change',session.state.pending_decision.continuation['replacement_resume_kind'])
            self.assertEqual(['pilot:'+seat],session.pending_principals())
            self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in (*bodies,*replacements)))
            session=self.replay(session,load=True)
            options=session.packet('pilot:'+seat,full=True)['decision']['ctx']['options'];self.assertEqual(2,len(options))
            before=authoritative_state_hash(session.state)
            for actor,option in (('A',options[0]['id']),(seat,'unoffered-ordered-replacement')):
                self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose','replacement':option}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
            chosen=session.act('pilot:'+seat,{'action_id':'choose','replacement':options[0]['id']});self.assertTrue(chosen.ok,chosen.summary)
            self.resolve(session)
        self.assertIsNone(self.resolve(session));self.assertEqual(42,session.state.players['A'].life)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' and sum(session.state.cards[c.object_id].counters.values())==1 for c in bodies))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in replacements))
        self.private_replay(session,private)

    def test_ordered_amount_clauses_keep_scope_source_spans_and_unknown_clause_boundaries(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        good='You gain 2 life. Then creatures you control gain trample and get +X/+X until end of turn, where X is the amount of life you gained this turn.'
        ir=compile_oracle_card(record(good,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=[n for f in ir.faces for n in f.nodes if n.effects];self.assertEqual(1,len(nodes));node=nodes[0]
        self.assertEqual(good,node.text);self.assertEqual(good,good[node.span.start:node.span.end])
        self.assertEqual(2,len(node.effects));self.assertEqual(2,node.effects[0]['delta'])
        self.assertEqual('public_turn_history',node.effects[1]['power']['origin'])
        self.assertEqual('life_gained',node.effects[1]['power']['history_fact'])
        self.assertEqual(node.effects[1]['power'],node.effects[1]['toughness'])
        self.assertIn('resolution.effect_program.closed_components',node.capability_dependencies)
        distinct='You gain X life, where X is the number of creatures you control. Draw X cards, where X is the number of cards in your hand.'
        scoped=compile_oracle_card(record(distinct,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',scoped.status,scoped.material_residuals)
        effects=[e for f in scoped.faces for n in f.nodes for e in n.effects];self.assertEqual(2,len(effects))
        self.assertNotEqual(effects[0]['delta']['binding_id'],effects[1]['count']['binding_id'])
        for text in (good.replace('You gain 2 life.','Perform an unrepresented instruction.'),
                good+' Perform an unrepresented instruction.',good.replace('trample','NotAKeyword'),
                good.replace('life you gained this turn','life you gained this way'),
                good.replace('Then creatures you control','Then creatures chosen by an unrepresented rule'),
                good.replace('You gain 2 life.','Target player gains 2 life.').replace('creatures you control','creatures target player controls')):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review').status,text)

    def test_ordered_group_runtime_requires_sequence_quantity_and_result_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler.program_generation import _is_closed_effect_program
        from quorune.compiler.declared_effect_amounts import closed_additional_cost_x_result
        from types import SimpleNamespace
        self.assertFalse(closed_additional_cost_x_result(SimpleNamespace(cost_schema=None),recognizers=()))
        self.assertFalse(closed_additional_cost_x_result(SimpleNamespace(cost_schema={'additional_costs':[{}]}),recognizers=()))
        from quorune.card_programs.runtime_requirements import typed_result_amount_runtime_dependencies
        from quorune.public_quantity_mana_model import PublicQuantityManaOutput,PUBLIC_QUANTITY_MANA_CAPABILITY
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
        mana=PublicQuantityManaOutput('fixed',('G',),ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic='power')).to_dict()
        self.assertEqual((PUBLIC_QUANTITY_MANA_CAPABILITY,),typed_result_amount_runtime_dependencies(mana))
        with self.assertRaises(ValueError):typed_result_amount_runtime_dependencies({**mana,'amount':{**mana['amount'],'binding_id':'unowned-mana-declaration'}})
        session=self.session(751003);source=self.add(session.engine,'Blossoming Bogbeast')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if len(p.effects)==2]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertTrue(_is_closed_effect_program(program))
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        for owner in ('continuous.resolution.fixed_characteristics_until_end_of_turn','quantity_expression.scalar_effect_amount',
                'quantity_expression.declared_effect_amount','resolution.effect_program.closed_components'):
            self.assertIn(owner,program.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=owner)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            result=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict'],(owner,result['blockers']))
            self.assertIn('capability:undeclared_runtime_dependency:'+owner,result['blockers'])
        short=bind_semantic_program_runtime(replace(program,effects=[program.effects[0]]),capability_registry=self.registry,profile='commander_review')
        self.assertFalse(short['strict']);self.assertTrue(any(b.startswith('runtime_closed_effect_program:invalid:') for b in short['blockers']))
        for changed in ({**program.effects[1],'extra':True},
                {**program.effects[1],'power':{**program.effects[1]['power'],'coefficient':True}}):
            broken=replace(program,effects=[program.effects[0],changed])
            self.assertFalse(_is_closed_effect_program(broken))
            self.assertFalse(bind_semantic_program_runtime(broken,capability_registry=self.registry,profile='commander_review')['strict'])
        from test_bound_effect_programs import record
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        text='You gain X life, where X is the number of creatures you control. Draw X cards, where X is the number of cards in your hand.'
        complete=compile_best_available_card_program(self.db,record(text,'Sorcery'),semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        ordered=[p for p in complete.abilities if len(p.effects)==2];self.assertEqual(1,len(ordered));declared=ordered[0]
        self.assertTrue(bind_semantic_program_runtime(declared,capability_registry=self.registry,profile='commander_review')['strict'])
        for owner in ('quantity_expression.public_query_effect_amount','quantity_expression.declared_effect_amount'):
            self.assertIn(owner,declared.capability_dependencies)
            deps=tuple(c for c in declared.capability_dependencies if c!=owner)
            stripped=replace(declared,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'],(owner,bound['blockers']))
            self.assertIn('capability:undeclared_runtime_dependency:'+owner,bound['blockers'])
        bad=replace(declared,effects=[{**declared.effects[0],'delta':{**declared.effects[0]['delta'],'coefficient':True}},declared.effects[1]])
        invalid=bind_semantic_program_runtime(bad,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(invalid['strict']);self.assertTrue(any(b.startswith('runtime_typed_result_amount:invalid:') for b in invalid['blockers']))

    def test_canonical_sequence_and_quantity_withdrawals_keep_full_originals_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.oracle_ir import compile_oracle_card
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for owner in ('resolution.effect_program.closed_components','quantity_expression.scalar_effect_amount'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==owner).update(status='blocked',blockers=['withdrawn ordered quantity owner'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            closure=registry.closure((owner,),profile='commander_review');self.assertFalse(closure.trusted)
            self.assertTrue(any('status:'+owner+':blocked' in b for b in closure.blockers))
            for name in ('Blossoming Bogbeast','Essence Pulse'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(name,owner))
                self.assertFalse(any('evidence_index:unverified' in b for r in ir.material_residuals for b in r.blockers))

    def test_fresh_native_results_kill_atomic_fallback_early_quantity_and_group_commit_omissions(self):
        from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
        def missing_atomic_fallback(body,*,compile_fixed,compile_atomic):return compile_fixed(body)
        def early(host,value,item):
            return 3*value['coefficient'] if value.get('history_fact')=='life_gained' else resolve_scalar_effect_amount(host,value,item)
        # Force the actual fixed callback's closed public-set branch to stay absent.
        cases=(('quorune.compiler.query_counter_sequences._fixed_quantity_component',missing_atomic_fallback,
                'test_actual_bogbeast_attack_gains_before_reading_own_gained_life_not_net_or_opponent_gain'),
            ('quorune.semantic_runtime.values.resolve_scalar_effect_amount',early,
                'test_actual_bogbeast_attack_gains_before_reading_own_gained_life_not_net_or_opponent_gain'),
            ('quorune.effect_runtime.zones_and_attachments.create_resolution_continuous_effect',lambda *a,**k:None,
                'test_actual_essence_pulse_reads_post_gain_history_and_weakens_every_seat_before_state_actions'),
            ('quorune.card_programs.runtime_requirements.closed_effect_program_runtime_dependencies',lambda *a:(),
                'test_ordered_group_runtime_requires_sequence_quantity_and_result_authority'),
            ('quorune.compiler.query_counter_sequences.declared_effect_amount_template',lambda *a,**k:None,
                'test_ordered_amount_clauses_keep_scope_source_spans_and_unknown_clause_boundaries'),
            ('quorune.card_programs.runtime_requirements.typed_result_amount_runtime_dependencies',lambda *a:(),
                'test_ordered_group_runtime_requires_sequence_quantity_and_result_authority'))
        for owner,mutation,method in cases:
            with patch(owner,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_essence_pulse_reads_post_gain_history_and_weakens_every_seat_before_state_actions(self):
        session=self.session(751001);source=self.add(session.engine,'Essence Pulse',zone='hand')
        dying=[self.add(session.engine,'Grizzly Bears',seat=seat,ref='pulse-dies-'+seat) for seat in 'AB']
        survivors=[self.add(session.engine,'Moonshaker Cavalry',seat='C'),self.add(session.engine,'Grizzly Bears',seat='D',ref='pulse-counter-body')]
        survivors[1].counters['+1/+1']=4
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');history=self.history_controls(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Essence Pulse must admit')
        session.state.players['B'].mana_pool.update({'R':1,'W':1})
        self.prepare(session,mana={'B':1,'C':3,'R':1,'W':1});self.checkpoint(session);self.gain_and_lose_three(session,history)
        self.cast(session,'A',source,payment={'B':1,'C':3});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((42,40,40,40),tuple(session.state.players[s].life for s in 'ABCD'))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in dying))
        for card in survivors:
            self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
            self.assertEqual((1,1),(session.engine._numeric_stat(card.object_id,'power'),session.engine._numeric_stat(card.object_id,'toughness')))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[survivors[0].object_id]))
        self.assertEqual(4,session.state.cards[survivors[1].object_id].counters['+1/+1'])
        self.private_replay(session,private)
