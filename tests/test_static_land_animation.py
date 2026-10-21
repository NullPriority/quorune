from __future__ import annotations

"""CR205.1b and302.6: static land creatures retain land and control history."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_expanded_basic_type_setting as controls


class StaticLandAnimationTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ExpandedBasicTypeSettingTests.session
    add=controls.ExpandedBasicTypeSettingTests.add
    prepare=controls.ExpandedBasicTypeSettingTests.prepare
    checkpoint=controls.ExpandedBasicTypeSettingTests.checkpoint
    resolve=controls.ExpandedBasicTypeSettingTests.resolve
    replay=controls.ExpandedBasicTypeSettingTests.replay
    binding=controls.ExpandedBasicTypeSettingTests.binding
    priority=controls.ExpandedBasicTypeSettingTests.priority
    cast=controls.ExpandedBasicTypeSettingTests.cast
    offered_cast=controls.ExpandedBasicTypeSettingTests.offered_cast
    private_replay=controls.ExpandedBasicTypeSettingTests.private_replay
    offered=controls.ExpandedBasicTypeSettingTests.offered
    advance=controls.ExpandedBasicTypeSettingTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'static-land-animation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/static-land-animation-cards.json',ROOT/'tests/fixtures/fixed-basic-type-setting-cards.json',
            ROOT/'tests/fixtures/attached-occurrence-cards.json',ROOT/'tests/fixtures/expanded-basic-type-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static land animation',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_complete_living_plane_and_natures_revolt_originals_register_before_actions(self):
        session=self.session(752100)
        for name in ('Living Plane',"Nature's Revolt"):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def test_complete_living_lands_and_kormus_bell_originals_register_before_actions(self):
        session=self.session(752101)
        for name in ('Living Lands','Kormus Bell'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def test_actual_living_plane_animates_all_seats_retains_land_mana_and_restores(self):
        session=self.session(752102);source=self.add(session.engine,'Living Plane',zone='hand')
        land=self.add(session.engine,'Darksteel Citadel',seat='B');plain=self.add(session.engine,'Generic Bound Plains',seat='D',ref='other-seat-plain')
        destroy=self.add(session.engine,'Naturalize',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        land.acquired_control_turn_count=-1;plain.acquired_control_turn_count=-1
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':3,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':2});self.assertIsNone(self.resolve(session))
        for target in (land,plain):
            data=session.engine._effective_card_data(session.state.cards[target.object_id]);kinds,_,_=session.engine._type_parts(data['type_line'])
            self.assertTrue({'land','creature'}<=kinds);self.assertEqual((1,1),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.assertIn('artifact',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[land.object_id])['type_line'])[0])
        self.assertIn('indestructible',{x.casefold() for x in session.engine._effective_card_data(session.state.cards[land.object_id])['keywords']})
        self.priority(session,'B');mana=[a for a in self.offered(session,'B') if a.get('card')==land.ref or land.ref in a['id']]
        self.assertEqual(1,len(mana));result=session.act('pilot:B',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.players['B'].mana_pool.get('C',0))
        self.offered_cast(session,destroy,{'G':1,'C':1},[source.ref]);self.assertIsNone(self.resolve(session))
        restored=session.engine._effective_card_data(session.state.cards[land.object_id]);self.assertEqual({'artifact','land'},session.engine._type_parts(restored['type_line'])[0])
        self.private_replay(session,private)

    def test_actual_natures_revolt_sets_two_two_and_counters_modify_after_base(self):
        session=self.session(752103);source=self.add(session.engine,"Nature's Revolt",zone='hand')
        land=self.add(session.engine,'Generic Bound Plains',ref='counter-land');land.counters['+1/+1']=1
        foreign=self.add(session.engine,'Darksteel Citadel',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':3});self.assertIsNone(self.resolve(session))
        self.assertEqual((3,3),(session.engine._numeric_stat(land.object_id,'power'),session.engine._numeric_stat(land.object_id,'toughness')))
        self.assertEqual((2,2),(session.engine._numeric_stat(foreign.object_id,'power'),session.engine._numeric_stat(foreign.object_id,'toughness')))
        self.assertEqual({'+1/+1':1},session.state.cards[land.object_id].counters);self.private_replay(session,private)

    def test_actual_kormus_bell_sets_swamps_black_and_leaves_other_lands_unchanged(self):
        session=self.session(752104);source=self.add(session.engine,'Kormus Bell',zone='hand')
        swamp=self.add(session.engine,'Swamp',seat='B');island=self.add(session.engine,'Snow-Covered Island',seat='D')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'C':4});self.checkpoint(session)
        self.offered_cast(session,source,{'C':4});self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[swamp.object_id]);self.assertEqual({'B'},set(data['colors']))
        self.assertEqual(({'land','creature'},{'swamp'},{'basic'}),session.engine._type_parts(data['type_line']))
        self.assertEqual((1,1),(session.engine._numeric_stat(swamp.object_id,'power'),session.engine._numeric_stat(swamp.object_id,'toughness')))
        self.assertEqual({'land'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[island.object_id])['type_line'])[0]);self.private_replay(session,private)

    def test_actual_living_lands_selects_current_forests_and_preserves_mana(self):
        session=self.session(752105);source=self.add(session.engine,'Living Lands',zone='hand')
        forest=self.add(session.engine,'Forest',seat='B');island=self.add(session.engine,'Snow-Covered Island',seat='D')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':1,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':1,'C':3});self.assertIsNone(self.resolve(session))
        self.assertEqual({'land','creature'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[forest.object_id])['type_line'])[0])
        self.assertEqual({'land'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[island.object_id])['type_line'])[0])
        self.assertEqual({'intrinsic_forest'},{a.ability_id for a in session.engine._activated_abilities(session.state.cards[forest.object_id])});self.private_replay(session,private)

    def test_old_and_new_animated_land_mana_actions_use_continuous_control_age(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752106);source=self.add(session.engine,"Nature's Revolt",zone='hand')
        old=self.add(session.engine,'Generic Bound Plains',ref='old-animated-land');old.acquired_control_turn_count=-1
        fresh=self.add(session.engine,'Generic Bound Plains',zone='hand',ref='fresh-animated-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':3});self.assertIsNone(self.resolve(session));self.priority(session,'A')
        actions=self.offered(session,'A');old_actions=[a for a in actions if a.get('card')==old.ref or old.ref in a['id']]
        self.assertEqual(1,len(old_actions));result=session.act('pilot:A',{'action_id':old_actions[0]['id']});self.assertTrue(result.ok,result.summary)
        self.priority(session,'A');plays=[a for a in self.offered(session,'A') if a['id'].startswith('play-land:') and fresh.ref in a['id']]
        self.assertEqual(1,len(plays));result=session.act('pilot:A',{'action_id':plays[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual({'land','creature'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[fresh.object_id])['type_line'])[0])
        self.priority(session,'A');self.assertFalse(any(a.get('card')==fresh.ref or fresh.ref in a['id'] for a in self.offered(session,'A')))
        # The equivalent offered basic-land action ID must reject for the fresh
        # creature land through the same mana owner, without tapping it.
        command={'action_id':old_actions[0]['id'].replace(old.ref,fresh.ref)};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));self.assertFalse(session.state.cards[fresh.object_id].tapped)
        self.private_replay(session,private)

    def test_kormus_membership_tracks_urborg_departure_and_restores_non_swamp(self):
        session=self.session(752107);bell=self.add(session.engine,'Kormus Bell',zone='hand')
        urborg=self.add(session.engine,'Urborg, Tomb of Yawgmoth');island=self.add(session.engine,'Snow-Covered Island',seat='B')
        bounce=self.add(session.engine,'Boomerang',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':4,'U':2});self.checkpoint(session);self.offered_cast(session,bell,{'C':4});self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[island.object_id]);self.assertEqual({'island','swamp'},session.engine._type_parts(data['type_line'])[1])
        self.assertIn('creature',session.engine._type_parts(data['type_line'])[0]);self.assertEqual({'B'},set(data['colors']))
        self.offered_cast(session,bounce,{'U':2},[urborg.ref]);self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[island.object_id]);self.assertEqual({'land'},session.engine._type_parts(data['type_line'])[0])
        self.assertEqual({'island'},session.engine._type_parts(data['type_line'])[1]);self.assertFalse(data['colors']);self.private_replay(session,private)

    def test_animation_descriptor_declares_type_layer_participation_and_closed_grammar(self):
        from quorune.compiler.static_land_animation import static_land_animation_handler
        from quorune.semantic_runtime.continuous_components import default_continuous_effect_component_registry
        from quorune.continuous_effect_model import Layer
        descriptor=static_land_animation_handler('All lands are 1/1 creatures that are still lands.')[1]
        self.assertEqual(Layer.TYPE,default_continuous_effect_component_registry().source_component_layer(descriptor))
        for text in ('All lands are X/X creatures that are still lands.','All lands are 1/1 creatures until end of turn. They are still lands.',
                'All lands are 1/1 creatures that are still lands. Draw a card.','All Deserts are 1/1 creatures that are still lands.'):
            self.assertIsNone(static_land_animation_handler(text),text)

    def test_living_plane_keeps_world_rule_when_a_new_original_resolves(self):
        session=self.session(752108);first=self.add(session.engine,'Living Plane',zone='hand',ref='first-world-animation')
        second=self.add(session.engine,'Living Plane',zone='hand',ref='new-world-animation');land=self.add(session.engine,'Generic Bound Plains')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'G':4,'C':4});self.checkpoint(session)
        self.offered_cast(session,first,{'G':2,'C':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[first.object_id].zone)
        self.offered_cast(session,second,{'G':2,'C':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[first.object_id].zone);self.assertEqual('battlefield',session.state.cards[second.object_id].zone)
        self.assertIn('world',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[second.object_id])['type_line'])[2])
        self.assertEqual((1,1),(session.engine._numeric_stat(land.object_id,'power'),session.engine._numeric_stat(land.object_id,'toughness')))
        self.private_replay(session,private)

    def test_song_removes_kormus_printed_animation_and_source_removal_restores_it(self):
        session=self.session(752109);bell=self.add(session.engine,'Kormus Bell',zone='hand');swamp=self.add(session.engine,'Swamp',seat='B')
        song=self.add(session.engine,'Song of the Dryads',zone='hand');destroy=self.add(session.engine,'Naturalize',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'C':7,'G':2});self.checkpoint(session)
        self.offered_cast(session,bell,{'C':4});self.assertIsNone(self.resolve(session));self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[swamp.object_id])['type_line'])[0])
        self.offered_cast(session,song,{'G':1,'C':2},[bell.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual({'land'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[swamp.object_id])['type_line'])[0])
        self.offered_cast(session,destroy,{'G':1,'C':1},[song.ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[swamp.object_id])['type_line'])[0]);self.private_replay(session,private)

    def test_animation_setting_owner_is_an_independent_runtime_minimum(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Living Plane',"Nature's Revolt",'Living Lands','Kormus Bell'):
            session=self.session(752110);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(d.get('handler_id')=='continuous.characteristics.fixed-public-setting.v1' for d in p.handlers)]
            self.assertEqual(1,len(programs));program=programs[0];cap='continuous.characteristics.fixed_public_setting'
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict'],name);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_verified_setting_withdrawal_blocks_all_four_complete_originals(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        cap='continuous.characteristics.fixed_public_setting';next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn static land animation authority'])
        registry=CapabilityRegistry(changed);decl=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Living Plane',"Nature's Revolt",'Living Lands','Kormus Bell'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_animation_query_and_descriptor_layers_reject_unsupported_shapes(self):
        from copy import deepcopy
        from quorune.compiler.static_land_animation import static_land_animation_handler
        from quorune.semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.continuous_effect_model import Layer
        handler=FixedCharacteristicSettingsHandler();base=static_land_animation_handler('All Swamps are 1/1 black creatures that are still lands.')[1]
        for predicate in (ObjectQuerySpec(types_all=('creature',),subtypes_all=('swamp',)),
                ObjectQuerySpec(types_all=('land',),subtypes_all=('desert',)),ObjectQuerySpec(types_all=('land',),subtypes_all=('swamp',),known_to_actor=False)):
            changed=deepcopy(base);changed['target']['predicate']=predicate.to_dict()
            with self.assertRaises(ValueError):handler.validate(changed)
        changed=deepcopy(base);changed['schema_version']=True
        with self.assertRaises(ValueError):handler.descriptor_source_component_layer(changed)
        pure=deepcopy(base);pure['modifier']['type_operations']=[];pure['modifier']['color_operations']=[]
        self.assertEqual(Layer.ABILITY,handler.descriptor_source_component_layer(pure))

    def test_compiler_lowerer_and_first_layer_omissions_are_killed(self):
        from unittest.mock import patch
        from quorune.continuous_effect_model import Layer
        cases=(('quorune.compiler.static_land_animation.static_land_animation_handler',lambda *a:None,
                'test_actual_natures_revolt_sets_two_two_and_counters_modify_after_base'),
            ('quorune.semantic_runtime.fixed_characteristic_settings.FixedCharacteristicSettingsHandler.lower',lambda *a:(),
                'test_actual_natures_revolt_sets_two_two_and_counters_modify_after_base'),
            ('quorune.semantic_runtime.fixed_characteristic_settings.FixedCharacteristicSettingsHandler.descriptor_source_component_layer',lambda *a:Layer.ABILITY,
                'test_animation_descriptor_declares_type_layer_participation_and_closed_grammar'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
