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


class SharedCreatureTypeComparisonTests(unittest.TestCase):
    @staticmethod
    def snapshot(object_id,types,epoch=0):
        return SharedCreatureTypeObject(ContinuousObjectIdentity(object_id,f"{object_id}@{epoch}"),
            tuple(sorted(types)))

    def test_any_shared_type_matches_and_empty_or_disjoint_types_do_not(self):
        from quorune.rules.shared_type_library_model import SharedCreatureTypeComparison as Comparison
        source=self.snapshot("source",("elf","shaman"))
        self.assertTrue(Comparison(source,self.snapshot("card",("elf",))).matches)
        self.assertTrue(Comparison(source,self.snapshot("card",("shaman","warrior"))).matches)
        self.assertFalse(Comparison(source,self.snapshot("card",("goblin",))).matches)
        self.assertFalse(Comparison(source,self.snapshot("card",())).matches)
        self.assertFalse(Comparison(self.snapshot("source",()),self.snapshot("card",("elf",))).matches)

    def test_kindred_or_changeling_membership_requires_no_creature_card_category(self):
        from quorune.rules.shared_type_library_model import SharedCreatureTypeComparison as Comparison
        from quorune.shared_creature_type_model import canonical_creature_types
        from quorune.creature_subtypes import CREATURE_SUBTYPES
        source=self.snapshot("source",("elf",))
        candidate=SharedCreatureTypeObject(ContinuousObjectIdentity("kindred-instant","kindred-instant@0"),
            canonical_creature_types(CREATURE_SUBTYPES))
        self.assertTrue(Comparison(source,candidate).matches)
        self.assertFalse(Comparison(self.snapshot("source",()),candidate).matches)

    def test_same_physical_source_returned_to_library_can_be_compared_as_new_incarnation(self):
        from quorune.rules.shared_type_library_model import SharedCreatureTypeComparison as Comparison
        source=self.snapshot("same-card",("elf",),0)
        top=self.snapshot("same-card",("elf",),1)
        self.assertNotEqual(source.identity,top.identity)
        self.assertTrue(Comparison(source,top).matches)

    def test_exact_codec_is_immutable_and_preserves_existing_snapshot_encoding(self):
        from dataclasses import FrozenInstanceError
        from quorune.rules.shared_type_library_model import SharedCreatureTypeComparison as Comparison
        source=self.snapshot("source",("elf",));candidate=self.snapshot("card",("elf",))
        old=source.to_dict();spec=Comparison(source,candidate);wire=spec.to_dict()
        self.assertEqual(spec,Comparison.from_dict(wire))
        self.assertEqual(old,wire["source"])
        restored=Comparison.from_dict(wire);wire["source"]["creature_types"].append("goblin")
        self.assertEqual(("elf",),restored.source.creature_types)
        with self.assertRaises(FrozenInstanceError):spec.schema_version=2

    def test_missing_unknown_or_wrong_snapshot_and_version_fail_closed(self):
        from quorune.rules.shared_type_library_model import SharedCreatureTypeComparison as Comparison
        wire=Comparison(self.snapshot("source",("elf",)),self.snapshot("card",("elf",))).to_dict()
        for version in (True,False,0,2,1.0,"1"):
            with self.subTest(version=version),self.assertRaises(ValueError):
                Comparison.from_dict({**wire,"schema_version":version})
        for bad in ({}, {**wire,"extra":None}, {**wire,"source":None}, {**wire,"candidate":{}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):Comparison.from_dict(bad)
        with self.assertRaises(ValueError):Comparison({},self.snapshot("card",("elf",)))

    def test_malformed_type_or_identity_snapshots_are_not_reinterpreted(self):
        from quorune.rules.shared_type_library_model import SharedCreatureTypeComparison as Comparison
        wire=Comparison(self.snapshot("source",("elf",)),self.snapshot("card",("elf",))).to_dict()
        for types in (["elf","elf"],["warrior","elf"],["not-a-creature-type"],["elf",True]):
            bad=deepcopy(wire);bad["candidate"]["creature_types"]=types
            with self.subTest(types=types),self.assertRaises(ValueError):Comparison.from_dict(bad)
        bad=deepcopy(wire);bad["source"]["identity"]["logical_object_id"]=False
        with self.assertRaises(ValueError):Comparison.from_dict(bad)


class SharedTypeLibraryChoiceTests(unittest.TestCase):
    @staticmethod
    def result():
        from quorune.rules.shared_type_library_model import SharedTypeLibraryResult
        return SharedTypeLibraryResult(({"op":"life","player":"$controller","delta":4},),("cr-119-life",))

    def context(self, *, types=("elf",), empty=False):
        from types import SimpleNamespace
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery,SemanticChoiceContext
        from quorune.replacement.immutable import FrozenMap
        from quorune.object_query import ObjectQueryResult
        top=ObjectQueryResult("top","TOP","Kindred card","A","A","library",
            types=("kindred","instant"),subtypes=types,logical_object_id="top@0",known_to_actor=True)
        base=SnapshotSemanticChoiceQuery(seat_order=("A","B","C","D"),active_order=("A","B","C","D"),
            object_rows=() if empty else (top,),libraries_by_seat=FrozenMap({"A":[] if empty else ["TOP"]}))
        source=SharedCreatureTypeObject(ContinuousObjectIdentity("source","source@0"),("elf","shaman"))
        class Query:
            shared_type_library_source=source
            def __getattr__(self,name):return getattr(base,name)
        query=Query()
        return SemanticChoiceContext("A","S","A","Shared type","SOURCE",None,"program",1,query,
            source_logical_object_id="source@0",source_object_id="source"),top

    @staticmethod
    def continuation(effect):
        from types import SimpleNamespace
        return SimpleNamespace(effect=effect,semantic_frame=SimpleNamespace(controller="A"),note="Shared type")

    def test_look_is_optional_and_does_not_expose_or_reveal_before_acceptance(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        h=SharedTypeLibraryHandler();context,top=self.context()
        prep=h.prepare(self.result().effect(player="A"),context)
        self.assertEqual((),prep.preparation_intents)
        self.assertNotIn("TOP",json.dumps(dict(prep.request.public_context)))
        self.assertNotIn("Kindred card",json.dumps(dict(prep.request.public_context)))
        done=h.complete(self.continuation(prep.continuation_effect),{"choice":"decline"},context.query)
        self.assertEqual(((),()),(done.intents,done.prepend_effects))

    def test_matching_kindred_card_gets_separate_optional_reveal_then_entire_symbolic_result(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        h=SharedTypeLibraryHandler();context,top=self.context();effect=self.result().effect(player="A")
        first=h.prepare(effect,context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        self.assertFalse(looked.intents[0].public);self.assertEqual(("TOP",),looked.intents[0].refs_top_first)
        stage,=looked.prepend_effects;second=h.prepare(stage,context)
        self.assertEqual((),second.preparation_intents)
        self.assertEqual("Kindred card",second.request.public_context["objects"][0]["name"])
        declined=h.complete(self.continuation(second.continuation_effect),{"choice":"decline"},context.query)
        self.assertEqual(((),()),(declined.intents,declined.prepend_effects))
        revealed=h.complete(self.continuation(second.continuation_effect),{"choice":"reveal"},context.query)
        self.assertTrue(revealed.intents[0].public)
        self.assertEqual("$controller",revealed.prepend_effects[0]["player"])

    def test_nonmatch_and_empty_library_do_not_execute_result(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        h=SharedTypeLibraryHandler();context,top=self.context(types=("goblin",))
        first=h.prepare(self.result().effect(player="A"),context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        second=h.prepare(looked.prepend_effects[0],context)
        self.assertIsNotNone(second.request);self.assertEqual((),second.preparation_intents)
        self.assertEqual(("decline",),second.request.choice.legal_values)
        done=h.complete(self.continuation(second.continuation_effect),{"choice":"decline"},context.query)
        self.assertEqual((),done.prepend_effects)
        empty,_=self.context(empty=True)
        self.assertIsNotNone(h.prepare(self.result().effect(player="A"),empty).auto_continue)

    def test_changed_top_incarnation_and_foreign_actor_or_tampered_stage_fail_closed(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.replacement.immutable import FrozenMap
        h=SharedTypeLibraryHandler();context,top=self.context()
        first=h.prepare(self.result().effect(player="A"),context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        for change in ({"_top":{"ref":"TOP","logical_object_id":"top@1"}},{"_shared_type_stage":"unknown"},{"player":"B"},{"extra":True}):
            with self.subTest(change=change),self.assertRaises(SemanticChoiceError):
                h.prepare({**dict(looked.prepend_effects[0]),**change},context)
        with self.assertRaises(SemanticChoiceError):
            h.prepare(self.result().effect(player="A"),replace(context,actor="B"))
        with self.assertRaises(SemanticChoiceError):
            h.complete(self.continuation(first.continuation_effect),{"choice":"reveal"},context.query)

    def test_result_codec_requires_closed_children_and_retains_parent_dependencies(self):
        from quorune.rules.shared_type_library_model import SharedTypeLibraryResult,SHARED_TYPE_LIBRARY_CAPABILITY
        spec=self.result();self.assertEqual(spec,SharedTypeLibraryResult.from_dict(spec.to_dict()))
        self.assertIn("life.change.effect",spec.capability_dependencies)
        self.assertIn(SHARED_TYPE_LIBRARY_CAPABILITY,spec.capability_dependencies)
        for bad in ({**spec.to_dict(),"schema_version":True},{**spec.to_dict(),"extra":None},
                {**spec.to_dict(),"effects":[{"op":"invoke_callback","callback":"anything"}]},
                {**spec.to_dict(),"effects":[]},{**spec.to_dict(),"effects":[{"op":"life","player":"$controller","delta":"X"}]}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):SharedTypeLibraryResult.from_dict(bad)

    def test_declining_reveal_already_public_still_runs_no_result_and_success_has_no_move(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        h=SharedTypeLibraryHandler();context,top=self.context()
        first=h.prepare(self.result().effect(player="A"),context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        second=h.prepare(looked.prepend_effects[0],context)
        self.assertEqual(("reveal","decline"),second.request.choice.legal_values)
        complete=h.complete(self.continuation(second.continuation_effect),{"choice":"reveal"},context.query)
        self.assertEqual(["RevealLibraryCardsIntent"],[type(i).__name__ for i in complete.intents])
        self.assertEqual(("TOP",),context.query.library_refs("A",top_first=True))


    def test_evaluated_matching_condition_survives_later_public_type_change(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        h=SharedTypeLibraryHandler();context,top=self.context()
        first=h.prepare(self.result().effect(player="A"),context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        second=h.prepare(looked.prepend_effects[0],context)
        # The continuation carries its completed comparison, independent of later query snapshots.
        context.query.shared_type_library_source=SharedCreatureTypeObject(
            ContinuousObjectIdentity("source","source@0"),("goblin",))
        result=h.complete(self.continuation(second.continuation_effect),{"choice":"reveal"},context.query)
        self.assertTrue(result.intents[0].public)
        self.assertEqual(1,len(result.prepend_effects))


    def test_fresh_nonmatch_reveal_gate_mutant_is_killed(self):
        import inspect,textwrap
        from quorune.semantic_choices import shared_type_library as owner
        source=textwrap.dedent(inspect.getsource(owner.SharedTypeLibraryHandler.prepare))
        expression='legal_values=("reveal", "decline") if comparison.matches else ("decline",)'
        self.assertEqual(1,source.count(expression))
        ns=dict(vars(owner));exec(source.replace(expression,'legal_values=("reveal", "decline")'),ns)
        context,top=self.context(types=("goblin",));h=owner.SharedTypeLibraryHandler()
        first=h.prepare(self.result().effect(player="A"),context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        def witness(prepare):
            self.assertEqual(("decline",),prepare(h,looked.prepend_effects[0],context).request.choice.legal_values)
        witness(owner.SharedTypeLibraryHandler.prepare)
        with self.assertRaises(AssertionError):witness(ns["prepare"])

    def test_pending_reveal_codec_roundtrip_and_raw_owner_minimum_preserve_child(self):
        from quorune.semantic_choices.shared_type_library import SharedTypeLibraryHandler
        from quorune.replacement.immutable import thaw_value,FrozenMap
        from quorune.rules.shared_type_library import shared_type_library_payload_dependencies
        from quorune.rules.shared_type_library_model import SHARED_TYPE_LIBRARY_CAPABILITY
        h=SharedTypeLibraryHandler();context,top=self.context();spec=self.result()
        deps=shared_type_library_payload_dependencies(spec.effect())
        self.assertTrue({SHARED_TYPE_LIBRARY_CAPABILITY,"life.change.effect","target.ability_source.current_or_lki"}<=set(deps))
        first=h.prepare(spec.effect(player="A"),context)
        looked=h.complete(self.continuation(first.continuation_effect),{"choice":"look"},context.query)
        ready=h.prepare(looked.prepend_effects[0],context)
        restored=FrozenMap(json.loads(json.dumps(thaw_value(ready.continuation_effect))))
        self.assertEqual(ready.continuation_effect,restored)
        done=h.complete(self.continuation(restored),{"choice":"reveal"},context.query)
        self.assertEqual("$controller",done.prepend_effects[0]["player"])


class SharedTypeLibraryCompilerTests(unittest.TestCase):
    def test_full_generic_wrapper_is_source_spanned_and_requires_fine_and_child_owners(self):
        from quorune.rules.shared_type_library_model import SHARED_TYPE_LIBRARY_CAPABILITY
        text=("At the beginning of your upkeep, you may look at the top card of your library. "
              "If it shares a creature type with this creature, you may reveal it. "
              "If you do, this creature gets +2/+2 and gains flying and vigilance until end of turn.")
        card=replace(query_record(text),type_line="Creature — Elf",power="2",toughness="2")
        ir=compile_oracle_card(card,capability_registry=load_default_capability_registry())
        node,=ir.faces[0].nodes
        self.assertEqual("triggered_ability",node.kind)
        self.assertEqual(text,node.text);self.assertEqual(text,card.oracle_text[node.span.start:node.span.end])
        self.assertIn(SHARED_TYPE_LIBRARY_CAPABILITY,node.capability_dependencies)
        self.assertTrue({"combat.block.flying","combat.attack.vigilance"}<=set(node.capability_dependencies))
        self.assertEqual("look_top_shared_type_reveal",node.effects[0]["op"])
        self.assertEqual("exact",ir.status,ir.material_residuals)

    def test_unknown_reveal_predicate_tail_or_targeted_result_does_not_get_wrapper(self):
        from quorune.compiler.shared_type_library_templates import shared_type_library_result_template
        from types import SimpleNamespace
        binding=SimpleNamespace(variant="your upkeep",active_zone="battlefield")
        good=("You may look at the top card of your library. If it shares a creature type "
              "with this creature, you may reveal it. If you do, you gain 4 life.")
        result=lambda body:("fixed",({"op":"life","player":"$controller","delta":4},),None,("cr-119-life",))
        self.assertIsNotNone(shared_type_library_result_template(binding,good,compile_result=result))
        for bad in (good.replace("creature type","name"),good.replace("your library","an opponent's library"),
                    good.replace("you may reveal it","reveal it"),good+" Rewrite history."):
            compiled=shared_type_library_result_template(binding,bad,compile_result=lambda body:result(body) if body=="you gain 4 life." else (None,(),None,()))
            self.assertIsNone(compiled)
        self.assertIsNone(shared_type_library_result_template(binding,good,compile_result=lambda body:
            ("targeted",({"op":"life","player":"$target.0","delta":4},),{"count":1},("cr-119-life",))))

    def test_symbolic_result_is_not_resolved_until_after_reveal(self):
        from types import SimpleNamespace
        from quorune.rules.shared_type_library_model import SharedTypeLibraryResult
        from quorune.semantic_runtime.values import resolve_semantic_value
        spec=SharedTypeLibraryResult(({"op":"life","player":"$controller","delta":4},),("cr-119-life",))
        resolved=resolve_semantic_value(SimpleNamespace(),spec.effect(),SimpleNamespace(controller="B"))
        self.assertEqual("B",resolved["player"])
        self.assertEqual("$controller",resolved["result"]["effects"][0]["player"])

    def test_source_types_use_current_then_immediate_departure_binding_not_reentry(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.target_source import capture_targeting_source,TARGETING_SOURCE_CONTEXT,pin_targeting_source_departures
        from quorune.rules.shared_type_library import materialize_shared_type_library_source
        card=CardInstance("source","SOURCE","fixture:source","Source","A","A","battlefield")
        current={"type_line":"Creature — Elf Shaman","colors":["G"]}
        host=SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card},stack=[],pending_trigger_batches=[]),
            _effective_card_data=lambda c:dict(current))
        item=SimpleNamespace(source_object_id=card.object_id,card_object_id=None,kind="triggered_ability",ref="S",
            context={"source_logical_object_id":card.logical_object_id})
        item.context[TARGETING_SOURCE_CONTEXT]=capture_targeting_source(host,card).to_dict()
        effect={"op":"look_top_shared_type_reveal"}
        self.assertEqual(("elf","shaman"),materialize_shared_type_library_source(host,item,effect).creature_types)
        current["type_line"]="Creature — Goblin"
        self.assertEqual(("goblin",),materialize_shared_type_library_source(host,item,effect).creature_types)
        # Canonical departure pinner operates over serialized frames.
        frame=SimpleNamespace(**vars(item));frame.to_dict=lambda:{"ref":frame.ref,"source_object_id":frame.source_object_id,"context":dict(frame.context)}
        host.state.stack=[frame]
        pin_targeting_source_departures(host,[card])
        item.context=frame.context
        card.zone_change_counter+=1;card.zone="graveyard";current["type_line"]="Creature — Elf"
        self.assertEqual(("goblin",),materialize_shared_type_library_source(host,item,effect).creature_types)
        card.zone="battlefield"
        self.assertEqual(("goblin",),materialize_shared_type_library_source(host,item,effect).creature_types)


from inherited_composition_fixture import InheritedNativeFixture


class SharedTypeLibraryNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition,DeckEntry
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/"tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        base["cards"]=[r for r in base["cards"] if r["name"]=="Generic Bound Plains"];base["rulings"]=[]
        path=folder/"base.json";path.write_text(json.dumps(base),encoding="utf-8")
        build_fixture_database([path,ROOT/"tests/fixtures/shared-type-library-cards.json"],folder/"shared-library.sqlite3")
        cls.db=CardDatabase(folder/"shared-library.sqlite3");cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition("Shared type",[DeckEntry("Isamaru, Hound of Konda",1,"commander"),
            DeckEntry("Generic Bound Plains",30)],["Isamaru, Hound of Konda"])

    def private_library_session(self,seed,*,top_name="Llanowar Elves"):
        from quorune.session import CommanderSession
        from quorune.model import GameConfig
        from common import keep_all
        from copy import deepcopy
        from quorune.deck import DeckDefinition,DeckEntry
        decks={seat:deepcopy(self.deck) for seat in "ABCD"}
        decks["A"]=DeckDefinition("Shared type",[DeckEntry("Isamaru, Hound of Konda",1,"commander"),
            DeckEntry(top_name,30)],["Isamaru, Hound of Konda"])
        s=CommanderSession.create(self.db,decks,first_player="A",seed=seed,
            config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(s);s.engine.permissions.invalidate_current();s.state.pending_decision=None
        s.state.priority_player=None;s.state.priority_passes=[]
        for seat in "ABCD":
            for oid in tuple(s.state.players[seat].zones["hand"])[:2]:
                s.engine.move_card(oid,"library",reason="Bound native full-turn hands",log=False)
            s.engine.shuffle_library(seat,reason="Hide prepared native library order")
        return s

    def advance_to_look(self,s,source):
        for _ in range(400):
            s.engine.pump();decision=s.state.pending_decision
            if decision is not None and decision.kind=="semantic.choice":return
            self.assertIsNotNone(decision)
            if decision.kind=="priority":command={"action_id":"pass"}
            elif decision.kind=="combat.attackers":command={"action_id":"attack","attackers":{}}
            elif decision.kind=="combat.blockers":command={"action_id":"block","blocks":{}}
            elif decision.kind=="trigger.order":
                group=next(g for g in s.state.pending_trigger_batches[0].groups if g.controller==decision.actors[0])
                command={"action_id":"order","triggers":[i.ref for i in group.items]}
            else:self.fail("Unexpected shared-type native step: "+decision.kind)
            result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.fail("Actual upkeep did not reach private look")

    def choose(self,s,value):
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act("pilot:C",{"action_id":"choose","choice":value}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act("pilot:A",{"action_id":"choose","choice":value});self.assertTrue(result.ok,result.summary)


class SharedTypeLibraryNativeTests(SharedTypeLibraryNativeFixture,unittest.TestCase):
    def test_eleven_originals_bind_whole_normally_and_freeplay_neighbor_stays_residual(self):
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        names=("Pyroclast Consul","Winnower Patrol","Sensation Gorger","Waterspout Weavers",
               "Ink Dissolver","Mudbutton Clanger","Squeaking Pie Grubfellows","Wolf-Skull Shaman",
               "Wandering Graybeard","Kithkin Zephyrnaut","Nightshade Schemers")
        for name in names:
            card=self.db.lookup(name)
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile="commander_review")
            self.assertEqual("exact",ir.status,(name,ir.material_residuals))
            p=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                capability_registry=self.registry,capability_profile="commander_review")
            self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile="commander_review")["strict_capability_ready"],name)
        c=self.db.lookup("Leaf-Crowned Elder")
        self.assertNotEqual("exact",compile_oracle_card(c,capability_registry=self.registry).status)

    def test_actual_paid_patrol_private_look_optional_public_reveal_counter_pending_load_and_replay(self):
        s=self.private_library_session(2072001)
        source=self.add(s.engine,"Winnower Patrol",zone="hand")
        self.assert_closed(s,source);self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s))
        self.assertFalse(any(s.state.players["A"].mana_pool.values()))
        self.advance_to_look(s,source)
        top=s.state.cards[s.state.players["A"].zones["library"][-1]]
        for seat in "ABCD":self.assertNotIn(top.ref,json.dumps(s.packet("pilot:"+seat,full=True)))
        s=self.replay(s,load=True);self.choose(s,"look")
        self.assertEqual("semantic.choice",s.state.pending_decision.kind)
        self.assertTrue(s.state.stack)
        self.assertIn(top.ref,json.dumps(s.packet("pilot:A",full=True)))
        for seat in "BCD":self.assertNotIn(top.ref,json.dumps(s.packet("pilot:"+seat,full=True)))
        self.assertFalse(s.state.cards[source.object_id].counters)
        s=self.replay(s,load=True);self.choose(s,"reveal");self.assertIsNone(self.resolve(s))
        self.assertEqual({"+1/+1":1},s.state.cards[source.object_id].counters)
        self.assertEqual(top.object_id,s.state.players["A"].zones["library"][-1])
        for seat in "ABCD":self.assertIn(top.ref,json.dumps(s.packet("pilot:"+seat,full=True)))
        self.replay(s,load=True)


    def test_actual_nonmatch_and_match_keep_same_public_progress_after_private_look(self):
        public=[]
        for match in (True,False):
            with self.subTest(match=match):
                s=self.private_library_session(2072002,top_name="Llanowar Elves" if match else "Grizzly Bears")
                source=self.add(s.engine,"Winnower Patrol",zone="hand")
                self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
                self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
                top=s.state.cards[s.state.players["A"].zones["library"][-1]]
                self.choose(s,"look")
                self.assertEqual("semantic.choice",s.state.pending_decision.kind)
                self.assertEqual(1,len(s.state.stack))
                packet=s.packet("pilot:B",full=True)
                self.assertNotIn(top.ref,json.dumps(packet))
                public.append((packet["state"]["turn"],packet["state"]["stack"],packet["state"]["players"]["A"]))
                self.choose(s,"decline");self.assertIsNone(self.resolve(s))
                self.assertFalse(s.state.cards[source.object_id].counters)
                self.replay(s,load=True)
        self.assertEqual(public[0],public[1])

    def test_actual_declining_look_or_reveal_never_executes_counter_or_moves_top(self):
        for stage in ("look","reveal"):
            with self.subTest(stage=stage):
                s=self.private_library_session(2072003+(stage=="reveal"))
                source=self.add(s.engine,"Winnower Patrol",zone="hand")
                self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
                self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
                top=s.state.cards[s.state.players["A"].zones["library"][-1]]
                before=authoritative_state_hash(s.state)
                self.assertFalse(s.act("pilot:A",{"action_id":"choose","choice":"reveal"}).ok)
                self.assertEqual(before,authoritative_state_hash(s.state))
                if stage=="reveal":self.choose(s,"look")
                self.choose(s,"decline");self.assertIsNone(self.resolve(s))
                self.assertFalse(s.state.cards[source.object_id].counters)
                self.assertEqual(top.object_id,s.state.players["A"].zones["library"][-1])
                for viewer in "BCD":self.assertNotIn(top.ref,json.dumps(s.packet("pilot:"+viewer,full=True)))
                self.replay(s,load=True)

    def test_actual_kindred_instant_changeling_qualifies_without_creature_card_type(self):
        s=self.private_library_session(2072005,top_name="Nameless Inversion")
        source=self.add(s.engine,"Winnower Patrol",zone="hand")
        self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
        self.choose(s,"look")
        self.assertEqual("semantic.choice",s.state.pending_decision.kind)
        s=self.replay(s,load=True);self.choose(s,"reveal");self.assertIsNone(self.resolve(s))
        self.assertEqual({"+1/+1":1},s.state.cards[source.object_id].counters)
        self.replay(s,load=True)


    def begin_printed_result(self,name,mana,seed,*,setup=None):
        s=self.private_library_session(seed,top_name=name)
        source=self.add(s.engine,name,zone="hand")
        extra=setup(s) if setup else None
        self.assert_closed(s,source);self.prepare(s,mana=mana);self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s))
        self.assertFalse(any(s.state.players["A"].mana_pool.values()))
        self.advance_to_look(s,source);self.choose(s,"look")
        top=s.state.cards[s.state.players["A"].zones["library"][-1]]
        self.choose(s,"reveal")
        return s,source,top,extra

    def finish_printed_result(self,s):
        for _ in range(80):
            s.engine.pump();decision=s.state.pending_decision
            if not s.state.stack:return
            self.assertIsNotNone(decision)
            principal=s.pending_principals()[0]
            if decision.kind=="priority":command={"action_id":"pass"}
            elif decision.kind=="choice.apnap":
                seat=decision.actors[0]
                card=s.state.cards[s.state.players[seat].zones["hand"][0]]
                command={"action_id":"choose","cards":[card.ref]}
            else:self.fail("Unexpected printed result decision: "+decision.kind)
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail("Printed result did not finish")

    def test_actual_printed_source_modifiers_keywords_and_life_results(self):
        cases=(("Mudbutton Clanger",{"R":1},(2,2),()),
               ("Kithkin Zephyrnaut",{"C":2,"W":1},(4,4),("flying","vigilance")),
               ("Wandering Graybeard",{"C":3,"W":2},(4,4),()))
        for index,(name,mana,stats,keywords) in enumerate(cases):
            with self.subTest(name=name):
                s,source,top,_=self.begin_printed_result(name,mana,2072100+index)
                self.finish_printed_result(s)
                self.assertEqual(stats,tuple(s.engine._numeric_stat(source.object_id,k) for k in ("power","toughness")))
                self.assertTrue(set(keywords)<=set(s.engine._combat_keywords(s.state.cards[source.object_id])))
                self.assertEqual(44 if name=="Wandering Graybeard" else 40,s.state.players["A"].life)
                self.assertEqual(top.object_id,s.state.players["A"].zones["library"][-1])
                self.replay(s,load=True)

    def test_actual_printed_controlled_flying_set_and_wolf_creation_results(self):
        def board(s):
            return (self.add(s.engine,"Grizzly Bears",ref="shared-friendly"),
                    self.add(s.engine,"Grizzly Bears",seat="B",ref="shared-foreign"))
        s,source,top,bodies=self.begin_printed_result("Waterspout Weavers",{"C":3,"U":2},2072110,setup=board)
        self.finish_printed_result(s)
        self.assertIn("flying",s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertIn("flying",s.engine._combat_keywords(s.state.cards[bodies[0].object_id]))
        self.assertNotIn("flying",s.engine._combat_keywords(s.state.cards[bodies[1].object_id]))
        self.replay(s,load=True)
        s,source,top,_=self.begin_printed_result("Wolf-Skull Shaman",{"C":1,"G":1},2072111)
        self.finish_printed_result(s)
        token,=[c for c in s.state.cards.values() if c.is_token and c.zone=="battlefield"]
        self.assertEqual(("A","A"),(token.owner,token.controller))
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,k) for k in ("power","toughness")))
        self.assertIn("wolf",s.engine._type_parts(s.engine._effective_card_data(token)["type_line"])[1])
        self.assertEqual(["G"],s.engine._effective_card_data(token)["colors"])
        self.replay(s,load=True)

    def test_actual_printed_opponent_life_loss_mill_and_discard_results(self):
        cases=(("Nightshade Schemers",{"C":4,"B":1}),
               ("Ink Dissolver",{"C":1,"U":1}),
               ("Squeaking Pie Grubfellows",{"C":3,"B":1}))
        for index,(name,mana) in enumerate(cases):
            with self.subTest(name=name):
                s=self.private_library_session(2072120+index,top_name=name)
                source=self.add(s.engine,name,zone="hand")
                self.assert_closed(s,source);self.prepare(s,mana=mana);self.checkpoint(s)
                self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
                before={seat:{zone:len(s.state.players[seat].zones[zone]) for zone in ("library","hand","graveyard")}
                        for seat in "ABCD"}
                self.choose(s,"look");self.choose(s,"reveal");self.finish_printed_result(s)
                for seat in "BCD":
                    if name=="Nightshade Schemers":self.assertEqual(38,s.state.players[seat].life)
                    elif name=="Ink Dissolver":
                        self.assertEqual(before[seat]["library"]-3,len(s.state.players[seat].zones["library"]))
                        self.assertEqual(before[seat]["graveyard"]+3,len(s.state.players[seat].zones["graveyard"]))
                    else:
                        self.assertEqual(before[seat]["hand"]-1,len(s.state.players[seat].zones["hand"]))
                        self.assertEqual(before[seat]["graveyard"]+1,len(s.state.players[seat].zones["graveyard"]))
                self.assertEqual(40,s.state.players["A"].life)
                self.replay(s,load=True)

    def test_actual_printed_whole_hand_reset_finishes_discard_before_four_draws(self):
        s=self.private_library_session(2072130,top_name="Sensation Gorger")
        source=self.add(s.engine,"Sensation Gorger",zone="hand")
        self.assert_closed(s,source);self.prepare(s,mana={"C":1,"R":2});self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
        old={seat:tuple(s.state.players[seat].zones["hand"]) for seat in "ABCD"}
        library={seat:len(s.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.choose(s,"look");self.choose(s,"reveal");self.finish_printed_result(s)
        for seat in "ABCD":
            self.assertEqual(4,len(s.state.players[seat].zones["hand"]))
            self.assertEqual(library[seat]-4,len(s.state.players[seat].zones["library"]))
            self.assertTrue(all(s.state.cards[oid].zone=="graveyard" for oid in old[seat]))
        self.replay(s,load=True)

    def test_actual_printed_fixed_damage_hits_every_creature_and_uses_original_source(self):
        def board(s):
            return (self.add(s.engine,"Grizzly Bears",ref="shared-damage-small"),
                    self.add(s.engine,"Colossal Dreadmaw",seat="B",ref="shared-damage-large"))
        s,source,top,bodies=self.begin_printed_result("Pyroclast Consul",{"C":3,"R":2},2072140,setup=board)
        self.finish_printed_result(s)
        self.assertEqual("graveyard",s.state.cards[bodies[0].object_id].zone)
        self.assertEqual(2,s.state.cards[bodies[1].object_id].marked_damage)
        self.assertEqual(2,s.state.cards[source.object_id].marked_damage)
        self.assertTrue(all(s.state.players[seat].life==40 for seat in "ABCD"))
        self.replay(s,load=True)


    def advance_to_kinship_trigger(self,s,source):
        for _ in range(400):
            s.engine.pump()
            if any(i.kind=="triggered_ability" and i.source_object_id==source.object_id for i in s.state.stack):return
            d=s.state.pending_decision;self.assertIsNotNone(d)
            if d.kind=="priority":command={"action_id":"pass"}
            elif d.kind=="combat.attackers":command={"action_id":"attack","attackers":{}}
            elif d.kind=="combat.blockers":command={"action_id":"block","blocks":{}}
            elif d.kind=="trigger.order":
                group=next(g for g in s.state.pending_trigger_batches[0].groups if g.controller==d.actors[0])
                command={"action_id":"order","triggers":[i.ref for i in group.items]}
            else:self.fail("Unexpected pre-kinship response step: "+d.kind)
            result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.fail("Upkeep trigger did not appear")

    def equipped_kinship(self,seed,*,equipment_owner="A"):
        from quorune.attachments import attach_objects
        s=self.private_library_session(seed,top_name="Grizzly Bears")
        source=self.add(s.engine,"Winnower Patrol")
        equipment=self.add(s.engine,"Runed Stalactite",seat=equipment_owner)
        self.assert_closed(s,source);self.assert_closed(s,equipment)
        attach_objects(s.state.cards,equipment,source,source_timestamp=equipment.zone_timestamp,players=s.state.players)
        return s,source,equipment

    def test_actual_source_uses_immediate_type_lki_and_counter_does_not_follow_bounced_incarnation(self):
        s,source,equipment=self.equipped_kinship(2072200)
        bounce=self.add(s.engine,"Unsummon",seat="B",zone="hand");self.add(s.engine,"Island",seat="B")
        self.prepare(s);self.checkpoint(s);self.advance_to_kinship_trigger(s,source)
        old=source.logical_object_id
        self.cast_for(s,"B",bounce,[source.ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual("hand",s.state.cards[source.object_id].zone)
        self.assertEqual("semantic.choice",self.resolve(s).kind)
        self.choose(s,"look")
        self.assertEqual("semantic.choice",s.state.pending_decision.kind)
        self.assertIn("reveal",s.packet("pilot:A",full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]["legal_values"])
        s=self.replay(s,load=True);self.choose(s,"reveal");self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.replay(s,load=True)

    def test_actual_equipment_removal_before_comparison_uses_current_types(self):
        s,source,equipment=self.equipped_kinship(2072201,equipment_owner="B")
        removal=self.add(s.engine,"Disenchant",seat="B",zone="hand")
        self.add(s.engine,"Plains",seat="B",ref="shared-removal-plains-1");self.add(s.engine,"Plains",seat="B",ref="shared-removal-plains-2")
        self.assert_closed(s,removal)
        self.prepare(s);self.checkpoint(s);self.advance_to_kinship_trigger(s,source)
        self.cast_for(s,"B",removal,[equipment.ref]);self.resolve_top(s)
        self.assertEqual("graveyard",s.state.cards[equipment.object_id].zone)
        self.assertEqual("semantic.choice",self.resolve(s).kind)
        self.choose(s,"look")
        before=authoritative_state_hash(s.state)
        rejected=s.act("pilot:B",{"action_id":"concede","choices":{"confirm_concede":True}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        values=s.packet("pilot:A",full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]["legal_values"]
        self.assertEqual(["decline"],values)
        self.choose(s,"decline");self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.replay(s,load=True)

    def test_actual_empty_library_has_no_private_card_result_and_already_revealed_card_stays_optional(self):
        from quorune.semantic_runtime.intents import RevealLibraryCardsIntent
        s=self.private_library_session(2072210)
        for oid in tuple(s.state.players["A"].zones["library"]):
            s.engine.move_card(oid,"exile",reason="Canonical empty-library fixture",log=False)
        source=self.add(s.engine,"Winnower Patrol",zone="hand")
        self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_kinship_trigger(s,source)
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[source.object_id].counters)
        self.replay(s,load=True)
        for reveal in (False,True):
            with self.subTest(reveal=reveal):
                s=self.private_library_session(2072211+reveal)
                source=self.add(s.engine,"Winnower Patrol",zone="hand")
                top=s.state.cards[s.state.players["A"].zones["library"][-1]]
                s.engine.reveal_library_cards_intent(RevealLibraryCardsIntent("A","A","A",(top.ref,),"Canonical already-public top",public=True))
                self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
                self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
                self.choose(s,"look");self.choose(s,"reveal" if reveal else "decline");self.assertIsNone(self.resolve(s))
                self.assertEqual({"+1/+1":1} if reveal else {},s.state.cards[source.object_id].counters)
                self.replay(s,load=True)




    def pending_patrol(self,seed):
        s=self.private_library_session(seed)
        source=self.add(s.engine,"Winnower Patrol",zone="hand")
        self.prepare(s,mana={"C":2,"G":1});self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s));self.advance_to_look(s,source)
        return s,source

    def test_actual_private_and_public_reveal_faults_restore_full_state_then_load_retry(self):
        from quorune.errors import GameRuleError
        for public in (False,True):
            with self.subTest(public=public):
                s,source=self.pending_patrol(2072300+public)
                if public:self.choose(s,"look")
                original=s.engine.reveal_library_cards_intent;seen=[]
                def fault(intent,*args,**kwargs):
                    result=original(intent,*args,**kwargs)
                    if intent.public==public:
                        seen.extend(result)
                        raise GameRuleError("Injected shared-type reveal fault")
                    return result
                before=authoritative_state_hash(s.state)
                choice="reveal" if public else "look"
                with patch.object(s.engine,"reveal_library_cards_intent",side_effect=fault):
                    rejected=s.act("pilot:A",{"action_id":"choose","choice":choice})
                self.assertFalse(rejected.ok);self.assertIn("Injected shared-type",rejected.summary)
                self.assertEqual(1,len(seen));self.assertEqual(before,authoritative_state_hash(s.state))
                s=self.replay(s,load=True);self.choose(s,choice)
                if not public:self.choose(s,"reveal")
                self.assertIsNone(self.resolve(s));self.assertEqual({"+1/+1":1},s.state.cards[source.object_id].counters)
                self.replay(s,load=True)

    def test_fresh_native_wrong_private_viewer_mutant_is_killed_after_paid_original(self):
        from quorune.engine import CommanderEngine
        from dataclasses import replace
        method="test_actual_paid_patrol_private_look_optional_public_reveal_counter_pending_load_and_replay"
        baseline=unittest.TestResult();type(self)(method).run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=CommanderEngine.reveal_library_cards_intent;seen=[]
        def wrong(engine,intent,*args,**kwargs):
            if not intent.public:
                seen.append(intent.viewer);intent=replace(intent,viewer="B")
            return original(engine,intent,*args,**kwargs)
        changed=unittest.TestResult()
        with patch.object(CommanderEngine,"reveal_library_cards_intent",wrong):type(self)(method).run(changed)
        self.assertEqual(["A"],seen);self.assertEqual([],changed.errors);self.assertEqual(1,len(changed.failures))

class SharedTypeLibraryWithdrawalTests(SharedTypeLibraryNativeFixture,unittest.TestCase):
    def test_genuinely_verified_fine_source_and_independent_result_owners(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/"quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        doc=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            return registry
        baseline=verified(raw)
        names=("Pyroclast Consul","Winnower Patrol","Sensation Gorger","Waterspout Weavers",
               "Ink Dissolver","Mudbutton Clanger","Squeaking Pie Grubfellows","Wolf-Skull Shaman",
               "Wandering Graybeard","Kithkin Zephyrnaut","Nightshade Schemers")
        dependencies={}
        for name in names:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=baseline,capability_profile="commander_review")
            self.assertEqual("exact",ir.status,(name,ir.material_residuals))
            dependencies[name]=tuple(c for f in ir.faces for n in f.nodes for c in n.capability_dependencies)
        cases=(
            ("library.top.shared_type_optional_reveal",names,None),
            ("target.ability_source.current_or_lki",names,None),
            ("counter.producer.fixed_effect",("Winnower Patrol",),"Wandering Graybeard"),
            ("life.change.effect",("Wandering Graybeard","Nightshade Schemers"),"Winnower Patrol"),
            ("token.creation.fixed_definition",("Wolf-Skull Shaman",),"Wandering Graybeard"),
            ("zone.discard.whole_hand",("Sensation Gorger",),"Winnower Patrol"),
            ("zone.draw.library_to_hand",("Sensation Gorger",),"Wolf-Skull Shaman"),
        )
        for cap,blocked,unrelated in cases:
            with self.subTest(cap=cap):
                changed=deepcopy(raw)
                next(r for r in changed["capabilities"] if r["id"]==cap).update(
                    status="blocked",blockers=["focused shared-type library withdrawal"])
                registry=verified(changed)
                for name in blocked:
                    self.assertIn(cap,dependencies[name])
                    self.assertNotEqual("exact",compile_oracle_card(self.db.lookup(name),capability_registry=registry).status)
                    self.assertIn("status:"+cap+":blocked",registry.closure(dependencies[name],profile="commander_review").blockers)
                if unrelated:
                    self.assertEqual("exact",compile_oracle_card(self.db.lookup(unrelated),capability_registry=registry).status)
                self.assertEqual("exact",compile_oracle_card(self.db.lookup("Grizzly Bears"),capability_registry=registry).status)
