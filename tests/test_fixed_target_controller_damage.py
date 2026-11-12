"""Pinned CR120.4/608.2b: one target and its controller-side damage share."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_dual_damage_templates import fixed_dual_damage_template
from quorune.deck import DeckDefinition,DeckEntry
from quorune.fixed_dual_damage_model import (FixedTargetControllerDamageSpec,FIXED_DUAL_DAMAGE_FIELD as FIELD,
    FIXED_DUAL_DAMAGE_CAPABILITY as CAPABILITY,FIXED_DUAL_DAMAGE_MECHANIC as MECHANIC)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_dual_damage import (bound_fixed_dual_damage_recipients,
    bind_fixed_dual_damage,fixed_dual_damage_node_capabilities,fixed_dual_damage_payload_dependencies)
from quorune.semantic_runtime import default_semantic_interpreter
from scripts.build_test_database import build_fixture_database


class FixedDualDamageCompilerTests(unittest.TestCase):
    def test_single_instruction_composes_existing_target_authority_and_fixed_amounts(self):
        registry = load_default_capability_registry()
        for text,kind in (('Generic Query Observer deals 4 damage to any target and 2 damage to you.','Instant'),
                ('{T}: This creature deals 2 damage to any target and 3 damage to you.','Creature'),
                ('When this artifact enters, it deals 4 damage to any target and 3 damage to you.','Artifact')):
            ir = compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node = ir.faces[0].nodes[0];self.assertEqual(1,len(node.effects))
            effect = node.effects[0];spec = FixedTargetControllerDamageSpec.from_dict(effect[FIELD])
            self.assertEqual(spec.total,effect['amount']);self.assertEqual('damage_fixed_set',effect['op'])
            self.assertIn(CAPABILITY,node.capability_dependencies)
            self.assertIn('damage.batch.fixed_set',node.capability_dependencies)
            self.assertEqual(1,node.target_schema['count'])

    def test_bound_controller_collision_is_one_combined_damage_event(self):
        spec = FixedTargetControllerDamageSpec(4,2)
        value = {**spec.to_dict(),'recipients':[{'ref':'A','logical_object_id':None,'amount':6}]}
        effect = {'op':'damage_fixed_set','source':'spell-ref','amount':6,FIELD:value}
        plan = default_semantic_interpreter().lower_for_seats(effect,actor='A',default_reason='fixed dual damage',
            seats=('A','B','C','D'),active_seats=('A','B','C','D'),apnap_order=('A','B','C','D'))
        intent, = plan.intents
        self.assertIsNone(intent.spec);self.assertEqual((('A',6),),tuple((row.ref,row.amount) for row in intent.allocated_recipients))
        retained = {**effect,'_replacement_selections':[]}
        item = SimpleNamespace(controller='A',targets=['A'],context={'targets_revalidated':True})
        self.assertEqual(value,bind_fixed_dual_damage(None,retained,item),'a pending continuation retains its sealed recipients without querying again')
        for targets in (['D'],[],[None]):
            with self.assertRaises(ValueError):
                bind_fixed_dual_damage(None,retained,SimpleNamespace(controller='A',targets=targets,context=item.context))
        for bad in ({'schema_version':True},{'target_amount':True},{'controller_amount':0},{'extra':1}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):FixedTargetControllerDamageSpec.from_dict({**spec.to_dict(),**bad})
        for rows in ([{'ref':'B','logical_object_id':None,'amount':6}],
                [{'ref':'D','logical_object_id':None,'amount':4},{'ref':'B','logical_object_id':None,'amount':2}],
                [{'ref':'A','logical_object_id':None,'amount':4},{'ref':'A','logical_object_id':None,'amount':2}]):
            with self.assertRaises(ValueError):bound_fixed_dual_damage_recipients({**spec.to_dict(),'recipients':rows},actor='A',amount=6)
        with self.assertRaises(ValueError):
            default_semantic_interpreter().lower_for_seats({**effect,'groups':[]},actor='A',default_reason='mixed',
                seats=('A','B'),active_seats=('A','B'),apnap_order=('A','B'))

    def test_unbound_source_variable_amounts_unknown_riders_and_malformed_descriptors_reject(self):
        for text in ('It deals 4 damage to any target and 2 damage to you.',
                'Generic Query Observer deals X damage to any target and 2 damage to you.',
                'Generic Query Observer deals 4 damage to each opponent and 2 damage to you.',
                'Generic Query Observer deals 4 damage to any target and X damage to you.',
                'Generic Query Observer deals 4 damage to any target and 2 damage to you. Rewrite history.'):
            self.assertIsNone(fixed_dual_damage_template(text,card_name='Generic Query Observer',source_is_permanent=False))
        template = fixed_dual_damage_template('Generic Query Observer deals 4 damage to any target and 2 damage to you.',
            card_name='Generic Query Observer',source_is_permanent=False)
        _,effects,schema,mechanics = template.compiled()
        for changed in ({'amount':True},{'amount':4},{'source':'$controller'},{'groups':[]}):
            altered = {**effects[0],**changed}
            self.assertEqual((),fixed_dual_damage_node_capabilities(effects=(altered,),target_schema=schema,mechanic_ids=mechanics))
            with self.assertRaises(ValueError):fixed_dual_damage_payload_dependencies(altered,mechanics=mechanics)
        with self.assertRaises(ValueError):fixed_dual_damage_payload_dependencies(effects[0],mechanics=tuple(m for m in mechanics if m!=MECHANIC))

    def test_verified_dual_owner_withdrawal_keeps_plain_damage_available(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked',blockers=['dual owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Generic Query Observer deals 4 damage to any target and 2 damage to you.',False),
                ('Generic Query Observer deals 4 damage to any target.',True),('Draw a card.',True)):
            self.assertEqual(expected,compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry).status=='exact')


class FixedDualDamageNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name)/'fixed-dual-damage.sqlite3'
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings'] = []
        basic = Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/fixed-target-controller-damage-cards.json'],path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed dual damage',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def private_finish(self,session,private):
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_char_self_target_is_one_six_damage_event_and_illegal_target_prevents_self_damage(self):
        import quorune.fixed_damage_set as owner
        original = owner.resolve_damage_batch
        for invalid in (False,True):
            with self.subTest(invalid=invalid):
                session = self.session(994100+invalid);source = self.add(session.engine,'Char',zone='hand')
                target = self.add(session.engine,'Grizzly Bears',seat='B')
                bounce = self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='char-dual-private')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session)
                self.cast(session,'A',source,targets=[target.ref if invalid else 'A'])
                if invalid:self.cast(session,'B',bounce,targets=[target.ref]);self.resolve_top(session)
                session = self.replay(session,load=True);observed = []
                def capture(host,proposals,**kwargs):
                    observed.append(tuple((p.recipient.ref,p.amount) for p in proposals))
                    return original(host,proposals,**kwargs)
                with patch.object(owner,'resolve_damage_batch',capture):self.assertIsNone(self.resolve(session))
                self.assertEqual([] if invalid else [(('A',6),)],observed)
                self.assertEqual(40 if invalid else 34,session.state.players['A'].life)
                self.assertEqual(40,session.state.players['B'].life);self.private_finish(session,private)

    def borrowed_artillery_witness(self,seed):
        session = self.session(seed);source = self.add(session.engine,'Goblin Artillery');source.owner = 'B';source.acquired_control_turn_count = -1
        bounce = self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='artillery-dual-private')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.priority(session,'A')
        action = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+source.ref+':'))
        command = {'action_id':action['id'],'targets':['D'],'pay':'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result = session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped)
        self.cast(session,'B',bounce,targets=[source.ref]);self.resolve_top(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone);self.assertIn(source.object_id,session.state.players['B'].zones['hand'])
        session = self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['A'].life,'ability controller damage must survive source departure')
        self.assertEqual(38,session.state.players['D'].life);self.assertEqual(40,session.state.players['B'].life)
        self.private_finish(session,private)

    def test_actual_borrowed_artillery_damage_uses_ability_controller_after_source_bounce(self):
        self.borrowed_artillery_witness(994110)

    def test_actual_cannonade_deals_both_amounts_then_draws_once(self):
        session = self.session(994120);source = self.add(session.engine,'Orcish Cannonade',zone='hand')
        target = self.add(session.engine,'Grizzly Bears',seat='B')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='cannonade-dual-private')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':2});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);hand = len(session.state.players['A'].zones['hand'])
        session = self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual(37,session.state.players['A'].life)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']));self.private_finish(session,private)

    def test_actual_pizza_entry_target_choice_deals_to_target_and_controller(self):
        session = self.session(994130);source = self.add(session.engine,'Spicy Oatmeal Pizza',zone='hand')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='pizza-dual-private')
        self.assert_closed(session,source);self.prepare(session,mana={'C':20,'R':5,'G':5,'W':5,'U':5,'B':5});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.target',self.resolve(session).kind)
        session = self.replay(session,load=True);self.target(session,'A',['D']);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual(36,session.state.players['D'].life);self.assertEqual(37,session.state.players['A'].life)
        self.private_finish(session,private)

    def test_actual_competing_replacements_remain_precommit_and_replay_for_both_recipients(self):
        session = self.session(994140);source = self.add(session.engine,'Char',zone='hand')
        first = self.add(session.engine,'Furnace of Rath',ref='first-dual-furnace')
        second = self.add(session.engine,'Furnace of Rath',seat='C',ref='second-dual-furnace')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='replacement-dual-private')
        self.assert_closed(session,source)
        for card in (first,second):
            programs = [program for program in session.engine.semantics.programs_for_oracle(card.oracle_id) if program.handlers]
            self.assertTrue(programs)
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(program) for program in programs),
                'replacement controls use their actual reviewed trust basis, without a whole-card closure claim')
        self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session);self.cast(session,'A',source,targets=['D'])
        self.assertEqual('replacement.order',self.resolve(session).kind)
        for actor in ('A','D'):
            self.assertEqual([actor],session.state.pending_decision.actors)
            self.assertEqual((40,40),(session.state.players['A'].life,session.state.players['D'].life),'damage batch must remain entirely precommit')
            packet = session.packet('pilot:'+actor,full=True);choice = packet['decision']['ctx']['options'][0]['id']
            before = authoritative_state_hash(session.state)
            for principal,selection in (('B',choice),(actor,'not-an-option')):
                self.assertFalse(session.act('pilot:'+principal,{'action_id':'choose','choices':{'replacement':selection}}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
            session = self.replay(session,load=True)
            result = session.act('pilot:'+actor,{'action_id':'choose','choices':{'replacement':choice}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(32,session.state.players['A'].life);self.assertEqual(24,session.state.players['D'].life)
        self.private_finish(session,private)

    def test_normal_binding_cannot_omit_dual_batch_controller_or_target_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(994150);source = self.add(session.engine,'Goblin Artillery');self.assert_closed(session,source)
        program, = [program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(FIELD in effect for effect in program.effects)]
        for cap in (CAPABILITY,'damage.batch.fixed_set','damage.result.player_life','damage.result.multitype_permanent','damage.source.stack_current_or_lki'):
            dependencies = tuple(value for value in program.capability_dependencies if value!=cap)
            missing = replace(program,capability_dependencies=dependencies,capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_fresh_controller_commit_omission_is_killed_by_actual_borrowed_activation(self):
        import quorune.fixed_damage_set as owner
        original = owner.resolve_damage_batch;observed = []
        def omitted(host,proposals,**kwargs):
            observed.append(True)
            return original(host,tuple(p for p in proposals if p.recipient.ref!='A'),**kwargs)
        with patch.object(owner,'resolve_damage_batch',omitted):
            with self.assertRaisesRegex(AssertionError,'ability controller damage must survive'):
                self.borrowed_artillery_witness(994160)
        self.assertTrue(observed)
