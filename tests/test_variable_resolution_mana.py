"""CR106/608.2h: sealed variable mana allocation during resolution."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record

CAPABILITY='mana.production.variable_resolution_choice'


class VariableResolutionManaCompilerTests(unittest.TestCase):
    def test_verified_variable_choice_withdrawal_keeps_fixed_choice(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Variable resolution choice withdrawn'])
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Add X mana of any one color, where X is the number of Shrines you control.',False),('Add two mana in any combination of colors.',True)):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact')

    def test_existing_public_amounts_lower_one_color_and_printed_color_combinations(self):
        for text in ('Add X mana of any one color, where X is the number of Shrines you control.',
                'Add X mana in any combination of {R} and/or {G}, where X is the number of creatures you control.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual('choose_mana',node.effects[0]['op'])
                self.assertEqual(3,node.effects[0]['schema_version']);self.assertIn(CAPABILITY,node.capability_dependencies)

    def test_unrepresented_restriction_retention_and_quantity_do_not_widen(self):
        for text in ('Add X mana of any one color, where X is the remembered number.',
                'Add X mana of any one color, where X is the number of creatures you control. Spend this mana only to cast remembered spells.',
                "Add X mana in any combination of colors, where X is the number of creatures you control. Until end of turn, you don't lose this mana as steps and phases end."):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)


class VariableResolutionManaModelTests(unittest.TestCase):
    def test_omitting_printed_color_restriction_is_killed(self):
        from unittest.mock import patch
        from quorune.semantic_choices import resolution_mana as owner
        original=owner._validated
        def omit(effect,query,**kwargs):
            spec,actor=original(effect,query,**kwargs)
            return replace(spec,colors=tuple(sorted('WUBRG'))),actor
        method='test_sealed_large_allocation_rejects_wrong_color_total_and_chooser'
        baseline=unittest.TestResult();VariableResolutionManaModelTests(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch.object(owner,'_validated',side_effect=omit):
            result=unittest.TestResult();VariableResolutionManaModelTests(method).run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def expression(self):
        from quorune.compiler.public_quantity_mana_effects import public_quantity_mana_effect_template
        return public_quantity_mana_effect_template('Add X mana in any combination of {R} and/or {G}, where X is the number of creatures you control.',source_name='Generic')[1][0]

    def test_version_two_encoding_and_fixed_limit_stay_unchanged(self):
        from quorune.resolution_mana_model import ResolutionManaChoiceSpec
        from quorune.semantic_choices.model import ManaBundleChoice
        self.assertEqual({'op':'choose_mana','schema_version':2,'player':'$controller','source':'$source','selection':'combination','amount':2},ResolutionManaChoiceSpec('combination',2).effect())
        with self.assertRaises(ValueError):ResolutionManaChoiceSpec('combination',11)
        with self.assertRaises(ValueError):ManaBundleChoice('mana',('R','G'),11)
        self.assertEqual(27,ManaBundleChoice('mana',('R','G'),27,schema_version=2).choice_schema()['total'])

    def test_expression_and_sealed_amount_codecs_are_distinct_and_closed(self):
        from quorune.variable_resolution_mana_model import VariableResolutionManaChoiceSpec
        raw=self.expression();spec=VariableResolutionManaChoiceSpec.from_effect(raw)
        self.assertEqual(raw,spec.effect());self.assertEqual(('G','R'),spec.colors)
        resolved={**raw,'amount':27};self.assertEqual(27,VariableResolutionManaChoiceSpec.from_effect(resolved,resolved=True).amount)
        for delta in ({'amount':True},{'colors':['C']},{'colors':'GR'},{'schema_version':True},{'amount':-1},{'extra':1}):
            with self.assertRaises((TypeError,ValueError)):VariableResolutionManaChoiceSpec.from_effect({**resolved,**delta},resolved=True)
        with self.assertRaises(ValueError):VariableResolutionManaChoiceSpec.from_effect(resolved)
        with self.assertRaises(ValueError):VariableResolutionManaChoiceSpec.from_effect(raw,resolved=True)

    def test_sealed_large_allocation_rejects_wrong_color_total_and_chooser(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.resolution_mana import prepare_resolution_mana,complete_resolution_mana
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.model import SemanticChoiceError
        effect={**self.expression(),'player':'A','source':'source1','amount':27}
        query=SimpleNamespace(active_seats=tuple('ABCD'))
        context=SimpleNamespace(actor='A',source_ref='source1',stack_ref='S1',query=query)
        prepared=prepare_resolution_mana(effect,context)
        continuation=SimpleNamespace(effect=prepared.continuation_effect)
        completion=complete_resolution_mana(continuation,{'action_id':'choose','mana':{'G':20,'R':7}},query)
        self.assertEqual({'G':20,'R':7},{intent.color:intent.amount for intent in completion.intents})
        for response in ({'mana':{'U':27}},{'mana':{'G':26}},{'mana':{'G':True,'R':26}}):
            with self.assertRaises(SemanticChoiceError):complete_resolution_mana(continuation,response,query)
        with self.assertRaises(SemanticChoiceError):prepare_resolution_mana(effect,SimpleNamespace(actor='B',source_ref='source1',query=query))
        zero=prepare_resolution_mana({**effect,'amount':0},context)
        self.assertIsNone(zero.request);self.assertIsNotNone(zero.auto_continue)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class VariableResolutionManaNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'variable-resolution-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/variable-resolution-mana-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Variable resolution mana',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def test_actual_shrine_trigger_counts_at_resolution_and_seals_pending_one_color(self):
        from quorune.turn_step_owner import TURN_STEPS
        s=self.session(106000201)
        source=self.add(s.engine,'Sanctum of Fruitful Harvest')
        shrine=self.add(s.engine,'Honden of Infinite Rage')
        removal=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        lands=[self.add(s.engine,'Plains',seat='B',ref='mana-choice-plains-'+str(i)) for i in range(2)]
        self.assert_closed(s,source);self.assert_closed(s,removal)
        self.prepare(s,mana={});s.engine.permissions.invalidate_current();s.state.pending_decision=None
        s.state.phase_index=TURN_STEPS.index(('beginning','draw'));s.state.phase='beginning';s.state.step='draw'
        s.engine._grant_priority('A');s.engine.pump();self.checkpoint(s)
        self.until(s,lambda current:current.state.phase=='precombat_main' and bool(current.state.stack))
        self.priority(s,'B')
        for land in lands:
            actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
            self.assertTrue(s.act('pilot:B',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'B',removal,[shrine.ref]);self.resolve_top(s)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(1,ctx['amount'])
        self.reject(s,'B',{'action_id':'choose','choice':'G'})
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:A',{'action_id':'choose','choice':'G'}).ok)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.players['A'].mana_pool.get('G',0))
        self.replay(s,load=True)

    def test_generated_combination_retains_subset_amount_private_projection_and_replay(self):
        s=self.session(106000202)
        spell=self.add(s.engine,'Generic Public Mana Surge',zone='hand')
        for i in range(12):self.add(s.engine,'Memnite',ref='mana-quantity-'+str(i))
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'C':1});self.checkpoint(s)
        self.cast(s,'A',spell,[]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'B',{'action_id':'choose','mana':{'R':6,'G':6}})
        for bundle in ({'U':12},{'R':11},{'R':True,'G':11}):self.reject(s,'A',{'action_id':'choose','mana':bundle})
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:A',{'action_id':'choose','mana':{'R':5,'G':7}}).ok)
        self.assertIsNone(self.resolve(s));self.assertEqual((5,7),(s.state.players['A'].mana_pool.get('R',0),s.state.players['A'].mana_pool.get('G',0)))
        self.replay(s,load=True)

    def test_generated_zero_quantity_auto_continues_without_choice_or_mana(self):
        s=self.session(106000203);spell=self.add(s.engine,'Generic Public Mana Surge',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'C':1});self.checkpoint(s)
        self.cast(s,'A',spell,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertFalse(s.state.stack)
        self.replay(s,load=True)
