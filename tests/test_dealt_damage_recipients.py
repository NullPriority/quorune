"""CR120/603.7: the damaged creature and damage source have distinct roles."""
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch
from common import ROOT
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class DealtDamageRecipientTests(unittest.TestCase):
    def test_self_damage_head_does_not_follow_a_returned_source_with_the_same_reference(self):
        from quorune.trigger_discovery import semantic_event_condition_matches
        registry=load_default_capability_registry()
        ir=compile_oracle_card(record('Whenever this creature deals damage to a creature, destroy that creature at end of combat.','Creature — Snake'),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        source=SimpleNamespace(ref='source',object_id='source-id',logical_object_id='source@1',controller='A',owner='A')
        context={'source':'source','source_logical_object_id':'source@0','target_kind':'permanent','target_types':['creature'],'amount':1}
        host=SimpleNamespace(state=SimpleNamespace(active_player='A'))
        self.assertFalse(semantic_event_condition_matches(host,ir.faces[0].nodes[0].event_condition,source=source,context=context))
        source.logical_object_id='source@0'
        self.assertTrue(semantic_event_condition_matches(host,ir.faces[0].nodes[0].event_condition,source=source,context=context))

    def test_damage_permanent_heads_preserve_source_scope_combat_and_recipient(self):
        registry=load_default_capability_registry()
        for head,combat in (('this creature deals combat damage to a creature',True),
                ('this creature deals damage to a creature',False),('a Warrior you control deals combat damage to a creature',True),
                ('a creature you control deals damage to a creature',False)):
            with self.subTest(head=head):
                ir=compile_oracle_card(record('Whenever '+head+', destroy that creature at end of combat.','Creature — Snake'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual('damage.dealt',node.event);self.assertIsNone(node.target_schema)
                self.assertEqual('$event.damaged_permanent',node.effects[0]['object'])
                terms=node.event_condition['all']
                self.assertIn({'field':'target_kind','op':'eq','value':'permanent'},terms)
                self.assertIn({'field':'target_types','op':'contains_any','value':['creature']},terms)
                self.assertEqual(combat,{'field':'combat','op':'truthy','value':True} in terms)
                source_terms=terms[0].get('all',[terms[0]])
                if head.startswith('a '):
                    self.assertIn({'field':'source_controller','op':'eq','value':'$source.controller'},source_terms)
                    self.assertIn({'field':'source_subtypes' if head.startswith('a Warrior') else 'source_types',
                        'op':'contains_any','value':['warrior' if head.startswith('a Warrior') else 'creature']},source_terms)
                else:
                    self.assertIn({'field':'source','op':'eq','value':'$source.ref'},source_terms)
                    self.assertIn({'field':'source_logical_object_id','op':'eq','value':'$source.logical_object_id'},source_terms)

    def test_unrepresented_damage_source_groups_and_linked_recipient_bodies_stay_residual(self):
        registry=load_default_capability_registry()
        for text in ('Whenever one or more creatures you control deal damage to one or more creatures, destroy those creatures at end of combat.',
                'Whenever another creature deals damage to a creature, destroy that creature at end of combat.',
                'Whenever this creature deals damage to a creature with a counter on it, destroy that creature at end of combat.',
                'Whenever this creature deals damage to a creature, destroy that creature at end of combat. Draw a card.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature — Snake'),capability_registry=registry).status)

    def test_recipient_capture_uses_the_damaged_creature_and_rejects_bad_public_facts(self):
        from quorune import damage_recipient_model as owner
        from quorune.delayed_object_actions import bind_delayed_object_instruction
        from quorune.event_counter_reference import resolve_sealed_event_object_value
        context={'source':'source','source_object_id':'source-id','source_logical_object_id':'source@0',
            'target':'recipient','target_object_id':'recipient-id','target_logical_object_id':'recipient@0',
            'target_kind':'permanent','target_types':['creature'],'amount':1}
        captured=owner.capture_damaged_permanent('damage.dealt',context)
        self.assertEqual({'reference':'recipient','logical_object_id':'recipient@0'},captured[owner.DAMAGE_PERMANENT_CONTEXT])
        card=SimpleNamespace(ref='recipient',logical_object_id='recipient@0',zone='battlefield',phased_out=False)
        host=SimpleNamespace(state=SimpleNamespace(cards={'recipient-id':card}),_stack_source_ref=lambda item:'source')
        item=SimpleNamespace(context={**captured,'source_logical_object_id':'source@0'})
        self.assertEqual((True,'recipient'),resolve_sealed_event_object_value(host,item,owner.DAMAGE_PERMANENT_OBJECT))
        card.logical_object_id='recipient@1'
        self.assertEqual((True,None),resolve_sealed_event_object_value(host,item,owner.DAMAGE_PERMANENT_OBJECT))
        effect={'op':'schedule_fixed_object_action','schema_version':1,'object':owner.DAMAGE_PERMANENT_OBJECT,
            'action':'destroy','timing':'end_of_combat'}
        self.assertEqual({'reference':'recipient','logical_object_id':'recipient@0'},bind_delayed_object_instruction(host,effect,item)['object'])
        for change in ({'target_kind':'player'},{'target_types':['land']},{'target_logical_object_id':None},
                {'target_object_id':''},{'target':''},{'amount':0},{'amount':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):owner.capture_damaged_permanent('damage.dealt',{**context,**change})
        with self.assertRaises(ValueError):owner.capture_damaged_permanent('damage.received',context)

    def test_runtime_recipient_binding_rejects_wrong_event_unproved_subjects_and_unowned_bodies(self):
        from quorune.rules.damage_permanent_recipients import damage_permanent_runtime_dependencies
        conditions=[{'field':'source','op':'eq','value':'$source.ref'},
            {'field':'source_logical_object_id','op':'eq','value':'$source.logical_object_id'},
            {'field':'target_kind','op':'eq','value':'permanent'},
            {'field':'target_types','op':'contains_any','value':['creature']}]
        effect={'op':'schedule_fixed_object_action','schema_version':1,'object':'$event.damaged_permanent',
            'action':'destroy','timing':'end_of_combat'}
        program=SimpleNamespace(event='damage.dealt',active_zone='battlefield',target_schema=None,
            effects=(effect,),coverage=('fixed-delayed-object-action',),event_condition={'all':conditions})
        self.assertIn('trigger.event.damage_permanent_recipient',damage_permanent_runtime_dependencies(program))
        for change in ({'event':'damage.received'},{'active_zone':'hand'},{'target_schema':{'count':1}},
                {'event_condition':{'all':conditions[1:]}},{'event_condition':{'all':conditions[:2]}},
                {'event_condition':{'any':[{'all':conditions},conditions[0]]}},
                {'event_condition':{'all':[{'field':'source_controller','op':'eq','value':'$source.controller'},*conditions[1:]]}},
                {'effects':({'op':'execute','card':'$event.damaged_permanent'},)},{'coverage':()}):
            with self.subTest(change=change),self.assertRaises(ValueError):damage_permanent_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))

    def test_verified_recipient_capability_withdrawal_keeps_existing_player_damage(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='trigger.event.damage_permanent_recipient').update(status='blocked',blockers=['recipient authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record('Whenever this creature deals combat damage to a creature, destroy that creature at end of combat.','Creature — Snake'),capability_registry=registry).status)
        old=compile_oracle_card(record('Whenever this creature deals combat damage to a player, draw a card.','Creature — Snake'),capability_registry=registry)
        self.assertEqual('exact',old.status,old.material_residuals)

    def test_substituting_the_damage_source_for_the_recipient_is_killed(self):
        from quorune import damage_recipient_model as owner
        original=owner.capture_damaged_permanent
        def swapped(event,context):
            value=original(event,context)
            value[owner.DAMAGE_PERMANENT_CONTEXT]={'reference':context['source'],'logical_object_id':context['source_logical_object_id']}
            return value
        with patch.object(owner,'capture_damaged_permanent',side_effect=swapped):
            result=unittest.TestResult();DealtDamageRecipientTests('test_recipient_capture_uses_the_damaged_creature_and_rejects_bad_public_facts').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)
