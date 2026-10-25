from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,_reviewed_effect_template
from quorune.rules.capabilities import load_default_capability_registry
from quorune.kicked_target_model import (KICKED_TARGET_CAPABILITY as CAP,bound_kicked_target_schema,
    validate_kicked_target_binding,validate_kicked_target_instruction)
from quorune.compiler.kicked_targets import kicked_target_template
from scripts.build_test_database import build_fixture_database
import test_nonmana_kicker as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Jilt','Dwarven Landslide','Rushing River','Magma Burst','Urborg Repossession','Probe')


class KickedTargetGroupTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.NonmanaKickerTests.session
    start=controls.NonmanaKickerTests.start
    add=controls.NonmanaKickerTests.add
    ready=controls.NonmanaKickerTests.ready
    prepare=controls.NonmanaKickerTests.prepare
    checkpoint=controls.NonmanaKickerTests.checkpoint
    resolve=controls.NonmanaKickerTests.resolve
    replay=controls.NonmanaKickerTests.replay
    binding=controls.NonmanaKickerTests.binding
    priority=controls.NonmanaKickerTests.priority
    private_replay=controls.NonmanaKickerTests.private_replay
    private=controls.NonmanaKickerTests.private
    reject=controls.NonmanaKickerTests.reject
    stats=controls.NonmanaKickerTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'kicked-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/kicked-target-group-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Kicker target roles',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def cast(self,s,card,targets=(),*,kicked=False,seat='A',**extra):
        self.priority(s,seat);rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        row=next((a for a in rows if a['id']=='cast:'+card.ref),None);self.assertIsNotNone(row,card.printed_name)
        command={'action_id':row['id'],'targets':targets if isinstance(targets,dict) else list(targets),'pay':'auto',
            **({'cost_option':'kicked'} if kicked else {}),**extra}
        self.reject(s,command,'D');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_target_roles_bind_to_kicker_and_refuse_drift_unknown_scopes_and_ambiguous_results(self):
        text="Return target creature to its owner's hand. If this spell was kicked, it deals 2 damage to another target creature."
        result=kicked_target_template(text,source_name='Witness',compile_component=lambda t:_reviewed_effect_template(t,card_name='Witness'))
        self.assertIsNotNone(result);wrapper=result[1][-1];binding=wrapper['target_binding']
        self.assertEqual((1,1),tuple(g['min'] for g in bound_kicked_target_schema(binding,True)['groups']))
        self.assertEqual((1,0),tuple(g['min'] for g in bound_kicked_target_schema(binding,False)['groups']))
        self.assertEqual(['target_0'],binding['groups'][1]['different_from_groups'])
        for value in (None,1,'kicked'):
            with self.assertRaises(ValueError):bound_kicked_target_schema(binding,value)
        for update in ({'schema_version':True},{'conditional_group':'target_0'},{'unknown':True},{'groups':[]}):
            with self.assertRaises(ValueError):validate_kicked_target_binding({**binding,**update})
        drift=deepcopy(binding);drift['groups'][0]['schema']['up_to']=2
        with self.assertRaises(ValueError):validate_kicked_target_binding(drift)
        for update in ({'schema_version':True},{'cast_fact':1},{'effects':[{'op':'draw','player':'$controller','count':1}]},{'unknown':1}):
            with self.assertRaises(ValueError):validate_kicked_target_instruction({**wrapper,**update})
        for text in ['If this spell was kicked, draw X cards.','If this spell was kicked, destroy another target land.',
                'Destroy target land. If this spell was kicked, instead destroy another target land.']:
            self.assertIsNone(kicked_target_template(text,source_name='Witness',compile_component=lambda t:_reviewed_effect_template(t,card_name='Witness')))

    def test_six_originals_have_complete_normal_registration_and_independent_group_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.start(792000)
        for name in NAMES:
            c=self.add(s.engine,name,zone='hand');bound=self.binding(s,c)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            p=next(p for p in s.engine.semantics.programs_for_oracle(c.oracle_id) if CAP in p.capability_dependencies)
            deps=tuple(d for d in p.capability_dependencies if d!=CAP)
            changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,result['blockers'])

    def test_jilt_ordinary_and_paid_have_distinct_required_roles_and_real_results(self):
        for kicked in (False,True):
            s=self.start(792010+kicked);source=self.add(s.engine,'Jilt',zone='hand')
            first=self.add(s.engine,'Generic Bound Body',seat='B',ref='kicked-jilt-first');second=self.add(s.engine,'Generic Bound Body',seat='C',ref='kicked-jilt-second')
            private=self.private(s);self.ready(s)
            wrong={'target_0':[first.ref],'target_1':[first.ref] if kicked else [second.ref]}
            self.reject(s,{'action_id':'cast:'+source.ref,'targets':wrong,'pay':'auto',**({'cost_option':'kicked'} if kicked else {})},'A')
            targets={'target_0':[first.ref],'target_1':[second.ref] if kicked else []}
            self.cast(s,source,targets,kicked=kicked);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('hand',s.state.cards[first.object_id].zone);self.assertEqual(2 if kicked else 0,s.state.cards[second.object_id].marked_damage)
            self.private_replay(s,private)

    def test_river_and_landslide_pay_nonmana_costs_and_keep_separate_target_domains(self):
        for name in ('Rushing River','Dwarven Landslide'):
            for kicked in (False,True):
                s=self.start(792020+(name=='Dwarven Landslide')*2+kicked);source=self.add(s.engine,name,zone='hand')
                land=self.add(s.engine,'Forest');first=self.add(s.engine,'Island' if name=='Dwarven Landslide' else 'Sol Ring',seat='B')
                second=self.add(s.engine,'Plains' if name=='Dwarven Landslide' else 'Generic Bound Body',seat='C')
                private=self.private(s);self.ready(s)
                targets={'target_0':[first.ref],'target_1':[second.ref] if kicked else []}
                self.cast(s,source,targets,kicked=kicked,**({'sacrifice_cards':[land.ref]} if kicked else {}))
                self.assertEqual('graveyard' if kicked else 'battlefield',s.state.cards[land.object_id].zone)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));destination='graveyard' if name=='Dwarven Landslide' else 'hand'
                self.assertEqual(destination,s.state.cards[first.object_id].zone);self.assertEqual(destination if kicked else 'battlefield',s.state.cards[second.object_id].zone)
                self.private_replay(s,private)

    def test_magma_burst_paid_sacrifices_two_lands_and_targets_distinct_player_or_permanent(self):
        for kicked in (False,True):
            s=self.start(792040+kicked);source=self.add(s.engine,'Magma Burst',zone='hand')
            lands=[self.add(s.engine,'Forest',ref='kicked-magma-land-'+str(i)) for i in range(2)]
            creature=self.add(s.engine,'Generic Bound Body',seat='C');private=self.private(s);self.ready(s)
            self.cast(s,source,{'target_0':['B'],'target_1':[creature.ref] if kicked else []},kicked=kicked,
                **({'sacrifice_cards':[c.ref for c in lands]} if kicked else {}))
            self.assertTrue(all(s.state.cards[c.object_id].zone==('graveyard' if kicked else 'battlefield') for c in lands))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['B'].life)
            self.assertEqual(3 if kicked else 0,s.state.cards[creature.object_id].marked_damage);self.private_replay(s,private)

    def test_repossession_returns_distinct_owned_card_roles_and_keeps_life_gain(self):
        for kicked in (False,True):
            s=self.start(792050+kicked);source=self.add(s.engine,'Urborg Repossession',zone='hand')
            first=self.add(s.engine,'Grizzly Bears',zone='graveyard');second=self.add(s.engine,'Sol Ring',zone='graveyard')
            private=self.private(s);self.ready(s)
            self.cast(s,source,{'target_0':[first.ref],'target_1':[second.ref] if kicked else []},kicked=kicked)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(42,s.state.players['A'].life)
            self.assertEqual('hand',s.state.cards[first.object_id].zone);self.assertEqual('hand' if kicked else 'graveyard',s.state.cards[second.object_id].zone)
            self.private_replay(s,private)

    def test_probe_requires_its_discard_target_only_when_paid_after_private_prefix_draw_discard(self):
        for kicked in (False,True):
            s=self.start(792060+kicked);source=self.add(s.engine,'Probe',zone='hand')
            for i in range(3):self.add(s.engine,'Generic Bound Plains',seat='B',zone='hand',ref='kicked-probe-b-'+str(i))
            private=self.private(s);self.ready(s);before=len(s.state.players['A'].zones['hand'])
            self.cast(s,source,{'target_1':['B'] if kicked else []},kicked=kicked)
            decision=self.resolve(s);self.assertIsNotNone(decision)
            for _ in range(3):
                if decision is None:break
                actor=s.pending_principals()[0];seat=actor[-1];hand=s.state.players[seat].zones['hand']
                refs=[s.state.cards[oid].ref for oid in hand[:2]];self.assertEqual(2,len(refs))
                s=self.replay(s,load=True);result=s.act(actor,{'action_id':'choose','cards':refs});self.assertTrue(result.ok,result.summary)
                decision=self.resolve(s)
            self.assertIsNone(decision);self.assertEqual(before,len(s.state.players['A'].zones['hand']))
            self.assertEqual(1 if kicked else 3,len(s.state.players['B'].zones['hand']));self.private_replay(s,private)

    def test_partial_and_all_target_loss_use_original_roles_and_do_not_retarget_returned_incarnations(self):
        for lost in ('first','second','both'):
            s=self.start(792070+('first','second','both').index(lost));source=self.add(s.engine,'Jilt',zone='hand')
            first=self.add(s.engine,'Generic Bound Body',seat='B',ref='kicked-loss-first');second=self.add(s.engine,'Generic Bound Body',seat='C',ref='kicked-loss-second')
            responses=[self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='kicked-loss-bounce-'+str(i)) for i in range(2)]
            s.state.players['B'].mana_pool['U']=2;private=self.private(s);self.ready(s)
            self.cast(s,source,{'target_0':[first.ref],'target_1':[second.ref]},kicked=True)
            if lost in ('first','both'):self.cast(s,responses[0],[first.ref],seat='B')
            if lost in ('second','both'):self.cast(s,responses[1],[second.ref],seat='B')
            self.assertIsNone(self.resolve(s))
            self.assertEqual('hand',s.state.cards[first.object_id].zone)
            self.assertEqual('hand' if lost in ('second','both') else 'battlefield',s.state.cards[second.object_id].zone)
            self.assertEqual(2 if lost=='first' else 0,s.state.cards[second.object_id].marked_damage)
            self.private_replay(s,private)

    def test_paid_target_can_be_sacrificed_as_the_cost_without_suppressing_other_legal_target(self):
        s=self.start(792080);source=self.add(s.engine,'Dwarven Landslide',zone='hand');payer=self.add(s.engine,'Forest')
        other=self.add(s.engine,'Island',seat='B');private=self.private(s);self.ready(s)
        self.cast(s,source,{'target_0':[payer.ref],'target_1':[other.ref]},kicked=True,sacrifice_cards=[payer.ref])
        self.assertEqual('graveyard',s.state.cards[payer.object_id].zone);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[other.object_id].zone);self.private_replay(s,private)

    def test_copy_inherits_paid_roles_and_new_target_constraints_without_paying_again(self):
        s=self.start(792090);source=self.add(s.engine,'Jilt',zone='hand');copy=self.add(s.engine,'Reverberate',zone='hand')
        cards=[self.add(s.engine,'Generic Bound Body',seat='B',ref='kicked-copy-body-'+str(i)) for i in range(4)]
        private=self.private(s);self.ready(s);self.cast(s,source,{'target_0':[cards[0].ref],'target_1':[cards[1].ref]},kicked=True)
        before=dict(s.state.players['A'].mana_pool);self.cast(s,copy,[s.state.stack[-1].ref]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        duplicate={'target_0':[cards[2].ref],'target_1':[cards[2].ref]}
        self.reject(s,{'action_id':'choose','targets':duplicate},'A')
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':{'target_0':[cards[2].ref],'target_1':[cards[3].ref]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        for index in (0,2):self.assertEqual('hand',s.state.cards[cards[index].object_id].zone)
        for index in (1,3):self.assertEqual(2,s.state.cards[cards[index].object_id].marked_damage)
        self.assertEqual(sum(before.values())-2,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_current_cost_and_target_schema_seals_reject_paid_count_or_predicate_drift(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.rules.casting.kicked_targets import validate_kicked_target_commit
        from quorune.rules.casting.proposal import _spell_semantic_key
        s=self.start(792100);source=self.add(s.engine,'Jilt',zone='hand');self.add(s.engine,'Generic Bound Body')
        self.add(s.engine,'Generic Bound Body',seat='B',ref='kicked-seal-other');self.ready(s)
        program=s.engine.semantics.get(_spell_semantic_key(self.db.lookup('Jilt'),None));self.assertIsNotNone(program)
        options=build_cast_cost_options(s.engine,'A',source,program,hint=True)
        self.assertTrue(options)
        for value in options:
            option=value.to_dict();validate_kicked_target_commit(program,option)
            changed=deepcopy(option);changed['target_schema']['groups'][-1]['min']=0 if option['kind']=='kicker' else 1
            with self.assertRaises(ValueError):validate_kicked_target_commit(program,changed)
            changed=deepcopy(option);changed['target_schema']['groups'][-1]['types_any']=['land']
            with self.assertRaises(ValueError):validate_kicked_target_commit(program,changed)

    def test_bounced_then_recast_target_is_a_new_incarnation_and_is_not_damaged_by_old_role(self):
        s=self.start(792110);source=self.add(s.engine,'Jilt',zone='hand')
        first=self.add(s.engine,'Generic Bound Body',seat='C',ref='kicked-reentry-first');second=self.add(s.engine,'Ashcoat Bear',seat='B',ref='kicked-reentry-second')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');s.state.players['B'].mana_pool.update(U=1,G=1,C=1)
        private=self.private(s);self.ready(s);logical=s.state.cards[second.object_id].logical_object_id
        self.cast(s,source,{'target_0':[first.ref],'target_1':[second.ref]},kicked=True)
        self.cast(s,bounce,[second.ref],seat='B')
        for _ in range(12):
            if s.state.cards[second.object_id].zone=='hand':break
            decision=s.state.pending_decision;self.assertIsNotNone(decision);self.assertEqual('priority',decision.kind)
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[second.object_id].zone)
        self.cast(s,s.state.cards[second.object_id],seat='B')
        for _ in range(12):
            if s.state.cards[second.object_id].zone=='battlefield':break
            decision=s.state.pending_decision;self.assertIsNotNone(decision);self.assertEqual('priority',decision.kind)
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',s.state.cards[second.object_id].zone)
        self.assertNotEqual(logical,s.state.cards[second.object_id].logical_object_id)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[first.object_id].zone)
        self.assertEqual(0,s.state.cards[second.object_id].marked_damage);self.private_replay(s,private)

    def test_unpaid_copy_cannot_add_the_conditional_target(self):
        s=self.start(792120);source=self.add(s.engine,'Jilt',zone='hand');copy=self.add(s.engine,'Reverberate',zone='hand')
        first=self.add(s.engine,'Generic Bound Body',seat='B',ref='kicked-unpaid-first');second=self.add(s.engine,'Generic Bound Body',seat='C',ref='kicked-unpaid-second')
        private=self.private(s);self.ready(s);self.cast(s,source,{'target_0':[first.ref],'target_1':[]})
        self.cast(s,copy,[s.state.stack[-1].ref]);decision=self.resolve(s);self.assertIsNotNone(decision)
        self.reject(s,{'action_id':'choose','targets':{'target_0':[second.ref],'target_1':[first.ref]}},'A')
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':{'target_0':[second.ref],'target_1':[]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[first.object_id].zone);self.assertEqual('hand',s.state.cards[second.object_id].zone);self.private_replay(s,private)

    def test_one_eligible_creature_keeps_normal_jilt_available_and_withholds_impossible_paid_option(self):
        s=self.start(792130);source=self.add(s.engine,'Jilt',zone='hand');target=self.add(s.engine,'Generic Bound Body',seat='B')
        private=self.private(s);self.ready(s)
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'];row=next((a for a in rows if a['id']=='cast:'+source.ref),None)
        self.assertIsNotNone(row);ids={v['id'] for v in row['cost_options']};self.assertIn('normal',ids);self.assertNotIn('kicked',ids)
        normal=next(v for v in row['cost_options'] if v['id']=='normal');self.assertEqual(0,normal['target_schema']['groups'][1]['max'])
        self.cast(s,source,{'target_0':[target.ref],'target_1':[]});self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[target.object_id].zone);self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_target_binding_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn kicked targets'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Jilt'),capability_registry=registry,capability_profile='commander_review')
        blocks=[b for r in ir.material_residuals for b in r.blockers];self.assertTrue(any('status:'+CAP+':blocked' in b for b in blocks),blocks)
        self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        method='test_six_originals_have_complete_normal_registration_and_independent_group_minima'
        with patch('quorune.compiler.kicked_targets.kicked_target_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_jilt_ordinary_and_paid_have_distinct_required_roles_and_real_results'
        from quorune.rules.casting.kicked_targets import bind_kicked_targets
        def always_paid(program,option):
            result=bind_kicked_targets(program,option)
            if 'target_schema' in result:
                result=deepcopy(result)
                for group in result['target_schema']['groups']:group.update(min=1,max=1)
            return result
        with patch('quorune.rules.casting.kicked_targets.bind_kicked_targets',side_effect=always_paid):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
