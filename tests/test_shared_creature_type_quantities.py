"""Pinned Coat/Alpha/Masterwork rulings count objects once across shared types."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_ring_native_actions as traversal
import test_fixed_copy_type_exceptions as copy_controls
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.continuous_effects import CharacteristicState, ContinuousEffect, ContinuousOperation, Layer, evaluate_continuous_effects
from quorune.recipient_modifier_model import (
    RecipientQuantityKind as Kind, RecipientQuantitySpec, RECIPIENT_MODIFIER_OPERATION,
)
from quorune.shared_creature_type_model import SharedCreatureTypeObject, shared_type_objects, shared_creature_type_count
from quorune.object_query import ObjectQueryResult
from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.shared_type_anthems import SharedTypeAnthemHandler
from quorune.compiler.shared_type_quantities import shared_type_quantity_handler


class SharedCreatureTypeModelTests(unittest.TestCase):
    def member(self,object_id,types,logical=None):
        return SharedCreatureTypeObject(ContinuousObjectIdentity(object_id,logical or object_id+':0'),tuple(sorted(types)))

    def test_version_one_is_unchanged_and_version_two_requires_its_controller_role(self):
        old={'schema_version':1,'kind':'creature_types','counter':None,'mana_color':None,'maximum':10}
        self.assertEqual(old,RecipientQuantitySpec.from_dict(old).to_dict())
        self.assertEqual(json.dumps(old),json.dumps(RecipientQuantitySpec.from_dict(old).to_dict()))
        for relation in ('any','source_controller'):
            spec=RecipientQuantitySpec(Kind.SHARED_CREATURE_TYPES,schema_version=2,controller_relation=relation)
            self.assertEqual(spec,RecipientQuantitySpec.from_dict(spec.to_dict()))
            for change in ({'schema_version':1},{'controller_relation':'recipient_controller'},
                           {'controller_relation':None},{'counter':'+1/+1'},{'unknown':True}):
                with self.assertRaises(ValueError):RecipientQuantitySpec.from_dict({**spec.to_dict(),**change})
        with self.assertRaises(ValueError):RecipientQuantitySpec(Kind.CREATURE_TYPES,schema_version=2,controller_relation='any')

    def test_two_shared_types_still_count_each_other_object_once(self):
        first=self.member('one',('goblin','warrior'))
        second=self.member('two',('goblin','warrior'))
        third=self.member('three',('goblin','shaman'))
        objects=(first,second,third)
        for recipient in objects:
            self.assertEqual(2,shared_creature_type_count(objects,recipient=recipient.identity,creature_types=recipient.creature_types))
        self.assertEqual(0,shared_creature_type_count(objects,recipient=first.identity,creature_types=()))
        # A not-yet-inserted recipient still reads the other current creatures.
        self.assertEqual(3,shared_creature_type_count(objects,
            recipient=ContinuousObjectIdentity('entering','entering:0'),creature_types=('goblin',)))

    def test_snapshot_codecs_reject_ambiguous_or_malformed_members(self):
        member=self.member('one',('goblin',))
        self.assertEqual(member,SharedCreatureTypeObject.from_dict(member.to_dict()))
        for change in ({'schema_version':True},{'creature_types':['Goblin']},{'creature_types':[True]},
                       {'creature_types':['goblin','goblin']},{'unknown':1}):
            with self.assertRaises(ValueError):SharedCreatureTypeObject.from_dict({**member.to_dict(),**change})
        with self.assertRaises(ValueError):shared_type_objects((member,member))
        with self.assertRaises(ValueError):shared_type_objects((member,self.member('one',('elf',),'one:2')))
        with self.assertRaises(ValueError):shared_type_objects((member,self.member('two',('elf',),'one:0')))

    def test_shared_operation_uses_current_recipient_types_and_exact_identity(self):
        source=self.member('source',('goblin','warrior'))
        other=self.member('other',('goblin','warrior'))
        quantity=RecipientQuantitySpec(Kind.SHARED_CREATURE_TYPES,schema_version=2,controller_relation='any')
        value={'schema_version':2,'quantity':quantity.to_dict(),'power':2,'toughness':2,
               'objects':[source.to_dict(),other.to_dict()]}
        op=ContinuousOperation(RECIPIENT_MODIFIER_OPERATION,value)
        self.assertEqual(op,ContinuousOperation.from_dict(op.to_dict()))
        effect=ContinuousEffect(effect_id='shared',source_id='provider',layer=Layer.POWER_TOUGHNESS,
            sublayer='7c',timestamp=1,operations=(op,))
        state=CharacteristicState('recipient',card_types={'Creature'},subtypes={'Goblin','Warrior'},power=2,toughness=2)
        def result(logical):
            return evaluate_continuous_effects(deepcopy(state),(effect,),
                context={'object_id':'source','logical_object_id':logical}).characteristics
        self.assertEqual((4,4),(result('source:0')['power'],result('source:0')['toughness']))
        # A distinct incarnation has no self member in this historical snapshot.
        self.assertEqual(6,result('source:2')['power'])
        state.subtypes={'Elf'}
        self.assertEqual(2,result('source:0')['power'])
        for update in ({'schema_version':1},{'objects':[source.to_dict(),source.to_dict()]},{'power':True}):
            with self.assertRaises(ValueError):ContinuousOperation(RECIPIENT_MODIFIER_OPERATION,{**value,**update})


class SharedCreatureTypeHandlerTests(unittest.TestCase):
    def row(self,ref,types,*,controller='A',card_types=('creature',),phased=False):
        return ObjectQueryResult(ref,ref,'Public object','D',controller,'battlefield',types=card_types,
            subtypes=types,logical_object_id=ref+':0',phased_out=phased)

    def context(self,rows=None,*,attached=None):
        return ContinuousEffectSourceContext('provider','provider-ref','A',1,'shared-component',
            attached_object=attached,source_logical_object_id='provider:0',
            public_type_objects=rows)

    def test_controller_scope_is_the_provider_and_only_current_creatures_count(self):
        compiled=shared_type_quantity_handler('Equipped creature gets +1/+1 for each other creature you control that shares a creature type with it.')
        receiver=self.row('receiver',('goblin','warrior'),controller='B')
        rows=(receiver,self.row('one',('goblin',)),self.row('two',('warrior',)),
              self.row('kindred',('goblin',),card_types=('kindred','enchantment')),
              self.row('phased',('goblin',),phased=True),self.row('typeless',()))
        context=self.context(rows,attached=ContinuousObjectIdentity('receiver','receiver:0'))
        effect=SharedTypeAnthemHandler().lower(compiled[1],context)[0]
        state=CharacteristicState('receiver',card_types={'Creature'},subtypes={'Goblin','Warrior'},power=2,toughness=2)
        result=evaluate_continuous_effects(state,(effect,),context={'object_id':'receiver','logical_object_id':'receiver:0','zone':'battlefield'}).characteristics
        self.assertEqual((4,4),(result['power'],result['toughness']))
        untouched=evaluate_continuous_effects(CharacteristicState('new',card_types={'Creature'},subtypes={'Goblin'},power=2,toughness=2),
            (effect,),context={'object_id':'receiver','logical_object_id':'receiver:2','zone':'battlefield'}).characteristics
        self.assertEqual(2,untouched['power'])

    def test_no_attachment_and_unavailable_world_are_distinct(self):
        attached=shared_type_quantity_handler('Enchanted creature gets +2/+2 for each other creature on the battlefield that shares a creature type with it.')[1]
        self.assertEqual((),SharedTypeAnthemHandler().lower(attached,self.context()))
        global_descriptor=shared_type_quantity_handler('Each creature gets +1/+1 for each other creature on the battlefield that shares at least one creature type with it.')[1]
        with self.assertRaises(SemanticNodeError):SharedTypeAnthemHandler().lower(global_descriptor,self.context())

    def test_new_snapshot_is_skipped_at_the_public_type_boundary(self):
        from quorune.card_programs.runtime import _component_public_state_snapshot
        descriptor=shared_type_quantity_handler('Each creature gets +1/+1 for each other creature on the battlefield that shares a creature type with it.')[1]
        self.assertEqual((True,None),_component_public_state_snapshot(None,descriptor,
            maximum_layer=Layer.COLOR,public_state_resolver=None,setting_state_resolver=None))

    def test_old_handler_cannot_accept_the_new_operation_or_untyped_context(self):
        from quorune.semantic_runtime.recipient_anthems import RecipientAnthemHandler
        descriptor=shared_type_quantity_handler('Each creature gets +1/+1 for each other creature on the battlefield that shares a creature type with it.')[1]
        local={key:value for key,value in descriptor.items() if key!='relation'}
        local['handler_id']='continuous.anthem.recipient-quantity.v1'
        with self.assertRaises(SemanticNodeError):RecipientAnthemHandler().validate(local)
        with self.assertRaises(SemanticNodeError):self.context(({},))


class SharedCreatureTypeCompilerTests(unittest.TestCase):
    def test_original_sentences_lower_complete_counts_with_distinct_controller_roles(self):
        registry=load_default_capability_registry()
        cases=(('Each creature gets +1/+1 for each other creature on the battlefield that shares at least one creature type with it.','any','none'),
            ('Enchanted creature gets +2/+2 for each other creature on the battlefield that shares a creature type with it.','any','source_attached_to_object'),
            ('Equipped creature gets +1/+1 for each other creature you control that shares a creature type with it.','source_controller','source_attached_to_object'))
        for text,controller,relation in cases:
            record=replace(query_record(text),type_line='Artifact',keywords=())
            ir=compile_oracle_card(record,capability_registry=registry)
            self.assertEqual('exact',ir.status)
            node=next(node for face in ir.faces for node in face.nodes)
            descriptor=node.handlers[0]
            self.assertEqual(controller,descriptor['modifier']['quantity']['controller_relation'])
            self.assertEqual(relation,descriptor['relation'])
            self.assertIn('continuous.power_toughness.recipient_shared_type',node.capability_dependencies)
        for text in ('Each creature gets +1/+1 for each creature on the battlefield that shares a creature type with it.',
                     'Each creature gets +1/+1 for each other creature that shares a color with it.',
                     'Each creature gets +1/+1 for each other creature you own that shares a creature type with it.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Artifact',keywords=()),
                capability_registry=registry).status)

    def test_dependency_fail_closed_uses_verified_index_and_an_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=replace(query_record('Each creature gets +1/+1 for each other creature on the battlefield that shares a creature type with it.'),
            type_line='Artifact',keywords=())
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for omitted in ('state_query.permanent.shared_creature_type','continuous.power_toughness.recipient_shared_type',
                        'continuous.power_toughness.recipient_quantity','continuous.power_toughness.fixed_anthem','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(status='blocked',blockers=['tested omitted owner'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            with self.subTest(omitted=omitted):
                self.assertEqual(omitted=='variant.monarch.designate',compile_oracle_card(record,capability_registry=registry).status=='exact')


class SharedCreatureTypeNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay
    until=traversal.RingNativeActionTests.until
    choose_copy=copy_controls.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'shared-types.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/shared-creature-type-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Shared creature types',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,name):
        program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'],name)

    def stats(self,session,card):
        data=session.engine._effective_card_data(session.state.cards[card.object_id])
        return int(data['power']),int(data['toughness'])

    def battlefield(self,session):
        return (self.add(session.engine,'Goblin Piker',ref='shared-piker'),
            self.add(session.engine,'Goblin Assailant',seat='B',ref='shared-assailant'),
            self.add(session.engine,"Mons's Goblin Raiders",seat='C',ref='shared-raiders'),
            self.add(session.engine,'Universal Automaton',seat='D',ref='shared-changeling'),
            self.add(session.engine,'Grizzly Bears',ref='shared-bear'))

    def test_original_coat_counts_objects_once_and_updates_copy_departure_and_replay(self):
        for name in ('Coat of Arms','Clone','Universal Automaton'):self.assert_closed(name)
        session=self.session(613_040_101)
        source=self.add(session.engine,'Coat of Arms',zone='hand',ref='shared-coat')
        copied=self.add(session.engine,'Clone',zone='hand',ref='shared-clone')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='shared-bounce')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='shared-private-C')
        piker,assailant,raiders,changeling,bear=self.battlefield(session)
        self.prepare(session,mana={'U':4,'C':20});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertEqual([(5,4),(5,5),(4,4),(5,5),(3,3)],
            [self.stats(session,card) for card in (piker,assailant,raiders,changeling,bear)])
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.cast(session,'A',copied);session=self.choose_copy(session,assailant)
        self.assertEqual([(6,5),(6,6),(5,5),(6,6),(6,6),(3,3)],
            [self.stats(session,card) for card in (piker,assailant,raiders,copied,changeling,bear)])
        self.cast(session,'A',bounce,[changeling.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual([(5,4),(5,5),(4,4),(5,5),(2,2)],
            [self.stats(session,card) for card in (piker,assailant,raiders,copied,bear)])
        self.replay(session,load=True)

    def test_original_alpha_uses_its_attachment_and_live_cross_player_matches(self):
        self.assert_closed('Alpha Status');session=self.session(613_040_102)
        aura=self.add(session.engine,'Alpha Status',zone='hand',ref='shared-alpha')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='shared-alpha-bounce')
        removal=self.add(session.engine,'Disenchant',zone='hand',ref='shared-alpha-remove')
        piker,assailant,_raiders,changeling,_bear=self.battlefield(session)
        self.prepare(session,mana={'G':4,'U':4,'W':4,'C':20});self.checkpoint(session)
        self.cast(session,'A',aura,[piker.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((8,7),self.stats(session,piker));self.assertEqual((2,2),self.stats(session,assailant))
        self.cast(session,'A',bounce,[changeling.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((6,5),self.stats(session,piker))
        self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((2,1),self.stats(session,piker))
        self.replay(session,load=True)

    def test_original_masterwork_counts_its_controller_after_receiver_control_changes(self):
        self.assert_closed('Stoneforge Masterwork');self.assert_closed('Act of Treason')
        session=self.session(613_040_103)
        equipment=self.add(session.engine,'Stoneforge Masterwork',ref='shared-masterwork')
        receiver=self.add(session.engine,'Goblin Piker',ref='shared-equipped')
        self.add(session.engine,'Goblin Assailant',ref='shared-ally-one')
        self.add(session.engine,"Mons's Goblin Raiders",ref='shared-ally-two')
        self.add(session.engine,'Universal Automaton',seat='D',ref='shared-excluded-opponent')
        steal=self.add(session.engine,'Act of Treason',seat='B',zone='hand',ref='shared-steal')
        for index in range(3):self.add(session.engine,'Mountain',seat='B',ref='shared-mountain-'+str(index))
        self.prepare(session,mana={'C':20});self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        equip=next(action for action in actions if action['id'].startswith('activate:'+equipment.ref+':'))
        result=session.act('pilot:A',{'action_id':equip['id'],'targets':[receiver.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual((4,3),self.stats(session,receiver))
        self.until(session,lambda current:current.state.active_player=='B' and current.state.phase=='precombat_main'
            and current.state.pending_decision is not None and current.state.pending_decision.kind=='priority'
            and current.state.pending_decision.actors==['B'])
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+steal.ref,'targets':[receiver.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        for seat in 'ACD':self.assertNotIn(steal.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.cast(session,'B',steal,[receiver.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('B',session.state.cards[receiver.object_id].controller)
        self.assertEqual('A',session.state.cards[equipment.object_id].controller)
        self.assertEqual((4,3),self.stats(session,receiver))
        self.replay(session,load=True)

    def test_shared_quantity_omission_mutant_is_killed_by_native_coat(self):
        with patch('quorune.shared_creature_type_model.shared_creature_type_count',return_value=0),self.assertRaises(AssertionError):
            self.test_original_coat_counts_objects_once_and_updates_copy_departure_and_replay()
