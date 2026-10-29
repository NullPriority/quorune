"""Champion's optional public choice retains exact original identities."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from quorune.compiler.champion_templates import champion_spec
from quorune.replacement.immutable import FrozenMap
from quorune.object_query import ObjectQueryResult
from quorune.semantic_choices.champion import ChampionExileChoiceHandler, ChampionReturnChoiceHandler
from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_runtime.champion_intent import ChampionExileIntent, ReturnLinkedAbilityExileIntent


class ChampionChoiceContractTests(unittest.TestCase):
    def row(self, ref, *, types=('creature',),subtypes=('kithkin',),owner='B',controller='A'):
        return ObjectQueryResult('object:'+ref,ref,'Choice permanent',owner,controller,'battlefield',
            types=types,subtypes=subtypes,logical_object_id='object:'+ref+':0')

    def context(self, rows=()):
        return SemanticChoiceContext(actor='A',stack_ref='S1',stack_controller='A',stack_label='Champion entry',
            source_ref='source',card_ref=None,semantic_program_id='unit:champion',semantic_program_version=1,
            source_object_id='object:source',source_logical_object_id='object:source:0',
            query=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),object_rows=tuple(rows)))

    def effect(self):
        return FrozenMap({'op':'choose_champion_exile','ability_pair':'pair:champion',
                          'spec':champion_spec('Champion a Kithkin').to_dict()})

    def test_choice_is_optional_untargeted_controlled_and_not_owned(self):
        own_source=self.row('source');borrowed=self.row('borrowed',owner='B')
        kindred=self.row('kindred',types=('kindred','enchantment'))
        enemy=self.row('enemy',controller='B',owner='A')
        prepared=ChampionExileChoiceHandler().prepare(self.effect(),self.context((own_source,enemy,kindred,borrowed)))
        self.assertEqual(('borrowed','kindred'),prepared.request.choice.legal_refs)
        self.assertEqual((0,1),(prepared.request.choice.minimum,prepared.request.choice.maximum))
        self.assertEqual('any',prepared.request.choice.owner_relation)
        self.assertEqual((),prepared.preparation_intents)
        completion=ChampionExileChoiceHandler().complete(SimpleNamespace(effect=prepared.continuation_effect),
            {'cards':[borrowed.ref]},self.context((borrowed,kindred)).query)
        intent=completion.intents[0]
        self.assertIsInstance(intent,ChampionExileIntent)
        self.assertEqual(borrowed.object_id,intent.subject.object_id)
        self.assertEqual('A',intent.actor)
        self.assertEqual('object:source:0',intent.source.logical_object_id)

    def test_decline_and_impossible_choice_keep_original_source_identity(self):
        handler=ChampionExileChoiceHandler();context=self.context()
        prepared=handler.prepare(self.effect(),context)
        self.assertIsNone(prepared.request)
        self.assertIsNone(prepared.preparation_intents[0].subject)
        borrower=self.row('borrowed')
        offered=handler.prepare(self.effect(),self.context((borrower,)))
        completion=handler.complete(SimpleNamespace(effect=offered.continuation_effect),
            {'cards':[]},self.context().query)
        self.assertIsNone(completion.intents[0].subject)
        self.assertEqual(prepared.preparation_intents[0].source,completion.intents[0].source)

    def test_stale_zone_control_and_unoffered_references_reject(self):
        handler=ChampionExileChoiceHandler();borrower=self.row('borrowed')
        prepared=handler.prepare(self.effect(),self.context((borrower,)))
        continuation=SimpleNamespace(effect=prepared.continuation_effect)
        for changed in (replace(borrower,logical_object_id='object:borrowed:2'),
                        replace(borrower,controller='B'),replace(borrower,phased_out=True),
                        replace(borrower,zone='exile')):
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation,
                {'cards':['borrowed']},self.context((changed,)).query)
        for response in ({'cards':['unknown']},{'cards':['borrowed','borrowed']},{'cards':True},{'cards':[],'unknown':1}):
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation,response,self.context((borrower,)).query)

    def test_return_resolves_without_live_source_or_a_player_choice(self):
        context=self.context();handler=ChampionReturnChoiceHandler()
        prepared=handler.prepare(FrozenMap({'op':handler.operation,'ability_pair':'pair:champion'}),context)
        self.assertIsNone(prepared.request)
        self.assertIsInstance(prepared.preparation_intents[0],ReturnLinkedAbilityExileIntent)
        self.assertEqual('object:source:0',prepared.preparation_intents[0].source.logical_object_id)
        with self.assertRaises(SemanticChoiceError):handler.complete(None,{},context.query)

    def test_malformed_instruction_and_retained_actor_fail_before_mutation(self):
        handler=ChampionExileChoiceHandler();context=self.context((self.row('borrowed'),))
        for changed in ({'unknown':1},{'ability_pair':''},{'ability_pair':' pair:champion '},
                        {'spec':{'schema_version':2}}):
            with self.assertRaises(SemanticChoiceError):handler.prepare(FrozenMap({**self.effect(),**changed}),context)
        prepared=handler.prepare(self.effect(),context)
        with self.assertRaises(SemanticChoiceError):handler.prepare(
            FrozenMap({**prepared.continuation_effect,'_champion_actor':'B'}),context)

    def test_decline_cannot_sacrifice_a_new_incarnation_or_another_controller(self):
        from unittest.mock import Mock
        from quorune.champion_state import sacrifice_champion_source_if_controlled
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        source=ContinuousObjectIdentity('source','source:0')
        card=SimpleNamespace(zone='battlefield',phased_out=False,
            logical_object_id='source:0',controller='A',ref='source-ref')
        host=SimpleNamespace(state=SimpleNamespace(cards={'source':card}),apply_effect=Mock())
        for changed in ({'controller':'B'},{'logical_object_id':'source:2'},
                        {'zone':'hand'},{'phased_out':True}):
            original={key:getattr(card,key) for key in changed}
            for key,value in changed.items():setattr(card,key,value)
            self.assertFalse(sacrifice_champion_source_if_controlled(host,
                source=source,actor='A',reason='resolving declined entry'))
            host.apply_effect.assert_not_called()
            for key,value in original.items():setattr(card,key,value)
        self.assertTrue(sacrifice_champion_source_if_controlled(host,
            source=source,actor='A',reason='resolving declined entry'))
        host.apply_effect.assert_called_once_with(
            {'op':'sacrifice','card':'source-ref','reason':'resolving declined entry'},actor='A')

    def test_replacement_identities_are_closed_and_selections_keep_the_original_binding(self):
        from quorune.semantic_choices.intent_replacement import (
            semantic_intent_identity, validate_semantic_intent_identity, with_replacement_selections,
        )
        source=self.context().source_object_id
        handler=ChampionExileChoiceHandler()
        prepared=handler.prepare(self.effect(),self.context((self.row('borrowed'),)))
        exile=handler.complete(SimpleNamespace(effect=prepared.continuation_effect),
            {'cards':['borrowed']},self.context((self.row('borrowed'),)).query).intents[0]
        returned=ChampionReturnChoiceHandler().prepare(FrozenMap({
            'op':'return_champion_exile','ability_pair':'pair:champion'}),self.context()).preparation_intents[0]
        for intent in (exile,returned):
            kind,identity=semantic_intent_identity(intent)
            self.assertEqual(source,identity['source']['object_id'])
            self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
            resumed=with_replacement_selections(intent,({'effect_id':'replacement-one'},))
            self.assertEqual((kind,identity),semantic_intent_identity(resumed))
            self.assertIsInstance(resumed.replacement_selections[0],FrozenMap)
            for changed in ({**identity,'unknown':True},{key:value for key,value in identity.items() if key!='source'}):
                with self.assertRaises(SemanticChoiceError):validate_semantic_intent_identity(kind,changed)
