from __future__ import annotations

"""CR 305.6/305.7: adding types preserves printed abilities and other facts."""

from pathlib import Path
from copy import deepcopy
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.capabilities import CapabilityRegistry
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.basic_type_addition_model import BasicTypeAdditionSpec,BASIC_TYPE_ADDITION_CAPABILITY
from quorune.compiler.basic_type_addition_templates import scoped_basic_type_addition_handler
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions

FIXTURE=ROOT/'tests/fixtures/scoped-basic-type-addition-cards.json'
CLOSED={'Stormtide Leviathan','Swampbenders','Dryad of the Ilysian Grove',"Nylea's Presence",'Prismatic Omen'}


class ScopedBasicTypeAdditionCompilerTests(unittest.TestCase):
    def test_scoped_additions_are_closed_typed_and_source_spanned(self):
        registry=load_default_capability_registry()
        for text,scope,types in (
            ('All lands are Islands in addition to their other types.','all_lands',('island',)),
            ('Lands you control are Swamps in addition to their other types.','controller_lands',('swamp',)),
            ('Enchanted land is every basic land type in addition to its other types.','attached_land',('forest','island','mountain','plains','swamp')),
        ):
            with self.subTest(text=text):
                parsed=scoped_basic_type_addition_handler(text)
                self.assertEqual({'scope':scope,'basic_types':list(types)},parsed[1]['addition'])
                spec=BasicTypeAdditionSpec(scope,types);self.assertEqual(spec,BasicTypeAdditionSpec.from_dict(spec.to_dict()))
                ir=compile_oracle_card(record(text,'Enchantment'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes;self.assertEqual(text,node.text)
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        for text in ('Nonbasic lands are Islands.','Enchanted land is an Island.',
            'Lands you control are Swamps.','Lands you control are Islands in addition to their other types if it is your turn.',
            'All lands is Islands in addition to their other types.'):
            self.assertIsNone(scoped_basic_type_addition_handler(text))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in (BASIC_TYPE_ADDITION_CAPABILITY,'mana.intrinsic.basic_land_type'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['addition owner omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record('Lands you control are Swamps in addition to their other types.','Enchantment'),capability_registry=CapabilityRegistry(changed)).status)


class ScopedBasicTypeAdditionRuntimeTests(unittest.TestCase):
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
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'add-basic.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/qualified-landwalk-cards.json',
            ROOT/'tests/fixtures/intrinsic-mana-ability-loss-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Basic addition',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_prismatic_omen_preserves_other_types_and_printed_mana(self):
        session=self.session(30507101);engine=session.engine
        omen=self.add(engine,'Prismatic Omen',zone='hand')
        own=self.add(engine,'Darksteel Citadel')
        foreign=self.add(engine,'Snow-Covered Island',seat='B')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='basic-add-private')
        self.assert_closed(session,omen)
        self.prepare(session,mana={'C':1,'G':1});self.checkpoint(session)
        self.cast(session,'A',omen);self.resolve(session)
        own_data=engine._effective_card_data(own);foreign_data=engine._effective_card_data(foreign)
        own_types=engine._type_parts(own_data['type_line'])
        self.assertEqual({'artifact','land'},own_types[0])
        self.assertEqual({'plains','island','swamp','mountain','forest'},own_types[1])
        self.assertNotIn('basic',own_types[2]);self.assertIn('Indestructible',own_data['keywords'])
        self.assertEqual({'island'},engine._type_parts(foreign_data['type_line'])[1])
        self.assertEqual({'basic','snow'},engine._type_parts(foreign_data['type_line'])[2])
        abilities=engine._activated_abilities(own)
        self.assertEqual(5,sum(a.ability_id.startswith('intrinsic_') for a in abilities))
        self.assertTrue(any(not a.ability_id.startswith('intrinsic_') and a.mana_ability for a in abilities))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(a for a in actions if a['id'].startswith('activate:'+own.ref+':') and 'intrinsic_mountain' in a['id'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':mana['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':mana['id']});self.assertTrue(result.ok,result.summary)
        self.assertTrue(own.tapped);self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_originals_close_only_with_all_siblings_supported(self):
        names=[row['name'] for row in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']][:6]
        for name in names:
            with self.subTest(name=name):
                program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(name in CLOSED,binding['strict_capability_ready'],binding['blockers'])

    def test_actual_nylea_presence_survives_recipient_humble_and_ends_with_source(self):
        session=self.session(30507102);engine=session.engine
        land=self.add(engine,'Dryad Arbor')
        aura=self.add(engine,"Nylea's Presence",zone='hand')
        humble=self.add(engine,'Humble',zone='hand')
        destroy=self.add(engine,'Disenchant',zone='hand')
        self.assert_closed(session,aura)
        self.prepare(session,mana={'C':1,'G':1});self.checkpoint(session)
        self.cast(session,'A',aura,[land.ref]);session=self.replay(session,load=True);self.resolve(session)
        land=session.state.cards[land.object_id]
        self.assertEqual({'plains','island','swamp','mountain','forest','dryad'},session.engine._type_parts(session.engine._effective_card_data(land)['type_line'])[1])
        self.replay(session,load=True)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[land.ref]);self.resolve(session)
        self.assertEqual({'plains','island','swamp','mountain','forest','dryad'},session.engine._type_parts(session.engine._effective_card_data(land)['type_line'])[1])
        self.assertFalse(any(a.ability_id.startswith('intrinsic_') for a in session.engine._activated_abilities(land)))
        self.replay(session,load=True)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',destroy,[aura.ref]);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        data=session.engine._effective_card_data(land)
        self.assertEqual({'forest','dryad'},session.engine._type_parts(data['type_line'])[1])
        self.assertFalse(any(a.ability_id.startswith('intrinsic_') for a in session.engine._activated_abilities(land)))
        self.replay(session,load=True)

    def test_actual_stormtide_type_effect_precedes_source_humble(self):
        session=self.session(30507103);engine=session.engine
        source=self.add(engine,'Stormtide Leviathan')
        land=self.add(engine,'Darksteel Citadel',seat='B')
        humble=self.add(engine,'Humble',zone='hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[source.ref]);self.resolve(session)
        self.assertNotIn('islandwalk',engine._combat_keywords(source))
        self.assertEqual({'island'},engine._type_parts(engine._effective_card_data(land)['type_line'])[1])
        self.assertTrue(any(a.ability_id=='intrinsic_island' for a in engine._activated_abilities(land)))
        self.replay(session,load=True)

    def test_addition_omission_is_killed_by_actual_omen_witness(self):
        from quorune.semantic_runtime.basic_type_addition import ScopedBasicTypeAdditionHandler
        with mock.patch.object(ScopedBasicTypeAdditionHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_prismatic_omen_preserves_other_types_and_printed_mana()
