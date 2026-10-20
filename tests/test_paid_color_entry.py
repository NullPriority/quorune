from __future__ import annotations

"""CR 400.7d/614.1c: actual paid-color counts govern self-entry counters."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from scripts.build_test_database import build_fixture_database
import test_turn_count_self_costs as controls


class PaidColorEntryTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCountSelfCostTests.session
    add=controls.TurnCountSelfCostTests.add
    prepare=controls.TurnCountSelfCostTests.prepare
    checkpoint=controls.TurnCountSelfCostTests.checkpoint
    resolve=controls.TurnCountSelfCostTests.resolve
    replay=controls.TurnCountSelfCostTests.replay
    binding=controls.TurnCountSelfCostTests.binding
    actions=controls.TurnCountSelfCostTests.actions
    priority=controls.TurnCountSelfCostTests.priority
    advance=controls.TurnCountSelfCostTests.advance
    private_replay=controls.TurnCountSelfCostTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'paid-color-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/paid-color-entry-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual paid-color entry',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def test_actual_ardenvale_cast_counts_three_paid_white_and_enters_with_one_counter(self):
        session=self.session(747100);source=self.add(session.engine,'Ardenvale Paladin',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':3,'C':1});self.checkpoint(session)
        action=next((row for row in self.actions(session) if row['id']=='cast:'+source.ref),None)
        self.assertIsNotNone(action)
        command={'action_id':action['id'],'pay':'manual','payment':{'W':3,'C':1}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'payment':{'W':4,'C':1}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        entered=session.state.cards[source.object_id]
        self.assertEqual('battlefield',entered.zone);self.assertEqual(1,entered.counters.get('+1/+1',0))
        self.assertEqual((3,6),(session.engine._numeric_stat(entered.object_id,'power'),session.engine._numeric_stat(entered.object_id,'toughness')))
        self.private_replay(session,private)

    def cast_paid(self,session,source,payment,targets=()):
        action=next((row for row in self.actions(session) if row['id']=='cast:'+source.ref),None)
        self.assertIsNotNone(action)
        command={'action_id':action['id'],'pay':'manual','payment':payment}
        if targets:command['targets']=list(targets)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_five_originals_bind_completely_and_count_actual_units_for_each_color(self):
        for index,(name,color,total,keyword) in enumerate((('Ardenvale Paladin','W',4,None),
            ('Embereth Paladin','R',4,'haste'),('Garenbrig Paladin','G',5,None),
            ('Locthwain Paladin','B',4,'menace'),('Vantress Paladin','U',4,'flying'))):
            for paid in (2,3):
                with self.subTest(name=name,paid=paid):
                    session=self.session(747110+index*10+paid);source=self.add(session.engine,name,zone='hand')
                    record=self.db.lookup(name)
                    ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                    self.prepare(session,mana={color:paid,'C':total-paid})
                    self.cast_paid(session,source,{color:paid,'C':total-paid})
                    spell=session.state.stack[-1];self.assertEqual(paid,spell.mana_spent_counts.count_for(source,color))
                    self.assertEqual((color,),spell.mana_colors_spent)
                    self.assertIsNone(self.resolve(session));entered=session.state.cards[source.object_id]
                    amount=int(paid>=3);self.assertEqual(amount,entered.counters.get('+1/+1',0))
                    self.assertEqual((int(record.power)+amount,int(record.toughness)+amount),
                        (session.engine._numeric_stat(entered.object_id,'power'),session.engine._numeric_stat(entered.object_id,'toughness')))
                    if keyword:self.assertIn(keyword,session.engine._combat_keywords(entered))

    def test_excess_white_qualifies_but_three_wrong_color_units_do_not(self):
        for index,payment in enumerate(({'W':4},{'W':1,'U':3})):
            session=self.session(747170+index);source=self.add(session.engine,'Ardenvale Paladin',zone='hand')
            self.prepare(session,mana=payment);self.checkpoint(session);self.cast_paid(session,source,payment)
            self.assertIsNone(self.resolve(session));self.assertEqual(int(index==0),source.counters.get('+1/+1',0))
            self.replay(session)

    def test_actual_entry_counter_is_modified_by_hardened_scales(self):
        session=self.session(747180);source=self.add(session.engine,'Ardenvale Paladin',zone='hand')
        self.add(session.engine,'Hardened Scales');self.prepare(session,mana={'W':3,'C':1});self.checkpoint(session)
        self.cast_paid(session,source,{'W':3,'C':1});session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertEqual(2,source.counters.get('+1/+1',0));self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
        self.replay(session)

    def test_noncast_return_does_not_reuse_a_prior_paid_spell_incarnation(self):
        session=self.session(747190);source=self.add(session.engine,'Ardenvale Paladin',zone='hand')
        kill=self.add(session.engine,'Murder',zone='hand');bring=self.add(session.engine,'Zombify',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':3,'B':3,'C':5});self.checkpoint(session)
        self.cast_paid(session,source,{'W':3,'C':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(1,source.counters.get('+1/+1',0));incarnation=source.logical_object_id
        self.cast_paid(session,kill,{'B':2,'C':1},[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',source.zone)
        self.cast_paid(session,bring,{'B':1,'C':3},[source.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertEqual('battlefield',source.zone);self.assertNotEqual(incarnation,source.logical_object_id)
        self.assertEqual(0,source.counters.get('+1/+1',0));self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.private_replay(session,private)

    def test_existing_spell_copy_owner_does_not_copy_actual_payment_counts(self):
        session=self.session(747200);source=self.add(session.engine,'Ardenvale Paladin',zone='hand')
        self.prepare(session,mana={'W':3,'C':1});self.cast_paid(session,source,{'W':3,'C':1})
        original=session.state.stack[-1]
        copied=session.engine._copy_stack_item(controller='A',target=original,targets=(),target_groups={},reason='Paid-color copy owner control')
        self.assertIsNone(copied.mana_spent_counts);self.assertEqual((),copied.mana_colors_spent)
        copy_id=copied.card_object_id;self.checkpoint(session);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.cards[copy_id].counters.get('+1/+1',0))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(session)

    def test_legacy_no_count_execution_retains_an_absent_payment_field(self):
        session=self.session(747210);session.state.spell_mana_counts_version=None
        source=self.add(session.engine,'Grizzly Bears',zone='hand');self.prepare(session,mana={'G':1,'C':1});self.checkpoint(session)
        self.cast_paid(session,source,{'G':1,'C':1});self.assertIsNone(session.state.stack[-1].mana_spent_counts)
        self.assertNotIn('mana_spent_counts',session.state.stack[-1].to_dict())
        self.assertNotIn('spell_mana_counts_version',session.state.to_dict())
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.replay(session)

    def test_legacy_generic_permanent_copy_records_its_exact_resolution_provenance(self):
        session=self.session(747211);session.state.spell_mana_counts_version=None
        source=self.add(session.engine,'Grizzly Bears',zone='hand');self.prepare(session,mana={'G':1,'C':1})
        self.cast_paid(session,source,{'G':1,'C':1});original=session.state.stack[-1]
        copied=session.engine._copy_stack_item(controller='A',target=original,targets=(),target_groups={},reason='Legacy generic copy provenance control')
        self.checkpoint(session);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[copied.card_object_id].zone)
        self.replay(session)

    def test_copy_provenance_retains_the_historical_no_runtime_trust_mode(self):
        from quorune.record_trust import implicit_semantic_execution_provenance,semantic_execution_provenance_row,validate_programs_used_provenance
        session=self.session(747212);source=self.add(session.engine,'Grizzly Bears',zone='hand')
        self.prepare(session,mana={'G':1,'C':1});self.cast_paid(session,source,{'G':1,'C':1})
        original=session.state.stack[-1]
        copied=session.engine._copy_stack_item(controller='A',target=original,targets=(),target_groups={},reason='Historical copied fallback control')
        proxy=SimpleNamespace(state=SimpleNamespace(stack=[copied],cards=session.state.cards),
            card_db=session.engine.card_db,card_record=session.engine.card_record,_trusted_generic_spell=session.engine._trusted_generic_spell,
            semantics=session.engine.semantics,semantic_program_is_current_trusted=session.engine.semantic_program_is_current_trusted)
        key=copied.semantic_key
        self.assertIsNone(implicit_semantic_execution_provenance(proxy,key))
        current=semantic_execution_provenance_row(session.engine,key,capability_registry=self.registry,profile='commander_review',stack_item=copied)
        self.assertEqual(source.oracle_id,current['oracle_id'])
        self.assertEqual('trusted_generic_permanent_spell_copy',current['implicit_fallback'])
        old={'key':key,'oracle_id':None,'version':None,'builtin':False}
        validate_programs_used_provenance([old],session.engine.semantics,profile='commander_review',require_runtime_provenance=False,
            sequence=1,implicit_provenance=lambda value:implicit_semantic_execution_provenance(proxy,value))
        lookup=lambda value:implicit_semantic_execution_provenance(proxy,value,include_spell_copies=True)
        validate_programs_used_provenance([current],session.engine.semantics,profile='commander_review',require_runtime_provenance=True,sequence=1,implicit_provenance=lookup)
        with self.assertRaisesRegex(ValueError,'provenance mismatch'):
            validate_programs_used_provenance([old],session.engine.semantics,profile='commander_review',require_runtime_provenance=True,sequence=1,implicit_provenance=lookup)

    def test_fresh_copy_provenance_omission_is_killed(self):
        import quorune.record_trust as owner
        original=owner.physical_stack_record
        def without_copies(host,item):return None if item.kind=='spell_copy' else original(host,item)
        method='test_legacy_generic_permanent_copy_records_its_exact_resolution_provenance'
        with patch.object(owner,'physical_stack_record',side_effect=without_copies):
            with self.assertRaisesRegex(ValueError,'provenance mismatch for oracle_id'):
                getattr(type(self)(method),method)()

    def test_actual_clone_uses_its_own_payment_after_acquiring_the_entry_ability(self):
        session=self.session(747220);source=self.add(session.engine,'Ardenvale Paladin')
        source.counters['+1/+1']=7
        self.add(session.engine,'Sphere of Resistance');clone=self.add(session.engine,'Clone',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':3,'U':2});self.checkpoint(session)
        self.cast_paid(session,clone,{'W':3,'U':2})
        pending=self.resolve(session);self.assertEqual('replacement.order',pending.kind)
        session=self.replay(session,load=True)
        option=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['options']
            if source.ref in row['id'] and not row['id'].startswith('decline:'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':option['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','replacement':option['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));entered=session.state.cards[clone.object_id]
        self.assertEqual(1,entered.counters.get('+1/+1',0));self.assertEqual(7,session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual((3,6),(session.engine._numeric_stat(entered.object_id,'power'),session.engine._numeric_stat(entered.object_id,'toughness')))
        self.private_replay(session,private)

    def test_actual_garenbrig_counter_and_current_power_blocking_restriction_compose(self):
        session=self.session(747230);source=self.add(session.engine,'Garenbrig Paladin',zone='hand')
        self.add(session.engine,'Fervor');small=self.add(session.engine,'Grizzly Bears',seat='B')
        large=self.add(session.engine,'Hill Giant',seat='B')
        self.prepare(session,mana={'G':3,'C':2});self.checkpoint(session);self.cast_paid(session,source,{'G':3,'C':2})
        self.assertIsNone(self.resolve(session));self.assertEqual(1,source.counters.get('+1/+1',0))
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{small.ref:[source.ref]}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'block','blocks':{large.ref:[source.ref]}});self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual('graveyard',session.state.cards[large.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual(40,session.state.players['B'].life);self.replay(session)

    def test_compiler_amount_and_handler_boundaries_are_closed(self):
        from dataclasses import replace
        from test_bound_effect_programs import record
        from quorune.compiler.paid_color_entry_templates import paid_color_entry_amount
        from quorune.entry_counter_model import DynamicEntryCounterAmountSpec
        from quorune.semantic_runtime.self_entry_counters import DynamicSelfEntryCounterHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        text='Adamant — If at least three white mana was spent to cast this spell, this creature enters with a +1/+1 counter on it.'
        source=record(text,'Creature')
        ir=compile_oracle_card(source,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
        self.assertEqual(text,source.oracle_text[node.span.start:node.span.end])
        descriptor=dict(node.handlers[0]);spec=DynamicSelfEntryCounterHandler().validate(descriptor).amount_spec
        self.assertEqual('W',spec.mana_color);self.assertEqual([0,0,1,1],[spec.amount(n) for n in (0,2,3,4)])
        self.assertEqual(spec,DynamicEntryCounterAmountSpec.from_dict(spec.to_dict()))
        missing={k:v for k,v in descriptor.items() if k!='requires_complete_card_program'}
        with self.assertRaises(SemanticNodeError):DynamicSelfEntryCounterHandler().validate(missing)
        with self.assertRaises(SemanticNodeError):DynamicSelfEntryCounterHandler().validate({**descriptor,'schema_version':True})
        for raw in ({**spec.to_dict(),'mana_color':'C'},{**spec.to_dict(),'minimum':True},
                {**spec.to_dict(),'value_source':'mana_colors_spent'},{**spec.to_dict(),'extra':1}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):DynamicEntryCounterAmountSpec.from_dict(raw)
        for changed in (text.replace('three white','X white'),text.replace('three white','three snow'),
                text.replace('at least','exactly'),text.replace('this creature','another creature'),text+' Draw a card.',
                text.replace('a +1/+1 counter','your choice of a +1/+1 counter or a flying counter')):
            with self.subTest(changed=changed):
                self.assertIsNone(paid_color_entry_amount(changed.split(' — ',1)[-1],source_name=source.name))
                self.assertNotEqual('exact',compile_oracle_card(replace(source,oracle_text=changed),
                    capability_registry=self.registry,capability_profile='commander_review').status)
        _,general=paid_color_entry_amount('If at least two blue mana was spent to cast this spell, this artifact enters with two charge counters on it.',source_name='Generic entry source')
        self.assertEqual([0,0,2,2],[general.amount(n) for n in range(4)])

    def test_verified_capability_withdrawal_and_stripped_runtime_minima_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        path=ROOT/'quorune/rules/capability-registry.json';base=json.loads(path.read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        discovered=set(discover_tests(ROOT).values())
        def verified(raw):
            registry=CapabilityRegistry(raw)
            index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Ardenvale Paladin'),capability_registry=verified(base)).status)
        for cap in ('counter.producer.paid_color_self_entry','casting.payment.mana_unit_counts','counter.producer.dynamic_self_entry','counter.placement.quantity_replacement'):
            raw=json.loads(path.read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn paid-entry authority'])
            ir=compile_oracle_card(self.db.lookup('Ardenvale Paladin'),capability_registry=verified(raw))
            self.assertNotEqual('exact',ir.status)
            blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(747240);source=self.add(session.engine,'Ardenvale Paladin')
        program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='zone.change']
        for cap in ('counter.producer.paid_color_self_entry','casting.payment.mana_unit_counts','counter.producer.dynamic_self_entry'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            modified=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            binding=bind_semantic_program_runtime(modified,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_fresh_entry_producer_payment_capture_and_color_count_mutants_are_killed(self):
        method='test_actual_ardenvale_cast_counts_three_paid_white_and_enters_with_one_counter'
        for target,kwargs in (
            ('quorune.compiler.paid_color_entry_templates.paid_color_entry_amount',{'return_value':None}),
            ('quorune.rules.casting.commit.capture_spell_mana_counts',{'return_value':None}),
            ('quorune.spell_mana_counts.SpellManaSpentCounts.count_for',{'return_value':0}),
        ):
            with self.subTest(target=target),patch(target,**kwargs):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_payment_fact_is_closed_and_legacy_game_and_stack_shapes_stay_absent(self):
        from dataclasses import replace
        from quorune.model import GameState,StackItem
        from quorune.spell_mana_counts import SpellManaSpentCounts,capture_spell_mana_counts
        card=SimpleNamespace(object_id='original',logical_object_id='original:1')
        fact=SpellManaSpentCounts.from_payment(card,{'W':3,'C':1})
        self.assertEqual(3,fact.count_for(card,'W'));self.assertEqual(0,fact.count_for(card,'U'))
        self.assertEqual(fact,SpellManaSpentCounts.from_dict(fact.to_dict()))
        wire=fact.to_dict()
        for value in ({**wire,'schema_version':True},{**wire,'amounts':{'W':3}},
                {**wire,'amounts':{**wire['amounts'],'U':True}},{**wire,'extra':1}):
            with self.subTest(value=value),self.assertRaises(ValueError):SpellManaSpentCounts.from_dict(value)
        with self.assertRaises(ValueError):fact.count_for(SimpleNamespace(object_id='original',logical_object_id='original:2'),'W')
        old=StackItem('old','S1','spell','A','Legacy spell',card_object_id='original')
        self.assertNotIn('mana_spent_counts',old.to_dict());self.assertEqual(old,StackItem.from_dict(old.to_dict()))
        with self.assertRaises(ValueError):replace(old,kind='spell_copy',mana_spent_counts=fact)
        self.assertIsNone(capture_spell_mana_counts(None,card,{'W':3,'C':1}))
        session=self.session(747101);payload=session.state.to_dict();self.assertEqual(1,payload['spell_mana_counts_version'])
        payload.pop('spell_mana_counts_version');legacy=GameState.from_dict(payload)
        self.assertIsNone(legacy.spell_mana_counts_version);self.assertNotIn('spell_mana_counts_version',legacy.to_dict())
        for version in (True,0,2,'1'):
            with self.subTest(version=version),self.assertRaises(ValueError):GameState.from_dict({**payload,'spell_mana_counts_version':version})
