from __future__ import annotations

"""CR 604.3/613.3: source definitions and live all-color settings are distinct."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_qualified_hexproof as controls


class AllColorsTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.QualifiedHexproofTests.session
    add=controls.QualifiedHexproofTests.add
    prepare=controls.QualifiedHexproofTests.prepare
    checkpoint=controls.QualifiedHexproofTests.checkpoint
    resolve=controls.QualifiedHexproofTests.resolve
    replay=controls.QualifiedHexproofTests.replay
    binding=controls.QualifiedHexproofTests.binding
    actions=controls.QualifiedHexproofTests.actions
    priority=controls.QualifiedHexproofTests.priority
    cast=controls.QualifiedHexproofTests.cast
    private_replay=controls.QualifiedHexproofTests.private_replay
    advance=controls.QualifiedHexproofTests.advance
    attack=controls.QualifiedHexproofTests.attack

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'all-colors.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/all-colors-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('All colors',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_courier_cast_has_complete_admission_and_all_five_colors(self):
        session=self.session(752100);source=self.add(session.engine,'Transguild Courier',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':4});self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'pay':'manual','payment':{'C':4}}
        offered=next((r for r in self.actions(session) if r['id']==command['action_id']),None)
        self.assertIsNotNone(offered,'The complete original all-colors creature must be available through ordinary registration')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));current=session.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone);self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(current)['colors']))
        self.assertEqual((3,3),(session.engine._numeric_stat(current.object_id,'power'),session.engine._numeric_stat(current.object_id,'toughness')))
        self.assertFalse(any(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_fresh_definition_compiler_omission_is_killed(self):
        method='test_actual_courier_cast_has_complete_admission_and_all_five_colors'
        with patch('quorune.compiler.all_colors_definitions.all_colors_definition_handler',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_definition_codec_all_zones_and_layer_order_are_independent_of_printed_color_data(self):
        from quorune.all_colors import AllColorsCharacteristicDefinitionSpec
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer
        from quorune.model import CardInstance
        fragment=ability_fragment_to_dict(AllColorsCharacteristicDefinitionSpec())
        card=CardInstance(object_id='all-colors-model',ref='all-colors-model',oracle_id='all-colors-model',printed_name='All colors model',owner='A',controller='A',zone='hand')
        base={'name':'All colors model','mana_cost':'{4}','mana_value':4,'type_line':'Artifact Creature — Golem',
            'oracle_text':'','colors':[],'keywords':[],'power':'3','toughness':'3','ability_fragments':[fragment],'activated_abilities':[]}
        for zone in ('library','hand','graveyard','exile','command','stack','battlefield'):
            card.zone=zone;self.assertEqual(set('WUBRG'),set(evaluate_card_characteristics(card,base)['colors']))
        card.zone='battlefield'
        red=ContinuousEffect(effect_id='red',source_id='color-source',layer=Layer.COLOR,sublayer='5',timestamp=1,
            operations=(ContinuousOperation('set_colors',('R',)),))
        loss=ContinuousEffect(effect_id='loss',source_id='ability-source',layer=Layer.ABILITY,sublayer='6',timestamp=2,
            operations=(ContinuousOperation('remove_all_abilities'),))
        result=evaluate_card_characteristics(card,base,runtime_effects=(red,loss))
        self.assertEqual(['R'],result['colors']);self.assertEqual([],result['ability_fragments'])
        result=evaluate_card_characteristics(card,base,runtime_effects=(loss,))
        self.assertEqual(set('WUBRG'),set(result['colors']))
        for raw in ({'schema_version':True},{'schema_version':2},{'schema_version':1,'colors':['R']}):
            with self.assertRaises(ValueError):AllColorsCharacteristicDefinitionSpec.from_dict(raw)
        from quorune.morph import MORPH_FACE_DOWN_ANNOTATION
        card.face_down=True;card.annotations[MORPH_FACE_DOWN_ANNOTATION]={'name':'','mana_cost':'','mana_value':0,'text':'',
            'supertypes':[],'card_types':['Creature'],'subtypes':[],'colors':[],'abilities':[],'power':2,'toughness':2}
        self.assertEqual([],evaluate_card_characteristics(card,base)['colors'])
        self.assertEqual(set('WUBRG'),set(evaluate_card_characteristics(card,base,ignore_face_down=True)['colors']))

    def test_actual_leyline_cast_colors_only_controlled_nonlands_and_retains_basic_mana(self):
        session=self.session(752110);source=self.add(session.engine,'Leyline of the Guildpact',zone='hand')
        own=self.add(session.engine,'Grizzly Bears');land=self.add(session.engine,'Plains')
        other=self.add(session.engine,'Grizzly Bears',seat='B',ref='foreign-all-colors-bear')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':4});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=next(r for r in self.actions(session) if r['id']=='cast:'+source.ref)
        option=next(r for r in action['cost_options'] if r['requirements']['G']==4)
        result=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'pay':'manual','payment':{'G':4}});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for object_id in (own.object_id,source.object_id):self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(session.state.cards[object_id])['colors']))
        self.assertEqual(['G'],session.engine._effective_card_data(session.state.cards[other.object_id])['colors'])
        current=session.state.cards[land.object_id];data=session.engine._effective_card_data(current)
        self.assertEqual([],data['colors']);self.assertEqual({'plains','island','swamp','mountain','forest'},session.engine._type_parts(data['type_line'])[1])
        self.assertEqual(5,sum(a.ability_id.startswith('intrinsic_') for a in session.engine._activated_abilities(current)))
        self.private_replay(session,private)

    def test_actual_later_entrant_receives_live_colors_and_bounce_ends_the_setting(self):
        session=self.session(752111);source=self.add(session.engine,'Leyline of the Guildpact')
        entrant=self.add(session.engine,'Grizzly Bears',zone='hand');bounce=self.add(session.engine,'Boomerang',zone='hand')
        self.prepare(session,mana={'G':1,'C':1,'U':2});self.checkpoint(session)
        self.cast(session,'A',entrant,payment={'G':1,'C':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(session.state.cards[entrant.object_id])['colors']))
        self.cast(session,'A',bounce,[source.ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(['G'],session.engine._effective_card_data(session.state.cards[entrant.object_id])['colors']);self.replay(session)

    def test_fresh_actual_definition_operation_omission_is_killed(self):
        method='test_definition_codec_all_zones_and_layer_order_are_independent_of_printed_color_data'
        with patch('quorune.characteristic_evaluation._has_all_colors_definition',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_sphinx_retains_flying_and_only_rejects_monocolored_sources(self):
        session=self.session(752120);source=self.add(session.engine,'Sphinx of the Guildpact');source.acquired_control_turn_count=-1
        bolt=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand');bear=self.add(session.engine,'Grizzly Bears',seat='B')
        self.prepare(session);session.state.players['B'].mana_pool['R']=1;self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(source)['colors']))
        self.assertIn('flying',session.engine._combat_keywords(source));self.assertIn('hexproof from monocolored',session.engine._combat_keywords(source))
        self.cast_attempt_blocked(session,bolt,source)
        self.attack(session,{source.ref:'B'});self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(35,session.state.players['B'].life);self.assertEqual('battlefield',session.state.cards[bear.object_id].zone);self.replay(session)

    def cast_attempt_blocked(self,session,spell,target):
        self.priority(session,'B');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_clone_copies_the_all_zone_definition_and_color_changes_override_it(self):
        session=self.session(752121);source=self.add(session.engine,'Transguild Courier',seat='B')
        clone=self.add(session.engine,'Clone',zone='hand');frog=self.add(session.engine,'Turn to Frog',zone='hand')
        self.prepare(session,mana={'U':2,'C':4});self.checkpoint(session)
        self.cast(session,'A',clone,payment={'U':1,'C':3});self.assertIsNotNone(self.resolve(session))
        option=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if source.ref in r['id'] and not r['id'].startswith('decline:'))
        result=session.act('pilot:A',{'action_id':'choose','replacement':option['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(session.state.cards[clone.object_id])['colors']))
        self.cast(session,'A',frog,[clone.ref],{'U':1,'C':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(['U'],session.engine._effective_card_data(session.state.cards[clone.object_id])['colors']);self.replay(session)

    def test_actual_ability_loss_does_not_undo_an_earlier_layer_color_definition(self):
        session=self.session(752122);courier=self.add(session.engine,'Transguild Courier');humble=self.add(session.engine,'Humble',zone='hand')
        self.prepare(session,mana={'W':1,'C':1});self.checkpoint(session)
        self.cast(session,'A',humble,[courier.ref],{'W':1,'C':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[courier.object_id])
        self.assertEqual(set('WUBRG'),set(data['colors']));self.assertEqual([],data['ability_fragments'])
        self.assertEqual((0,1),(session.engine._numeric_stat(courier.object_id,'power'),session.engine._numeric_stat(courier.object_id,'toughness')));self.replay(session)

    def test_fresh_actual_live_color_setting_omission_is_killed(self):
        import quorune.semantic_runtime.fixed_characteristic_settings as owner
        original=owner.fixed_characteristic_effects
        def without_color(node,context,*,common):
            return tuple(effect for effect in original(node,context,common=common) if effect.layer.value!=5)
        method='test_actual_leyline_cast_colors_only_controlled_nonlands_and_retains_basic_mana'
        with patch.object(owner,'fixed_characteristic_effects',side_effect=without_color):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_verified_definition_withdrawal_and_stripped_component_minimum_reject(self):
        import json
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.all_colors import ALL_COLORS_CAPABILITY
        p=ROOT/'quorune/rules/capability-registry.json';baseline=json.loads(p.read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'));known=set(discover_tests(ROOT).values())
        def verified(raw):
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Transguild Courier'),capability_registry=verified(baseline),capability_profile='commander_review').status)
        raw=json.loads(p.read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==ALL_COLORS_CAPABILITY).update(status='blocked',blockers=['withdrawn all-colors definition authority'])
        ir=compile_oracle_card(self.db.lookup('Transguild Courier'),capability_registry=verified(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+ALL_COLORS_CAPABILITY+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(752130);source=self.add(session.engine,'Transguild Courier')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.active_zone=='all')
        deps=tuple(c for c in program.capability_dependencies if c!=ALL_COLORS_CAPABILITY)
        changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+ALL_COLORS_CAPABILITY,bound['blockers'])

    def test_actual_opening_leyline_is_optional_private_and_enters_before_the_first_turn(self):
        import json
        from test_opening_hand_battlefield import OpeningHandBattlefieldRuntimeTests as opening
        session=opening.session(self,752140,{'A':['Leyline of the Guildpact']})
        source=opening.card(self,session,'Leyline of the Guildpact')
        opening.keep(self,session);self.assertEqual('opening.hand',session.state.pending_decision.kind)
        self.assertFalse(session.state.started)
        for seat in 'BCD':self.assertNotIn(source.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':'choose','choices':{'refs':[source.ref]}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));session=self.replay(session,load=True)
        opening.choose(self,session,[source.ref]);self.assertTrue(session.state.started)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(session.state.cards[source.object_id])['colors']))
        self.assertFalse(session.state.stack);self.replay(session,load=True)

    def test_current_control_and_timestamps_preserve_live_nonland_scope(self):
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer,ContinuousObjectIdentity,ContinuousEffectRelation
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.all_colors import AllColorsCharacteristicDefinitionSpec
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.model import CardInstance
        session=self.session(752141);source=self.add(session.engine,'Leyline of the Guildpact')
        own=self.add(session.engine,'Grizzly Bears');foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='control-all-colors-bear')
        land=self.add(session.engine,'Plains',seat='B');self.prepare(session)
        session.engine.change_control(source.object_id,'B',reason='All-colors current-control owner boundary')
        self.checkpoint(session)
        self.assertEqual(['G'],session.engine._effective_card_data(own)['colors'])
        self.assertEqual(set('WUBRG'),set(session.engine._effective_card_data(foreign)['colors']))
        self.assertEqual([],session.engine._effective_card_data(land)['colors']);self.replay(session,load=True)
        card=CardInstance(object_id='timestamp-all-colors',ref='timestamp-all-colors',oracle_id='timestamp-all-colors',printed_name='Timestamp colors',owner='B',controller='B',zone='battlefield')
        base={'name':'Timestamp colors','mana_cost':'','mana_value':0,'type_line':'Creature','oracle_text':'','power':'2','toughness':'2','colors':['G'],'keywords':[],'ability_fragments':[],'activated_abilities':[]}
        red=ContinuousEffect(effect_id='red-first',source_id='red-source',layer=Layer.COLOR,sublayer='5',timestamp=1,operations=(ContinuousOperation('set_colors',('R',)),))
        all_colors=ContinuousEffect(effect_id='all-later',source_id='all-source',layer=Layer.COLOR,sublayer='5',timestamp=2,operations=(ContinuousOperation('set_colors',tuple('WUBRG')),))
        self.assertEqual(set('WUBRG'),set(evaluate_card_characteristics(card,base,runtime_effects=(all_colors,red))['colors']))
        from dataclasses import replace
        self.assertEqual(['R'],evaluate_card_characteristics(card,base,runtime_effects=(replace(red,timestamp=3),all_colors))['colors'])
