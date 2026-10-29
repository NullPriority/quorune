"""CR118.12/707.10: pay once on resolution to copy the referenced spell."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
import inherited_composition_fixture as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

COPY='stack.copy.cast_event_spell'
PAYMENT='effect.choice.optional_fixed_mana_payment'
TEXT='Whenever you cast an instant or sorcery spell, you may pay {3}. If you do, copy that spell. You may choose new targets for the copy.'


class PaidEventCopyCompilerTests(unittest.TestCase):
    def test_paid_copy_component_retains_exact_source_cost_and_target_boundaries(self):
        from quorune.event_spell_copy_model import event_spell_copy_effect
        from quorune.rules.fixed_effect_clause_shapes import closed_effect_component_capabilities
        registry=load_default_capability_registry()
        ir=compile_oracle_card(query_record(TEXT),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertTrue({COPY,PAYMENT}<=set(node.capability_dependencies));self.assertIsNone(node.target_schema);self.assertEqual(3,node.effects[0]['cost']['GENERIC'])
        targeted=compile_oracle_card(query_record('Whenever this creature attacks, you may pay {1}. If you do, copy target instant or sorcery spell. You may choose new targets for the copy.'),capability_registry=registry)
        self.assertEqual('exact',targeted.status,targeted.material_residuals);self.assertIsNotNone(targeted.faces[0].nodes[0].target_schema);self.assertIn('stack.copy.fixed_instant_or_sorcery',targeted.faces[0].nodes[0].capability_dependencies)
        for text in (TEXT.replace('{3}','{X}'),TEXT+' Rewrite history.',TEXT.replace('copy that spell','copy every spell'),TEXT.replace('copy that spell','you may pay {1}. If you do, copy that spell')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        for change in ({'stack':'$target.0'},{'copy_source':'$source'},{'extra':True}):
            self.assertEqual((),closed_effect_component_capabilities(({**event_spell_copy_effect(),**change},),target_schema=None,mechanics={'cast-event-spell-copy'}))

    def test_verified_copy_or_payment_withdrawal_keeps_unrelated_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner in (COPY,PAYMENT):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==owner).update(status='blocked',blockers=['paid copy owner withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)

    def test_omitted_copy_component_resolver_mutant_is_killed(self):
        import quorune.rules.fixed_effect_clause_shapes as owner
        from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
        def witness():self.assertEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch.object(owner,'_COMPONENT_RESOLVERS',tuple(r for r in owner._COMPONENT_RESOLVERS if r is not fixed_stack_spell_copy_node_capabilities)),self.assertRaises(AssertionError):witness()


class PaidEventCopyNativeTests(fixtures.InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'paid-event-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/paid-event-copy-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Paid event copy',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    def pay(self,session,value):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind);command={'action_id':'choose','pay':value};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def retarget(self,session,targets):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind);command={'action_id':'choose','targets':targets};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_mirari_payment_resolution_decline_unaffordable_and_private_draw(self):
        for mode in ('pay','decline','unaffordable'):
            with self.subTest(mode=mode):
                session=self.session(609649001+len(mode));source=self.add(session.engine,'Mirari',zone='hand');spell=self.add(session.engine,'Divination',zone='hand');self.assert_closed(session,source)
                fee=0 if mode=='unaffordable' else 3;self.prepare(session,mana={'C':7+fee,'U':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':2+fee,'U':1});self.settle(session);self.cast(session,'A',spell)
                self.assertEqual(1,self.trigger_count(session,source));self.assertEqual(fee,session.state.players['A'].mana_pool['C']);hand=len(session.state.players['A'].zones['hand']);expected=[session.state.cards[oid].ref for oid in session.state.players['A'].zones['library'][-4:]]
                self.assertEqual('semantic.choice',self.resolve(session).kind);session=self.replay(session,load=True)
                if mode=='unaffordable':
                    before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'choose','pay':True}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
                self.pay(session,mode=='pay');copies=[i for i in session.state.stack if i.kind=='spell_copy'];self.assertEqual(1 if mode=='pay' else 0,len(copies));self.assertEqual(0,self.trigger_count(session,source))
                self.assertEqual(0 if mode=='pay' else fee,session.state.players['A'].mana_pool['C']);self.settle(session);self.assertEqual(hand+(4 if mode=='pay' else 2),len(session.state.players['A'].zones['hand']))
                for seat in 'BCD':
                    for ref in expected:self.assertNotIn(ref,json.dumps(session.packet('pilot:'+seat,full=True)))
                self.replay(session,load=True)

    def test_actual_mirari_copies_countered_x_spell_after_source_destruction_and_retargets(self):
        session=self.session(609649020);source=self.add(session.engine,'Mirari',zone='hand');spell=self.add(session.engine,'Blaze',zone='hand');counter=self.add(session.engine,'Counterspell',seat='B',zone='hand');destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'C':11,'R':1});session.state.players['B'].mana_pool.update({'U':2,'C':1,'W':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':6,'R':1});self.settle(session);self.priority(session,'A');offered=self.action(session,'A',spell)
        result=session.act('pilot:A',{'action_id':offered['id'],'x':3,'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary);original=next(i for i in session.state.stack if i.card_object_id==spell.object_id)
        self.cast_for(session,'B',counter,[original.ref],reserve={'C':1,'W':1});self.cast(session,'B',destroy,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone);session=self.replay(session,load=True);self.pay(session,True);self.retarget(session,['C'])
        copied=next(i for i in session.state.stack if i.kind=='spell_copy');self.assertEqual(3,copied.x_value);self.assertEqual(['C'],copied.targets);self.assertIsNone(copied.mana_spent_counts);self.assertEqual((),copied.mana_colors_spent);life={s:session.state.players[s].life for s in 'BC'}
        session=self.replay(session,load=True);self.settle(session);self.assertEqual(life['B'],session.state.players['B'].life);self.assertEqual(life['C']-3,session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_riku_keeps_modal_choice_and_uses_separate_entering_creature_copy(self):
        session=self.session(609649030);source=self.add(session.engine,'Riku of Two Reflections',zone='hand');modal=self.add(session.engine,'Healing Salve',zone='hand');body=self.add(session.engine,'Memnite',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');foreign=self.add(session.engine,'Shock',seat='B',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'G':2,'U':3,'R':2,'W':1});session.state.players['B'].mana_pool.update({'U':1,'R':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'G':1,'U':2,'R':1,'W':1});self.settle(session);self.priority(session,'A');offered=self.action(session,'A',modal);life={s:session.state.players[s].life for s in 'CD'}
        result=session.act('pilot:A',{'action_id':offered['id'],'modes':['mode_1'],'targets':['C'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual('semantic.choice',self.resolve(session).kind);self.pay(session,True);self.retarget(session,['D'])
        copied=next(i for i in session.state.stack if i.kind=='spell_copy');self.assertEqual(['mode_1'],copied.modes);self.settle(session);self.assertEqual(life['C']+3,session.state.players['C'].life);self.assertEqual(life['D']+3,session.state.players['D'].life)
        self.cast(session,'A',body);self.resolve_top(session);self.assertEqual(1,self.trigger_count(session,source));self.cast(session,'B',bounce,[body.ref]);self.resolve_top(session);self.assertEqual('hand',session.state.cards[body.object_id].zone);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session=self.replay(session,load=True);self.pay(session,True);self.settle(session);tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token];self.assertEqual(1,len(tokens));self.assertEqual('Memnite',tokens[0].printed_name);self.assertEqual(0,self.trigger_count(session,source))
        self.cast(session,'B',foreign,['A']);self.assertEqual(0,self.trigger_count(session,source));self.settle(session);self.replay(session,load=True)

    def test_actual_mendicant_requires_max_speed_and_resolves_one_artifact_spell_copy(self):
        from quorune.rules.player_speed_model import player_speed
        session=self.session(609649040);source=self.add(session.engine,'Mendicant Core, Guidelight',zone='hand');below=self.add(session.engine,'Memnite',zone='hand',ref='below-max-artifact');above=self.add(session.engine,'Memnite',zone='hand',ref='above-max-artifact');shock=self.add(session.engine,'Shock',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'W':1,'U':1,'R':1,'C':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'R':1,'C':1});self.settle(session);self.assertEqual(1,player_speed(session.state.players['A']));self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'))
        session.state.players['A'].stats['speed']=3;self.checkpoint(session);self.cast(session,'A',below);self.assertEqual(0,self.trigger_count(session,source));self.settle(session);self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',shock,['B']);self.settle(session);self.assertEqual(4,player_speed(session.state.players['A']));self.cast(session,'A',above);self.assertEqual(1,self.trigger_count(session,source));self.assertEqual(1,session.state.players['A'].mana_pool['C']);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session=self.replay(session,load=True);self.pay(session,True);copies=[i for i in session.state.stack if i.kind=='spell_copy'];self.assertEqual(1,len(copies));self.assertEqual('A',copies[0].controller);self.assertNotEqual(copies[0].ref,next(i.ref for i in session.state.stack if i.card_object_id==above.object_id and i.kind=='spell'))
        self.settle(session);tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token];self.assertEqual(1,len(tokens));self.assertEqual('token',tokens[0].object_kind);self.assertEqual('battlefield',session.state.cards[above.object_id].zone);self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'));self.replay(session,load=True)

    def test_paid_runtime_copy_owner_is_inferred_without_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(609649050);checked=0
        for name in ('Mirari','Riku of Two Reflections','Mendicant Core, Guidelight'):
            source=self.add(session.engine,name)
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if program.event!='spell.cast' or COPY not in program.capability_dependencies:continue
                deps=tuple(c for c in program.capability_dependencies if c!=COPY);changed=replace(program,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+COPY,result['blockers']);checked+=1
        self.assertEqual(3,checked)
