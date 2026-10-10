from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class StaticCharacteristicLabelCompilerTests(unittest.TestCase):
    def test_decorative_static_labels_preserve_descriptor_and_original_source_span(self):
        registry=load_default_capability_registry()
        for label,body in (
            ('Protector','Other artifact creatures you control have hexproof.'),
            ('Unquestionable Wisdom','Other creatures you control get +1/+0 and have vigilance.'),
            ('Domain','Enchanted creature gets +1/+1 for each basic land type among lands you control.'),
        ):
            plain=compile_oracle_card(query_record(body),capability_registry=registry)
            text=label+' — '+body
            decorated=compile_oracle_card(query_record(text),capability_registry=registry)
            self.assertEqual('exact',decorated.status,decorated.material_residuals)
            a,b=plain.faces[0].nodes[0],decorated.faces[0].nodes[0]
            self.assertEqual(a.handlers,b.handlers)
            self.assertEqual(a.capability_dependencies,b.capability_dependencies)
            self.assertEqual((0,len(text)),(b.span.start,b.span.end))
            self.assertEqual(text,b.text)

    def test_semantic_markers_and_unrepresented_labeled_body_stay_residual(self):
        from quorune.compiler.runtime_templates import static_runtime_template
        registry=load_default_capability_registry()
        for text in ('Solved — Creatures you control get +1/+1.',
                     'Solve — Creatures you control have flying.',
                     'III — Creatures you control get +1/+1.',
                     '• Sultai — Creatures you control get +1/+1.',
                     'Protector — Creatures you control have an unrepresented ability.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        self.assertIsNone(static_runtime_template('Protector — Creatures you control have flying.',source_name='Class witness',source_is_class=True))

    def test_static_label_dispatch_omission_mutant_is_killed(self):
        import quorune.compiler.runtime_templates as owner
        original=owner._continuous_static_runtime_template
        def omitted(text,**kwargs):
            return None if ' — ' in text else original(text,**kwargs)
        with patch.object(owner,'_continuous_static_runtime_template',side_effect=omitted):
            with self.assertRaises(AssertionError):
                self.test_decorative_static_labels_preserve_descriptor_and_original_source_span()

    def test_werewolf_plural_union_is_typed_and_deduplicates_recipient_query(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        relation,query,other=fixed_characteristic_battlefield_query_subject('Other Wolves and Werewolves you control')
        self.assertEqual('source_controller',relation);self.assertTrue(other)
        self.assertEqual(('werewolf','wolf'),query.subtypes_any)
        self.assertEqual(('creature',),query.types_all)
        self.assertIsNone(fixed_characteristic_battlefield_query_subject('Other Wolves and Boguses you control'))

    def test_original_werewolf_union_with_unrepresented_sibling_rejects_whole_card_admission(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'boundary.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/static-characteristic-labels.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry();record=db.lookup('Nightpack Ambusher')
                program=compile_best_available_card_program(db,record,semantic_registry=SemanticRegistry(),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertTrue(any(a.template_id=='continuous-fixed-query-anthem-v2'
                                    for face in compile_oracle_card(record,capability_registry=registry).faces for a in face.nodes))
                binding=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                self.assertFalse(binding['strict_capability_ready'])
                self.assertIn('trust_basis:unresolved',binding['blockers'])


class StaticCharacteristicLabelRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'labels.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/static-characteristic-labels.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static labels',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_cryptothrall_cast_grants_current_other_artifacts_and_replays(self):
        session=self.session(298001);engine=session.engine
        body=self.add(engine,'Generic Static Label Artifact Creature')
        foreign=self.add(engine,'Generic Static Label Artifact Creature',seat='B',ref='foreign-artifact')
        ordinary=self.add(engine,'Generic Bound Body')
        source=self.add(engine,'Cryptothrall',zone='hand')
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.lookup('Cryptothrall'),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        spell=self.add(engine,'Unsummon',zone='hand')
        action=self.ready(session,source,{'C':4,'U':1});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertIn('hexproof',engine._combat_keywords(body))
        for card in (source,foreign,ordinary):self.assertNotIn('hexproof',engine._combat_keywords(card))
        self.replay(session,load=True)
        action=self.ready(session,spell,{'U':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertNotIn('hexproof',engine._combat_keywords(body))
        self.replay(session,load=True)

    def test_actual_nightpack_cast_union_counts_each_current_creature_once_and_replays(self):
        session=self.session(298002);engine=session.engine
        wolf=self.add(engine,'Generic Static Label Wolf')
        werewolf=self.add(engine,'Generic Static Label Werewolf')
        both=self.add(engine,'Generic Static Label Wolf Werewolf')
        enemy=self.add(engine,'Generic Static Label Wolf Werewolf',seat='B',ref='enemy-both')
        source=self.add(engine,'Nightpack Ambusher',zone='hand')
        action=self.ready(session,source,{'G':2,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        for body in (wolf,werewolf,both):
            self.assertEqual((3,4),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertEqual((2,3),(engine._numeric_stat(enemy.object_id,'power'),engine._numeric_stat(enemy.object_id,'toughness')))
        self.assertEqual(4,engine._numeric_stat(source.object_id,'power'))
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
