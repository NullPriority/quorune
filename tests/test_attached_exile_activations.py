from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.attachment_references import AttachmentReferenceKind as K, AttachmentReferenceSpec
from quorune.compiler.exile_templates import attached_exile_effect_template
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import targeted_exile_node_capabilities
from test_qualified_zone_event_queries import query_record
from scripts.build_test_database import build_fixture_database
import test_life_gain_prohibitions as native


class AttachedExileActivationCompilerTests(unittest.TestCase):
    def test_attached_exile_requires_the_source_relation_and_versioned_information(self):
        template = attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=K.ENCHANTED)
        self.assertIsNotNone(template)
        identity,effects,schema,mechanics = template.compiled()
        self.assertEqual('exile-source-attachment-v1',identity)
        self.assertIsNone(schema)
        self.assertEqual(('exile',),mechanics)
        reference = AttachmentReferenceSpec(K.ENCHANTED,'permanent',schema_version=2)
        self.assertEqual(({'op':'exile_permanent','card':reference.to_dict()},),effects)
        required = targeted_exile_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics)
        self.assertEqual(('permanent.exile.effect','object.reference.source_attachment_information'),required)
        self.assertNotIn('target.revalidate_resolution',required)
        for relation in (None,K.EQUIPPED,K.FORTIFIED):
            self.assertIsNone(attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=relation))
        for text in ('Exile enchanted permanent.','Exile equipped creature.',
                'You may exile enchanted creature.','Exile enchanted creature until this Aura leaves the battlefield.',
                'Exile enchanted creature. Draw a card.'):
            self.assertIsNone(attached_exile_effect_template(text,source_attachment_relation=K.ENCHANTED))

    def test_attached_exile_reference_and_target_shape_mutants_fail_closed(self):
        effect = attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=K.ENCHANTED).compiled()[1][0]
        for fields in ({'schema_version':1},{'schema_version':True},{'schema_version':2.0},
                {'required_card_type':'creature'},{'relation':'equipped_object'},{'unknown':True}):
            changed = {**effect,'card':{**effect['card'],**fields}}
            self.assertFalse(targeted_exile_node_capabilities(effects=(changed,),target_schema=None,mechanic_ids=('exile',)))
        for effects,schema,mechanics in ((({**effect,'unknown':True},),None,('exile',)),
                ((effect,effect),None,('exile',)),((effect,),{'count':1},('exile',)),
                ((effect,),None,('exile','cr-115-targets')),((effect,),None,())):
            self.assertFalse(targeted_exile_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))

    def test_attached_exile_compiles_a_complete_source_with_its_original_activation_cost(self):
        record = replace(query_record('Enchant creature\n{2}{W}: Exile enchanted creature.'),
            name='Generic Exile Aura',type_line='Enchantment — Aura',power=None,toughness=None,keywords=('Enchant',))
        ir = compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node = next(node for node in ir.faces[0].nodes if node.template_id=='exile-source-attachment-v1')
        self.assertEqual('activate',node.event)
        self.assertIsNone(node.target_schema)
        self.assertEqual({'GENERIC':2,'W':1,'U':0,'B':0,'R':0,'G':0,'C':0},node.cost['mana'])
        self.assertIn('object.reference.source_attachment_information',node.capability_dependencies)

    def test_omitting_attached_exile_lowering_is_detected(self):
        method = 'test_attached_exile_compiles_a_complete_source_with_its_original_activation_cost'
        with patch('quorune.compiler.exile_templates.attached_exile_effect_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_compiled_exile_reference_tracks_current_and_departure_information(self):
        from quorune.model import CardInstance
        from quorune.attachments import attach_objects
        from quorune.attachment_references import ATTACHMENT_INFORMATION_VERSION, capture_source_attachment_snapshot
        from quorune.attachment_information import pin_attachment_information_departures
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.characteristic_evaluation import type_parts
        cards = {key:CardInstance(object_id=key,ref=key,oracle_id='model:'+key,printed_name=key,
            owner='A',controller='A',zone='battlefield') for key in ('aura','first','second')}
        aura,first,second = (cards[key] for key in ('aura','first','second'))
        attach_objects(cards,aura,first,source_timestamp=1)
        context = dict(source_attachment_snapshot=capture_source_attachment_snapshot(cards,aura,K.ENCHANTED).to_dict(),
            source_logical_object_id=aura.logical_object_id)
        current = SimpleNamespace(source_object_id=aura.object_id,card_object_id=None,
            context={**context,ATTACHMENT_INFORMATION_VERSION:1})
        legacy = SimpleNamespace(source_object_id=aura.object_id,card_object_id=None,context=dict(context))
        host = SimpleNamespace(state=SimpleNamespace(cards=cards,stack=[current,legacy]),
            _type_parts=type_parts,_target_snapshot=lambda ref:{'type_line':'Creature'})
        reference = attached_exile_effect_template('Exile enchanted creature.',source_attachment_relation=K.ENCHANTED).compiled()[1][0]['card']
        attach_objects(cards,aura,second,source_timestamp=2)
        self.assertEqual(second.ref,resolve_semantic_value(host,reference,current))
        pin_attachment_information_departures(host,[aura])
        aura.zone = 'graveyard'
        aura.zone_change_counter += 1
        self.assertEqual(second.ref,resolve_semantic_value(host,reference,current))
        self.assertEqual(first.ref,resolve_semantic_value(host,{**reference,'schema_version':1},legacy))
        second.zone_change_counter += 1
        self.assertIsNone(resolve_semantic_value(host,reference,current))


NAMES = ('Cooped Up','Choking Restraints','Utter Insignificance','Dreadful Apathy')


class AttachedExileActivationRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LifeGainProhibitionRuntimeTests.session
    add = native.LifeGainProhibitionRuntimeTests.add
    prepare = native.LifeGainProhibitionRuntimeTests.prepare
    checkpoint = native.LifeGainProhibitionRuntimeTests.checkpoint
    resolve = native.LifeGainProhibitionRuntimeTests.resolve
    replay = native.LifeGainProhibitionRuntimeTests.replay
    private_replay = native.LifeGainProhibitionRuntimeTests.private_replay
    priority = native.LifeGainProhibitionRuntimeTests.priority
    actions = native.LifeGainProhibitionRuntimeTests.actions
    cast = native.LifeGainProhibitionRuntimeTests.cast
    reject = native.LifeGainProhibitionRuntimeTests.reject
    binding = native.LifeGainProhibitionRuntimeTests.binding
    activation_command = native.LifeGainProhibitionRuntimeTests.activation_command
    activate = native.LifeGainProhibitionRuntimeTests.activate
    resume = native.LifeGainProhibitionRuntimeTests.resume
    settle = native.LifeGainProhibitionRuntimeTests.settle

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'attached-exile.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/attached-exile-activation-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source Aura exile',[
            DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def fixture(self, seed, *, name='Cooped Up', recipient_seat='B'):
        s = self.session(seed)
        aura = self.add(s.engine,name,zone='hand')
        recipient = self.add(s.engine,'Wind Drake',seat=recipient_seat)
        recipient.acquired_control_turn_count = -1
        other = self.add(s.engine,'Fugitive Wizard',ref='other-recipient')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        return s,aura,recipient,other,private

    def cast_aura(self, s, aura, recipient):
        if aura.printed_name == 'Utter Insignificance':payment = {'C':1,'U':1}
        else:payment = {'C':1 if aura.printed_name == 'Cooped Up' else 2,'W':1}
        self.assertTrue(self.binding(s,aura)['strict_capability_ready'])
        self.cast(s,aura,[recipient.ref],payment=payment)
        self.assertIsNone(self.settle(s))
        self.assertEqual(recipient.object_id,s.state.cards[aura.object_id].attached_to)

    def test_paid_original_aura_exile_preserves_static_bodies_and_untargeted_shroud_behavior(self):
        for i,name in enumerate(NAMES):
            with self.subTest(name=name):
                s,aura,recipient,other,private = self.fixture(113700100+i,name=name)
                robe = self.add(s.engine,'Robe of Mirrors',zone='hand')
                self.prepare(s,mana={'C':12,'W':8,'U':4})
                self.checkpoint(s)
                self.cast_aura(s,aura,recipient)
                if name == 'Utter Insignificance':
                    self.assertEqual((1,1),(s.engine._numeric_stat(recipient.object_id,'power'),
                        s.engine._numeric_stat(recipient.object_id,'toughness')))
                    self.assertNotIn('flying',s.engine._combat_keywords(s.state.cards[recipient.object_id]))
                    self.assertIn('flash',s.engine._combat_keywords(s.state.cards[aura.object_id]))
                else:
                    self.assertFalse(s.engine._can_block(s.state.cards[other.object_id],
                        s.state.cards[recipient.object_id])[0])
                self.cast(s,robe,[recipient.ref],payment={'U':1})
                self.assertIsNone(self.settle(s))
                self.assertIn('shroud',s.engine._combat_keywords(s.state.cards[recipient.object_id]))
                command = self.activation_command(s,aura)
                programs = s.engine.semantics.programs_for_oracle(aura.oracle_id)
                activation = next(program for program in programs if program.event == 'activate')
                self.assertIsNone(activation.target_schema)
                self.reject(s,command,seat='D')
                result = s.act('pilot:A',command)
                self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard' if name == 'Choking Restraints' else 'battlefield',
                    s.state.cards[aura.object_id].zone)
                s = self.resume(s)
                self.assertIsNone(self.settle(s))
                self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
                self.assertIn(recipient.object_id,s.state.players['B'].zones['exile'])
                self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[robe.object_id].zone)
                self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
                self.private_replay(s,private)

    def test_actual_source_departure_keeps_original_attachment_and_replays_pending_ability(self):
        s,aura,recipient,other,private = self.fixture(113700120)
        destroy = self.add(s.engine,'Disenchant',zone='hand')
        self.prepare(s,mana={'C':8,'W':5})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        self.activate(s,aura)
        self.cast(s,destroy,[aura.ref],payment={'C':1,'W':1})
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.private_replay(s,private)

    def test_returned_aura_incarnation_does_not_replace_pending_source_information(self):
        s,aura,recipient,other,private = self.fixture(113700130)
        self.add(s.engine,'Vedalken Orrery')
        bounce = self.add(s.engine,'Boomerang',zone='hand')
        self.prepare(s,mana={'C':9,'W':5,'U':2})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        original_identity = s.state.cards[aura.object_id].logical_object_id
        self.activate(s,aura)
        self.cast(s,bounce,[aura.ref],payment={'U':2})
        # Resolve only the response; keep the older activated ability pending.
        while len(s.state.stack) > 1:
            result = s.act(s.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[aura.object_id].zone)
        self.assertNotEqual(original_identity,s.state.cards[aura.object_id].logical_object_id)
        self.cast(s,s.state.cards[aura.object_id],[other.ref],payment={'C':1,'W':1})
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[aura.object_id].zone)
        self.assertEqual(other.object_id,s.state.cards[aura.object_id].attached_to)
        self.private_replay(s,private)

    def test_actual_recipient_blink_never_exiles_the_returned_creature(self):
        s,aura,recipient,other,private = self.fixture(113700140,recipient_seat='A')
        blink = self.add(s.engine,'Cloudshift',zone='hand')
        self.prepare(s,mana={'C':8,'W':5})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        original_identity = s.state.cards[recipient.object_id].logical_object_id
        self.activate(s,aura)
        self.cast(s,blink,[recipient.ref],payment={'W':1})
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('battlefield',s.state.cards[recipient.object_id].zone)
        self.assertNotEqual(original_identity,s.state.cards[recipient.object_id].logical_object_id)
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.private_replay(s,private)

    def test_sacrificed_aura_exiles_its_same_recipient_after_actual_creature_type_loss(self):
        s,aura,recipient,other,private = self.fixture(113700150,name='Choking Restraints')
        self.add(s.engine,'Vedalken Orrery')
        change = self.add(s.engine,'Song of the Dryads',zone='hand')
        self.prepare(s,mana={'C':12,'W':8,'G':1})
        self.checkpoint(s)
        self.cast_aura(s,aura,recipient)
        original_identity = s.state.cards[recipient.object_id].logical_object_id
        self.activate(s,aura)
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.cast(s,change,[recipient.ref],payment={'C':2,'G':1})
        while len(s.state.stack) > 1:
            result = s.act(s.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        current = s.state.cards[recipient.object_id]
        types = s.engine._type_parts(s.engine._effective_card_data(current)['type_line'])[0]
        self.assertIn('land',types)
        self.assertNotIn('creature',types)
        self.assertEqual(original_identity,current.logical_object_id)
        s = self.resume(s)
        self.assertIsNone(self.settle(s))
        self.assertEqual('exile',s.state.cards[recipient.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[change.object_id].zone)
        self.private_replay(s,private)

    def test_verified_attached_exile_authority_withdrawals_are_specific_rules_blocks(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        value = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for cap in ('permanent.exile.effect','object.reference.source_attachment_information'):
            changed = deepcopy(value)
            next(row for row in changed['capabilities'] if row['id']==cap).update(
                status='blocked',blockers=['withdrawn attached-exile authority'],dependency_fail_closed_status='not_run')
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in NAMES:
                ir = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                blockers = [blocker for residual in ir.material_residuals for blocker in residual.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in blocker for blocker in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in blocker for blocker in blockers),blockers)

    def test_attached_exile_cannot_omit_its_runtime_or_information_dependency(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,aura,recipient,other,private = self.fixture(113700160)
        program = next(program for program in s.engine.semantics.programs_for_oracle(aura.oracle_id)
            if program.event=='activate')
        for cap in ('permanent.exile.effect','object.reference.source_attachment_information'):
            dependencies = tuple(value for value in program.capability_dependencies if value != cap)
            missing = replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertTrue(any(cap in blocker for blocker in bound['blockers']),bound['blockers'])


if __name__ == '__main__':
    unittest.main()
