from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest import mock
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.capabilities import CapabilityRegistry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.record import authoritative_state_hash
from quorune.compiler.token_copy_templates import _fixed_exception
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions

FIXTURE=ROOT/'tests/fixtures/fixed-copy-type-exception-cards.json'


class FixedCopyTypeExceptionCompilerTests(unittest.TestCase):
    def test_fixed_copy_type_exceptions_consume_full_type_words(self):
        spec=_fixed_exception("it's a Synth artifact creature in addition to its other types")
        self.assertEqual(('artifact','creature'),spec.add_card_types)
        self.assertEqual(('synth',),spec.add_subtypes)
        spec=_fixed_exception("it's a Ninja in addition to its other creature types")
        self.assertEqual(('ninja',),spec.add_subtypes)
        for text in ("it's a Synth unknown creature in addition to its other types",
            "it's a Synth artifact creature in addition to its other creature types",
            "it's a Synth Synth artifact creature in addition to its other types",
            "it's a Ninja in addition to its other types. Draw a card."):
            with self.subTest(text=text):self.assertIsNone(_fixed_exception(text))


class FixedCopyTypeExceptionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'copy-type.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copy types',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose_copy(self,session,target):
        decision=self.resolve(session);self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True)
        option=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['options']
            if target.ref in row['id'] and not row['id'].startswith('decline:'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'choose','replacement':option['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','replacement':option['id']})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        return session

    def test_actual_synth_exception_is_copiable_and_survives_ability_removal(self):
        session=self.session(70709101);engine=session.engine
        target=self.add(engine,'Generic Bound Body',seat='B')
        source=self.add(engine,'Synth Infiltrator',zone='hand')
        clone=self.add(engine,'Clone',zone='hand')
        humble=self.add(engine,'Humble',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='copy-type-private')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'U':2});self.checkpoint(session)
        self.cast(session,'A',source);session=self.choose_copy(session,target)
        current=session.state.cards[source.object_id]
        data=session.engine._effective_card_data(current)
        self.assertEqual({'artifact','creature'},session.engine._type_parts(data['type_line'])[0])
        self.assertIn('synth',session.engine._type_parts(data['type_line'])[1])
        self.assertNotIn('improvise',{keyword.casefold() for keyword in data['keywords']})
        self.replay(session,load=True)
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',clone);session=self.choose_copy(session,current)
        copied=session.state.cards[clone.object_id]
        data=session.engine._effective_card_data(copied)
        self.assertEqual({'artifact','creature'},session.engine._type_parts(data['type_line'])[0])
        self.assertIn('synth',session.engine._type_parts(data['type_line'])[1])
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[copied.ref]);self.resolve(session)
        data=session.engine._effective_card_data(copied)
        self.assertEqual({'artifact','creature'},session.engine._type_parts(data['type_line'])[0])
        self.assertIn('synth',session.engine._type_parts(data['type_line'])[1])
        self.replay(session,load=True)

    def test_original_type_exception_carriers_require_whole_card_admission(self):
        for name in ('Synth Infiltrator',"Sakashima's Student"):
            with self.subTest(name=name):
                card=self.db.lookup(name)
                p=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(b['strict_capability_ready'],b['blockers'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        changed=json.loads(json.dumps(raw));next(r for r in changed['capabilities'] if r['id']=='zone.entry.fixed_copy').update(status='blocked',blockers=['copy owner omitted'])
        blocked=CapabilityRegistry(changed)
        card=self.db.lookup('Synth Infiltrator');p=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=blocked,capability_profile='commander_review')
        binding=bind_card_program_runtime(p,capability_registry=blocked,profile='commander_review')
        self.assertFalse(binding['strict_capability_ready'])

    def test_copied_type_omission_is_killed_by_actual_synth(self):
        import quorune.semantic_runtime.entry_copy as owner
        original=owner.apply_copy_exception
        def omitted(values,spec):return original(values,replace(spec,add_card_types=(),add_subtypes=()))
        with mock.patch.object(owner,'apply_copy_exception',side_effect=omitted):
            with self.assertRaises(AssertionError):self.test_actual_synth_exception_is_copiable_and_survives_ability_removal()
