from __future__ import annotations

"""CR702.151: two paid actions and an attachment-bound creature-type effect."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.ability_fragments import WardSpec
from quorune.continuous_effect_model import ContinuousEffect, ContinuousOperation, Layer
from quorune.continuous_effect_state import ResolutionEffectSource, create_resolution_continuous_effect, active_resolution_effects
from scripts.build_test_database import build_fixture_database
import test_turn_count_self_costs as controls
from quorune.rules.evidence_storage import read_evidence_document


class ReconfigureTests(unittest.TestCase):
    def test_resolve_components_do_not_require_activation_catalog_fields(self):
        from test_bound_effect_programs import record
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        source=record('Target creature gets +1/+0 until end of turn. Draw a card.','Instant')
        program=compile_best_available_card_program(self.db,source,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

    register_fixture_cards_on_add=True
    session=controls.TurnCountSelfCostTests.session
    add=controls.TurnCountSelfCostTests.add
    prepare=controls.TurnCountSelfCostTests.prepare
    checkpoint=controls.TurnCountSelfCostTests.checkpoint
    cast=controls.TurnCountSelfCostTests.cast
    resolve=controls.TurnCountSelfCostTests.resolve
    replay=controls.TurnCountSelfCostTests.replay
    binding=controls.TurnCountSelfCostTests.binding
    actions=controls.TurnCountSelfCostTests.actions
    priority=controls.TurnCountSelfCostTests.priority
    private_replay=controls.TurnCountSelfCostTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'reconfigure.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/reconfigure-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Two Reconfigure actions',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def reconfigure_action(self,session,source,mode):
        return next((row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':')
            and row['id'].endswith('-reconfigure-'+mode)),None)

    def activate_reconfigure(self,session,source,mode,target=None):
        action=self.reconfigure_action(session,source,mode);self.assertIsNotNone(action)
        command={'action_id':action['id'],'pay':'auto'}
        if target is not None:command['targets']=[target.ref]
        result=session.act('pilot:'+source.controller,command);self.assertTrue(result.ok,result.summary)

    def is_creature(self,session,source):
        return 'creature' in session.engine._type_parts(session.engine._effective_card_data(
            session.state.cards[source.object_id])['type_line'])[0]

    def test_actual_rabbit_attach_and_unattach_pay_separately_and_preserve_tapped_state(self):
        session=self.session(746100);source=self.add(session.engine,'Rabbit Battery')
        source.tapped=True
        target=self.add(session.engine,'Grizzly Bears')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':2});self.checkpoint(session)
        action=self.reconfigure_action(session,source,'attach');self.assertIsNotNone(action)
        self.assertIsNone(self.reconfigure_action(session,source,'unattach'))
        command={'action_id':action['id'],'targets':[target.ref],'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'targets':[source.ref]}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        attached=session.state.cards[source.object_id]
        self.assertEqual(target.object_id,attached.attached_to)
        self.assertTrue(attached.tapped)
        self.assertNotIn('creature',session.engine._type_parts(session.engine._effective_card_data(attached)['type_line'])[0])
        self.assertEqual((3,3),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.assertIn('haste',session.engine._combat_keywords(session.state.cards[target.object_id]))
        action=self.reconfigure_action(session,attached,'unattach');self.assertIsNotNone(action)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['A'].mana_pool['R'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        detached=session.state.cards[source.object_id]
        self.assertIsNone(detached.attached_to);self.assertTrue(detached.tapped)
        self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(detached)['type_line'])[0])
        self.assertEqual((2,2),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.assertNotIn('haste',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.private_replay(session,private)

    def test_fresh_reconfigure_producer_and_type_consequence_mutants_are_killed(self):
        method='test_actual_rabbit_attach_and_unattach_pay_separately_and_preserve_tapped_state'
        with patch('quorune.compiler.reconfigure_nodes.reconfigure_nodes',return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()
        with patch('quorune.effect_runtime.zones_and_attachments.commit_reconfigure_attachment_effect'):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()
        with patch('quorune.attachment_duration.AttachmentDuration.is_current',return_value=True):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()

    def test_seven_originals_register_two_complete_actions_and_apply_all_characteristics(self):
        cases=(('Armguard Familiar',4,(4,3),'ward'),('Blade of the Oni',4,(5,5),'menace'),
            ('Bronzeplate Boar',5,(5,4),'trample'),('Leech Gauntlet',4,(2,2),'lifelink'),
            ('Lizard Blades',2,(2,2),'double strike'),('Rabbit Battery',1,(3,3),'haste'),
            ('Webspinner Cuff',4,(3,6),'reach'))
        for index,(name,cost,stats,keyword) in enumerate(cases):
            with self.subTest(name=name):
                session=self.session(746210+index);source=self.add(session.engine,name)
                target=self.add(session.engine,'Grizzly Bears')
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='activate']
                self.assertEqual(2,len(programs));self.assertTrue(all(p.effects and p.handlers for p in programs))
                self.prepare(session,mana={'B':4,'R':2,'C':20})
                before=sum(session.state.players['A'].mana_pool.values())
                self.activate_reconfigure(session,source,'attach',target);self.assertIsNone(self.resolve(session))
                self.assertEqual(before-cost,sum(session.state.players['A'].mana_pool.values()))
                self.assertFalse(self.is_creature(session,source));self.assertEqual(target.object_id,source.attached_to)
                self.assertEqual(stats,(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
                if keyword=='ward':
                    self.assertTrue(any(isinstance(f,WardSpec) for f in session.engine._effective_ability_fragments(target)))
                else:self.assertIn(keyword,session.engine._combat_keywords(target))
                if name=='Blade of the Oni':
                    data=session.engine._effective_card_data(target)
                    self.assertIn('B',data['colors']);self.assertIn('demon',session.engine._type_parts(data['type_line'])[1])
                self.activate_reconfigure(session,source,'unattach');self.assertIsNone(self.resolve(session))
                self.assertTrue(self.is_creature(session,source));self.assertIsNone(source.attached_to)
                self.assertEqual((2,2),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))

    def test_actual_retarget_same_target_payment_and_stale_payment_or_timing_are_atomic(self):
        session=self.session(746230);source=self.add(session.engine,'Rabbit Battery')
        first=self.add(session.engine,'Grizzly Bears');other=self.add(session.engine,'Grizzly Bears',ref='second-receiver')
        enemy=self.add(session.engine,'Grizzly Bears',seat='B',ref='foreign-receiver')
        self.prepare(session,mana={'R':4});self.checkpoint(session)
        action=self.reconfigure_action(session,source,'attach');self.assertIsNotNone(action)
        for command in ({'targets':[enemy.ref],'pay':'auto'}, {'targets':[first.ref],'pay':'manual','payment':{}},
                {'action_id':'activate:'+source.ref+':ab3-reconfigure-unattach','pay':'auto'}):
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':action['id'],**command})
            self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.activate_reconfigure(session,source,'attach',first);self.assertIsNone(self.resolve(session))
        timestamp=source.zone_timestamp;count=len(session.state.continuous_effects)
        attach_events=sum(e.code=='attachment.attach' for e in session.state.events)
        self.activate_reconfigure(session,source,'attach',first);self.assertIsNone(self.resolve(session))
        self.assertEqual(timestamp,source.zone_timestamp);self.assertEqual(count,len(session.state.continuous_effects))
        self.assertEqual(attach_events,sum(e.code=='attachment.attach' for e in session.state.events))
        self.assertEqual(2,session.state.players['A'].mana_pool['R'])
        self.activate_reconfigure(session,source,'attach',other);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertNotIn(source.object_id,session.state.cards[first.object_id].attachments)
        self.assertIn(source.object_id,session.state.cards[other.object_id].attachments)
        self.assertGreater(source.zone_timestamp,timestamp)
        self.assertEqual(1,len(active_resolution_effects(session.state,source)))
        self.assertEqual((2,3),(session.engine._numeric_stat(first.object_id,'power'),session.engine._numeric_stat(other.object_id,'power')))
        self.replay(session)

    def test_loss_of_abilities_keeps_type_effect_until_native_receiver_departure(self):
        session=self.session(746240);source=self.add(session.engine,'Rabbit Battery')
        target=self.add(session.engine,'Grizzly Bears');bounce=self.add(session.engine,'Boomerang',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'U':2});self.activate_reconfigure(session,source,'attach',target)
        self.assertIsNone(self.resolve(session))
        create_resolution_continuous_effect(session.engine,source=ResolutionEffectSource('loss-control'),targets=(source,),
            layer=Layer.ABILITY,sublayer='6',operations=(ContinuousOperation('remove_all_abilities'),))
        self.prepare(session);self.checkpoint(session)
        self.assertFalse(session.engine._activated_abilities(source))
        self.assertFalse(self.is_creature(session,source));self.assertEqual(target.object_id,source.attached_to)
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'))
        self.cast(session,'A',bounce,[target.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertTrue(self.is_creature(session,source));self.assertIsNone(source.attached_to)
        self.private_replay(session,private)

    def test_later_native_animation_detaches_even_while_reconfigure_is_present(self):
        session=self.session(746250);source=self.add(session.engine,'Rabbit Battery')
        target=self.add(session.engine,'Grizzly Bears');animation=self.add(session.engine,'Ensoul Artifact',zone='hand')
        self.prepare(session,mana={'R':1,'U':1,'C':1});self.checkpoint(session)
        self.activate_reconfigure(session,source,'attach',target);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',animation,[source.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertTrue(self.is_creature(session,source));self.assertIsNone(source.attached_to)
        self.assertEqual(5,session.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'))
        self.assertTrue(any(ability.reconfigure for ability in session.engine._activated_abilities(source)))
        self.replay(session)

    def test_attachment_duration_codec_is_closed_and_cannot_revive_old_relation(self):
        session=self.session(746260);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
        self.prepare(session,mana={'R':2});self.activate_reconfigure(session,source,'attach',target)
        self.assertIsNone(self.resolve(session));effect,=active_resolution_effects(session.state,source)
        self.assertEqual(effect,ContinuousEffect.from_dict(effect.to_dict()))
        facts=effect.attachment_duration;self.assertTrue(facts.is_current(session.state.cards))
        self.activate_reconfigure(session,source,'unattach');self.assertIsNone(self.resolve(session))
        self.assertFalse(facts.is_current(session.state.cards))
        self.prepare(session,mana={'R':1});self.activate_reconfigure(session,source,'attach',target)
        self.assertIsNone(self.resolve(session));self.assertFalse(facts.is_current(session.state.cards))
        self.assertEqual(1,len(active_resolution_effects(session.state,source)))
        raw=effect.to_dict()
        for value in ({**raw,'attachment_duration':None},{**raw,'attachment_duration':{**facts.to_dict(),'attachment_timestamp':True}},
                {**raw,'duration':'until_end_of_turn'},{**raw,'source_id':'wrong-source'}):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                ContinuousEffect.from_dict(value)

    def test_capability_withdrawals_and_adjacent_cost_grammar_stay_residual(self):
        from dataclasses import replace
        from test_bound_effect_programs import record
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.card_programs.binding import bind_semantic_program_runtime
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        def verified_registry(raw):
            index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=discovered)
            registry=CapabilityRegistry(raw)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            return registry
        baseline=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Rabbit Battery'),
            capability_registry=verified_registry(baseline),capability_profile='commander_review').status)
        for cap in ('attachment.reconfigure.fixed_mana','attachment.action.fixed_source','target.revalidate_resolution'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn attachment authority'])
            ir=compile_oracle_card(self.db.lookup('Rabbit Battery'),capability_registry=verified_registry(raw),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
            blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)
        session=self.session(746265);source=self.add(session.engine,'Rabbit Battery')
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if program.event!='activate':continue
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            missing=replace(program,capability_dependencies=(),capability_closure=None)
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:attachment.reconfigure.fixed_mana',bound['blockers'])
        for text in ('Reconfigure {X}','Reconfigure {W/U}','Reconfigure {S}','Reconfigure—Pay 2 life.',
                'Reconfigure {2}. Activate only once each turn.'):
            ir=compile_oracle_card(replace(record(text,'Artifact Creature — Equipment Beast'),keywords=['Reconfigure']),
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_generic_attachment_uses_reconfigure_but_rejects_self_and_ordinary_creature_equipment(self):
        session=self.session(746270);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
        ordinary=self.add(session.engine,'Bonesplitter')
        create_resolution_continuous_effect(session.engine,source=ResolutionEffectSource('ordinary-animation'),targets=(ordinary,),
            layer=Layer.TYPE,sublayer='4',operations=(ContinuousOperation('add_types',['Creature']),))
        timestamp=ordinary.zone_timestamp
        session.engine.apply_effect({'op':'attach','attachment_kind':'equipment','source':ordinary.ref,'target':target.ref},actor='A')
        self.assertIsNone(ordinary.attached_to);self.assertEqual(timestamp,ordinary.zone_timestamp)
        session.engine.apply_effect({'op':'attach','attachment_kind':'equipment','source':source.ref,'target':source.ref},actor='A')
        self.assertIsNone(source.attached_to)
        session.engine.apply_effect({'op':'attach','attachment_kind':'equipment','source':source.ref,'target':target.ref},actor='A')
        self.assertFalse(self.is_creature(session,source));self.assertEqual(target.object_id,source.attached_to)
        self.prepare(session,mana={'R':1});self.checkpoint(session)
        self.activate_reconfigure(session,source,'unattach');session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertTrue(self.is_creature(session,source));self.replay(session)

    def test_control_phasing_and_new_incarnations_preserve_only_the_current_relation(self):
        session=self.session(746280);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
        self.prepare(session,mana={'R':1});self.activate_reconfigure(session,source,'attach',target);self.assertIsNone(self.resolve(session))
        effect,=active_resolution_effects(session.state,source)
        session.engine.change_control(source.object_id,'B');session.engine.change_control(target.object_id,'D')
        self.assertTrue(effect.attachment_duration.is_current(session.state.cards));self.assertFalse(self.is_creature(session,source))
        source.phased_out=True;target.phased_out=True
        self.assertTrue(effect.attachment_duration.is_current(session.state.cards))
        source.phased_out=False;target.phased_out=False
        self.assertFalse(self.is_creature(session,source));self.assertEqual(target.object_id,source.attached_to)
        prior=target.logical_object_id
        session.engine.move_card(target.object_id,'exile',log=False)
        self.assertIsNone(source.attached_to);self.assertTrue(self.is_creature(session,source))
        session.engine.move_card(target.object_id,'battlefield',controller='D',log=False)
        self.assertNotEqual(prior,target.logical_object_id);self.assertFalse(effect.attachment_duration.is_current(session.state.cards))
        self.assertNotIn(source.object_id,target.attachments)
        prior_source=source.logical_object_id
        session.engine.move_card(source.object_id,'hand',log=False);session.engine.move_card(source.object_id,'battlefield',controller='B',log=False)
        self.assertNotEqual(prior_source,source.logical_object_id);self.assertTrue(self.is_creature(session,source))

    def test_sorcery_timing_and_a_target_incarnation_are_revalidated(self):
        session=self.session(746290);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
        bounce=self.add(session.engine,'Boomerang',zone='hand');self.prepare(session,mana={'R':1,'U':2})
        action=self.reconfigure_action(session,source,'attach');self.assertIsNotNone(action)
        session.state.phase='combat';session.state.step='beginning_combat';session.engine.permissions.invalidate_current()
        session.engine._grant_priority('A');session.engine.pump();self.assertIsNone(self.reconfigure_action(session,source,'attach'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.prepare(session);self.checkpoint(session)
        self.activate_reconfigure(session,source,'attach',target)
        self.priority(session,'A');self.cast(session,'A',bounce,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertTrue(self.is_creature(session,source));self.assertEqual(0,session.state.players['A'].mana_pool['R'])
        self.replay(session)

    def test_native_ability_loss_in_response_prevents_a_creature_equipment_attachment(self):
        session=self.session(746300);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
        humble=self.add(session.engine,'Humble',zone='hand');self.prepare(session,mana={'R':1,'W':1,'C':1})
        self.checkpoint(session);self.activate_reconfigure(session,source,'attach',target)
        self.priority(session,'A');self.cast(session,'A',humble,[source.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertIsNone(source.attached_to);self.assertTrue(self.is_creature(session,source))
        self.assertFalse(session.engine._activated_abilities(source))
        self.assertEqual((0,1),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'));self.replay(session)

    def test_native_reconfigure_detaches_equipment_previously_equipping_its_source(self):
        session=self.session(746310);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
        equipment=self.add(session.engine,'Bonesplitter');self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+equipment.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'manual','payment':{'C':1}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(source.object_id,equipment.attached_to)
        self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
        self.activate_reconfigure(session,source,'attach',target);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertIsNone(session.state.cards[equipment.object_id].attached_to);self.assertFalse(source.attachments)
        self.assertFalse(self.is_creature(session,source));self.assertEqual(3,session.engine._numeric_stat(target.object_id,'power'))
        self.replay(session)

    def test_native_animated_ordinary_equipment_can_activate_but_cannot_attach(self):
        session=self.session(746320);source=self.add(session.engine,'Bonesplitter');target=self.add(session.engine,'Grizzly Bears')
        animation=self.add(session.engine,'Ensoul Artifact',zone='hand');self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        self.cast(session,'A',animation,[source.ref]);self.assertIsNone(self.resolve(session))
        timestamp=source.zone_timestamp
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));source=session.state.cards[source.object_id]
        self.assertIsNone(source.attached_to);self.assertEqual(timestamp,source.zone_timestamp)
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'));self.replay(session)

    def test_reconfigure_catalog_requires_complete_admission_and_closed_activation_facts(self):
        from dataclasses import replace
        from quorune.abilities import ActivatedAbility
        from quorune.compiler.reconfigure_nodes import is_closed_reconfigure_program
        from quorune.semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.rules.attachment_action_capability_shapes import reconfigure_node_capabilities
        session=self.session(746330);source=self.add(session.engine,'Rabbit Battery')
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if program.event!='activate':continue
            self.assertTrue(is_closed_reconfigure_program(program))
            descriptor=dict(program.handlers[0]);descriptor.pop('requires_complete_card_program')
            with self.assertRaises(SemanticNodeError):ActivatedAbilityCatalogHandler().validate(descriptor)
            ability=ActivatedAbilityCatalogHandler().validate(program.handlers[0]);wire=ability.to_dict()
            self.assertEqual(ability,ActivatedAbility.from_dict(wire))
            with self.assertRaises(ValueError):ActivatedAbility.from_dict({**wire,'reconfigure':False})
            changed=replace(ability,sorcery_speed=False)
            altered=replace(program,handlers=[{**dict(program.handlers[0]),'ability':changed.to_dict()}])
            self.assertFalse(is_closed_reconfigure_program(altered))
            if ability.activation_conditions:
                changed=replace(ability,activation_conditions=())
                altered=replace(program,handlers=[{**dict(program.handlers[0]),'ability':changed.to_dict()}])
                self.assertFalse(is_closed_reconfigure_program(altered))
            if program.target_schema is not None:
                for schema in ({**program.target_schema,'count':True},{**program.target_schema,'source_exclusion':False}):
                    self.assertFalse(reconfigure_node_capabilities(effects=program.effects,target_schema=schema))

    def test_typed_keyword_removal_withdraws_both_actions_without_ending_an_existing_type_effect(self):
        for attached in (False,True):
            with self.subTest(attached=attached):
                session=self.session(746340+attached);source=self.add(session.engine,'Rabbit Battery');target=self.add(session.engine,'Grizzly Bears')
                self.prepare(session,mana={'R':2})
                if attached:
                    self.activate_reconfigure(session,source,'attach',target);self.assertIsNone(self.resolve(session))
                create_resolution_continuous_effect(session.engine,source=ResolutionEffectSource('keyword-loss-control'),targets=(source,),
                    layer=Layer.ABILITY,sublayer='6',operations=(ContinuousOperation('remove_ability','Reconfigure'),))
                self.prepare(session)
                self.assertFalse(any(a.reconfigure for a in session.engine._activated_abilities(source)))
                self.assertIsNone(self.reconfigure_action(session,source,'attach'));self.assertIsNone(self.reconfigure_action(session,source,'unattach'))
                self.assertEqual(not attached,self.is_creature(session,source))
                self.assertEqual(3 if attached else 2,session.engine._numeric_stat(target.object_id,'power'))
