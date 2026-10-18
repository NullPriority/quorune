from __future__ import annotations

import json,unittest
from common import ROOT
from quorune.conditional_copy_model import ConditionalCopyException
from quorune.token_copy_recipes import TokenCopyExceptionSpec,TokenCopyRecipeSpec
from quorune.entry_copy import EntryCopySpec
from quorune.compiler.entry_copy_templates import entry_copy_spec


class ConditionalCopyModelTests(unittest.TestCase):
    def test_conditional_exception_and_creation_origin_require_explicit_closed_versions(self):
        value=ConditionalCopyException();self.assertEqual(value,ConditionalCopyException.from_dict(json.loads(json.dumps(value.to_dict()))))
        for changed in ({'schema_version':True},{'condition':'token_is_white'},{'extra':1}):
            with self.assertRaises(ValueError):ConditionalCopyException.from_dict({**value.to_dict(),**changed})
        legacy=TokenCopyRecipeSpec('source').to_dict();self.assertNotIn('creation_origin',legacy)
        recipe=TokenCopyRecipeSpec('source',schema_version=2,creation_origin='embalm')
        self.assertEqual(recipe,TokenCopyRecipeSpec.from_dict(json.loads(json.dumps(recipe.to_dict()))))
        for changes in ({'creation_origin':'token'},{'schema_version':1},{'extra':True}):
            with self.assertRaises(ValueError):TokenCopyRecipeSpec.from_dict({**recipe.to_dict(),**changes})

    def test_vizier_conditional_copy_is_closed_without_inference_from_generic_token(self):
        text="You may have this creature enter as a copy of any creature on the battlefield, except if this creature was embalmed, the token has no mana cost, it's white, and it's a Zombie in addition to its other types."
        spec=entry_copy_spec(text,source_name='Generic Copier');self.assertIsNotNone(spec)
        self.assertEqual(5,spec.schema_version);self.assertEqual(spec,EntryCopySpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        self.assertEqual(('W',),spec.exception.colors);self.assertTrue(spec.exception.remove_mana_cost)
        self.assertIsNone(entry_copy_spec(text.replace('was embalmed','is a token'),source_name='Generic Copier'))
        self.assertNotIn('conditional_exception',EntryCopySpec(spec.selection).to_dict())

    def test_conditional_value_omission_changes_independent_copiable_result(self):
        from unittest.mock import patch
        from quorune.token_copy_recipes import apply_copy_exception
        source={'name':'Body','type_line':'Creature — Elf','colors':['G'],'mana_cost':'{1}{G}','mana_value':2,'power':'1','toughness':'1','keywords':[],'ability_fragments':[]}
        actual=apply_copy_exception(source,TokenCopyExceptionSpec(schema_version=2,colors=('W',),add_subtypes=('zombie',),remove_mana_cost=True))
        self.assertEqual(('',0,['W']),(actual['mana_cost'],actual['mana_value'],actual['colors']))
        self.assertIn('Zombie',actual['type_line'])
        with patch('quorune.token_copy_recipes.apply_copy_exception',side_effect=lambda values,spec:dict(values)),self.assertRaises(AssertionError):
            import quorune.token_copy_recipes as owner
            self.assertEqual(0,owner.apply_copy_exception(source,TokenCopyExceptionSpec(schema_version=2,remove_mana_cost=True))['mana_value'])


import test_entry_copy_values as values


class ConditionalCopyCompilerBoundaryTests(unittest.TestCase):
    def test_conditional_and_changeling_dependencies_and_producer_omissions_fail_closed(self):
        from copy import deepcopy
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from test_qualified_zone_event_queries import query_record
        texts=[("You may have this creature enter as a copy of any creature on the battlefield, except if this creature was embalmed, the token has no mana cost, it's white, and it's a Zombie in addition to its other types.",'zone.entry.conditional_copy_exception'),
            ('You may have this creature enter as a copy of any creature on the battlefield, except it has changeling.','continuous.characteristics.changeling')]
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));caps=load_default_capability_registry()
        for text,cap in texts:
            record=query_record(text);self.assertEqual('exact',compile_oracle_card(record,capability_registry=caps).status)
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['omitted conditional/CDA authority'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(changed)).status)
        with patch('quorune.compiler.entry_copy_templates.entry_copy_spec',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(query_record(texts[0][0]),capability_registry=caps).status)

    def test_generic_copy_recipe_cannot_acquire_embalm_origin_without_its_owner(self):
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        from quorune.semantic_runtime.graveyard_copy_keyword import GRAVEYARD_COPY_KEYWORD_CAPABILITY
        from quorune.compiler.graveyard_copy_keyword_nodes import graveyard_copy_keyword_node
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_qualified_zone_event_queries import query_record
        from dataclasses import replace
        ir=compile_oracle_card(replace(query_record('Embalm {W}'),keywords=('Embalm',)),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status);node,=ir.faces[0].nodes
        required=fixed_token_creation_node_capabilities(effects=node.effects,target_schema=node.target_schema,mechanic_ids=node.mechanics)
        self.assertIn(GRAVEYARD_COPY_KEYWORD_CAPABILITY,required)


class ConditionalCopyRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=values.EntryCopyValueRuntimeTests.session
    add=values.EntryCopyValueRuntimeTests.add
    prepare=values.EntryCopyValueRuntimeTests.prepare
    cast=values.EntryCopyValueRuntimeTests.cast
    resolve=values.EntryCopyValueRuntimeTests.resolve
    replay=values.EntryCopyValueRuntimeTests.replay
    checkpoint=values.EntryCopyValueRuntimeTests.checkpoint
    choose_copy=values.EntryCopyValueRuntimeTests.choose_copy
    assert_closed=values.EntryCopyValueRuntimeTests.assert_closed
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'conditional-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/conditional-copy-value-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Conditional copy',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_ordinary_vizier_copies_without_inventing_embalm_values(self):
        s=self.session(707250);e=s.engine
        target=self.add(e,'Elvish Visionary');source=self.add(e,'Vizier of Many Faces',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'U':5,'C':5});self.checkpoint(s)
        before=len(s.state.players['A'].zones['hand'])
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertEqual('{1}{G}',data['mana_cost']);self.assertEqual(2,data['mana_value']);self.assertEqual(['G'],data['colors'])
        self.assertNotIn('zombie',s.engine._type_parts(data['type_line'])[1]);self.assertEqual(before,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)

    def test_embalmed_vizier_conditional_values_survive_entry_and_are_copiable_without_status(self):
        from quorune.conditional_copy_model import EMBALMED_DESIGNATION
        s=self.session(707251);e=s.engine
        target=self.add(e,'Elvish Visionary');source=self.add(e,'Vizier of Many Faces',zone='graveyard')
        clone=self.add(e,'Clone',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'U':7,'C':8});self.checkpoint(s)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        s=self.choose_copy(s,target)
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));token=tokens[0];data=s.engine._effective_card_data(token)
        self.assertEqual(('',0,['W']),(data['mana_cost'],data['mana_value'],data['colors']))
        self.assertIn('zombie',s.engine._type_parts(data['type_line'])[1]);self.assertIs(True,token.annotations[EMBALMED_DESIGNATION])
        self.replay(s,load=True)
        self.cast(s,'A',clone);s=self.choose_copy(s,token)
        copied=s.state.cards[clone.object_id];data=s.engine._effective_card_data(copied)
        self.assertEqual(('',0,['W']),(data['mana_cost'],data['mana_value'],data['colors']))
        self.assertIn('zombie',s.engine._type_parts(data['type_line'])[1]);self.assertNotIn(EMBALMED_DESIGNATION,copied.annotations)
        self.replay(s,load=True)

    def test_added_changeling_is_copiable_and_layer_four_survives_humble(self):
        s=self.session(707252);e=s.engine
        target=self.add(e,'Elvish Visionary');source=self.add(e,'Omni-Changeling',zone='hand')
        clone=self.add(e,'Clone',zone='hand');humble=self.add(e,'Humble',zone='hand')
        self.assert_closed(s,source);self.prepare(s,mana={'U':8,'G':8,'W':3,'C':15});self.checkpoint(s)
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        from quorune.creature_subtypes import CREATURE_SUBTYPES
        subtypes=s.engine._type_parts(s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'])[1]
        self.assertLessEqual(CREATURE_SUBTYPES,subtypes)
        self.cast(s,'A',clone);s=self.choose_copy(s,s.state.cards[source.object_id])
        self.cast(s,'A',humble,[clone.ref]);self.assertIsNone(self.resolve(s))
        data=s.engine._effective_card_data(s.state.cards[clone.object_id])
        self.assertLessEqual(CREATURE_SUBTYPES,s.engine._type_parts(data['type_line'])[1])
        self.assertFalse(s.engine._combat_keywords(s.state.cards[clone.object_id]));self.replay(s,load=True)


    def test_added_changeling_changes_subtype_layers_but_own_convoke_is_not_retained(self):
        from quorune.creature_subtypes import CREATURE_SUBTYPES
        s=self.session(707253);e=s.engine
        target=self.add(e,'Elvish Visionary');source=self.add(e,'Omni-Changeling',zone='hand')
        private=self.add(e,'Generic Bound Body',seat='C',zone='hand',ref='private-conditional-copy-hand')
        self.prepare(s,mana={'U':4,'G':6,'C':12});self.checkpoint(s)
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        current=s.state.cards[source.object_id];data=s.engine._effective_card_data(current)
        self.assertNotIn('convoke',{v.casefold() for v in data['keywords']})
        self.assertLessEqual(CREATURE_SUBTYPES,s.engine._type_parts(data['type_line'])[1])
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_conditional_and_changeling_value_omissions_fail_native_results(self):
        from unittest.mock import patch
        from dataclasses import replace
        import quorune.semantic_runtime.entry_copy as owner
        original=owner.apply_copy_exception
        def omit(values,spec):return original(values,replace(spec,remove_mana_cost=False,colors=None,add_subtypes=()))
        method='test_embalmed_vizier_conditional_values_survive_entry_and_are_copiable_without_status'
        with patch.object(owner,'apply_copy_exception',side_effect=omit),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()
        def omit_cda(values,spec):return original(values,replace(spec,add_ability_fragments=()))
        method='test_added_changeling_is_copiable_and_layer_four_survives_humble'
        with patch.object(owner,'apply_copy_exception',side_effect=omit_cda),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


    def test_both_originals_have_complete_children_and_native_origin_is_not_copied(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Vizier of Many Faces','Omni-Changeling'):
            c=self.db.lookup(name);p=compile_best_available_card_program(self.db,c,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            with self.subTest(card=name):self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])


    def test_generic_token_status_alone_does_not_enable_conditional_exception(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.semantic_runtime.entry_copy import _copiable_values
        from quorune.conditional_copy_model import EMBALMED_DESIGNATION
        s=self.session(707254);e=s.engine
        target=self.add(e,'Elvish Visionary')
        entering=self.add(e,'Vizier of Many Faces',zone='hand')
        entering.is_token=True
        text="You may have this creature enter as a copy of any creature on the battlefield, except if this creature was embalmed, the token has no mana cost, it's white, and it's a Zombie in addition to its other types."
        spec=entry_copy_spec(text,source_name='Vizier of Many Faces')
        values=_copiable_values(e,target,spec,entering=entering)
        self.assertEqual(('{1}{G}',2,['G']),(values['mana_cost'],values['mana_value'],values['colors']))
        self.assertNotIn('zombie',e._type_parts(values['type_line'])[1])
        entering.annotations[EMBALMED_DESIGNATION]=True
        values=_copiable_values(e,target,spec,entering=entering)
        self.assertEqual(('',0,['W']),(values['mana_cost'],values['mana_value'],values['colors']))
