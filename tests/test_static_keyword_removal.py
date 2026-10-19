from __future__ import annotations

"""CR 613.1f/613.7: a live static removal permits later keyword grants."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_characteristic_settings import fixed_characteristic_setting_handler
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls


class StaticKeywordRemovalCompilerTests(unittest.TestCase):
    def test_static_removal_is_complete_registered_descriptor_and_unknown_removals_fail_closed(self):
        result=fixed_characteristic_setting_handler('All creatures lose flying and islandwalk.')
        self.assertIsNotNone(result)
        self.assertEqual(['Flying','Islandwalk'],result[1]['modifier']['remove_abilities'])
        self.assertEqual('any',result[1]['target']['controller'])
        self.assertIn('combat.block.flying',result[2]);self.assertIn('combat.block.landwalk.basic_type',result[2])
        FixedCharacteristicSettingsHandler().validate(result[1])
        for bad in ('All creatures lose your choice of flying or trample.',
                'All creatures lose flying and cannot gain flying.',
                'All creatures lose flying until end of turn.',
                'All creatures lose toxic 2.','All creatures lose prowess.',
                'All creatures lose an unrepresented keyword.'):
            self.assertIsNone(fixed_characteristic_setting_handler(bad),bad)
        changed=deepcopy(result[1]);changed['modifier']['remove_abilities']=['Prowess']
        with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate(changed)


class StaticKeywordRemovalRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'static-removal.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/static-keyword-removal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static keyword removal',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_sphere_removes_current_flying_and_later_jump_restores_it(self):
        session=self.session(730101)
        source=self.add(session.engine,'Gravity Sphere',zone='hand')
        own=self.add(session.engine,'Generic Bound Body',ref='sphere-own');own.counters['flying']=1
        other=self.add(session.engine,'Generic Bound Body',seat='B',ref='sphere-other');other.counters['flying']=1
        jump=self.add(session.engine,'Jump',seat='C',zone='hand')
        removal=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1,'C':1});session.state.players['C'].mana_pool.update({'U':1})
        self.prepare(session,mana={'R':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (own,other):self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.cast(session,'C',jump,[other.ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[other.object_id]))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[own.object_id]))
        self.cast(session,'B',removal,[session.state.cards[source.object_id].ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        for card in (own,other):self.assertIn('flying',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.private_replay(session,private)

    def test_original_decree_removes_two_keywords_and_source_departure_restores_both(self):
        session=self.session(730102)
        source=self.add(session.engine,'Mystic Decree',zone='hand')
        target=self.add(session.engine,'River Boa',seat='D',ref='decree-target')
        target.counters['flying']=1
        removal=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1,'C':1})
        self.prepare(session,mana={'U':2,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertNotIn('islandwalk',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.cast(session,'B',removal,[session.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertIn('islandwalk',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.private_replay(session,private)

    def test_fresh_sphere_witness_kills_lost_static_keyword_removal(self):
        original=FixedCharacteristicSettingsHandler.validate
        def omit(handler,descriptor):
            if descriptor.get('modifier',{}).get('remove_abilities'):
                value=deepcopy(descriptor);value['modifier']['remove_abilities']=[]
                return original(handler,value)
            return original(handler,descriptor)
        method='test_original_sphere_removes_current_flying_and_later_jump_restores_it'
        with patch.object(FixedCharacteristicSettingsHandler,'validate',omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_sphere_applies_to_later_wind_drake_entry_and_ends_on_departure(self):
        session=self.session(730103)
        source=self.add(session.engine,'Gravity Sphere',zone='hand')
        later=self.add(session.engine,'Wind Drake',zone='hand')
        removal=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1,'C':1})
        self.prepare(session,mana={'R':1,'U':1,'C':4});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',later);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[later.object_id].zone)
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[later.object_id]))
        self.cast(session,'B',removal,[session.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[later.object_id]))
        self.private_replay(session,private)

    def test_originals_require_full_static_and_keyword_capabilities_and_complete_preflight(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.preflight import card_semantic_status
        from quorune.rules.capabilities import CapabilityRegistry
        session=self.session(730104)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for name in ('Gravity Sphere','Mystic Decree'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
            cap='continuous.characteristics.fixed_public_setting'
            program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
            for dependency in (cap,'combat.block.flying'):
                changed=deepcopy(raw)
                next(row for row in changed['capabilities'] if row['id']==dependency).update(status='blocked',blockers=['withdrawn'])
                self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=CapabilityRegistry(changed),capability_profile='commander_review').status,dependency)
