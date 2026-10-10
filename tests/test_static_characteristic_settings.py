from __future__ import annotations

"""Pinned CR205/611/613: constant live characteristic settings."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_characteristic_settings import fixed_characteristic_setting_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class StaticSettingCompilerTests(unittest.TestCase):
    def test_global_permanent_subject_has_no_fictitious_card_type(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        for text in ('All permanents','Other permanents','permanents you control'):
            relation,query,excluded=fixed_characteristic_battlefield_query_subject(text)
            self.assertEqual((),query.types_all)
            self.assertEqual(('battlefield',),query.zones)
        self.assertEqual(('artifact',),fixed_characteristic_battlefield_query_subject('All artifacts')[1].types_all)

    def test_static_settings_preserve_exact_types_colors_and_source_spans(self):
        cases=(
            'Enchanted artifact is a creature with base power and toughness 5/5 in addition to its other types.',
            'Other creatures have base power and toughness 2/2 and are Bears in addition to their other types.',
            'All permanents are colorless.',
            'All creatures are black.',
            'Enchanted creature has base power and toughness 0/4, has defender, loses all other abilities, and is a blue Wall in addition to its other colors and types.',
            'Creatures you control with +1/+1 counters on them have base power and toughness 4/4, have flying, and are Angels in addition to their other types.')
        for text in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Enchantment'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status)
                node=ir.faces[0].nodes[0]
                self.assertEqual('static-fixed-characteristic-setting-v1',node.template_id)
                FixedCharacteristicSettingsHandler().validate(node.handlers[0])
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        compiled=fixed_characteristic_setting_handler(cases[4])[1]
        modifier=compiled['modifier']
        self.assertTrue(modifier['remove_all_abilities']);self.assertEqual(['Defender'],modifier['add_abilities'])
        self.assertEqual([{'op':'add_colors','values':['U']}],modifier['color_operations'])
        self.assertEqual(0,modifier['base_power']);self.assertEqual(4,modifier['base_toughness'])

    def test_static_setting_unsupported_grammar_and_malformed_descriptors_fail_closed(self):
        for text in ('All lands are Islands.', 'All creatures are named Example.',
                     'All creatures are every creature type.', 'All creatures have base power and toughness X/X.',
                     'As long as you control a creature, all creatures are black.',
                     'Enchanted creature is a Goblin and has "{T}: Draw a card."',
                     'Enchanted creature is a Goblin. You win the game.',
                     'All creatures are enchantments.',
                     'All creatures have base power and toughness 1/1 and have base power and toughness 2/2.',
                     'All creatures have flying and lose all abilities.'):
            self.assertIsNone(fixed_characteristic_setting_handler(text),text)
        base=fixed_characteristic_setting_handler('All creatures have base power and toughness 1/1.')[1]
        for field,value in [('schema_version',True),('event','resolve'),('extra',0)]:
            with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate({**base,field:value})
        missing=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        missing['capabilities']=[r for r in missing['capabilities'] if r['id']!='continuous.characteristics.fixed_public_setting']
        ir=compile_oracle_card(replace(query_record('All creatures have base power and toughness 1/1.'),type_line='Enchantment'),
            capability_registry=CapabilityRegistry(missing))
        self.assertNotEqual('exact',ir.status)

    def test_static_setting_creature_subtype_replacement_keeps_artifact_subtypes(self):
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        descriptor=fixed_characteristic_setting_handler('Enchanted creature is a Demon Spirit.')[1]
        context=ContinuousEffectSourceContext(source_object_id='aura',source_ref='aura',source_controller='A',
            source_timestamp=1,component_id='subtype-witness',attached_object=ContinuousObjectIdentity('body','body@0'))
        effects=FixedCharacteristicSettingsHandler().lower(descriptor,context)
        base=CharacteristicState(name='Subtype witness',controller='B',card_types={'Artifact','Creature'},
            subtypes={'Human','Equipment'},power=2,toughness=3)
        result=evaluate_continuous_effects(base,effects,context={'object_id':'body','logical_object_id':'body@0','zone':'battlefield','owner':'B'}).characteristics
        self.assertEqual({'demon','spirit','equipment'},{s.lower() for s in result['subtypes']})

    def test_static_setting_attachment_incarnation_and_query_codec_remain_closed(self):
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        descriptor=fixed_characteristic_setting_handler('Enchanted artifact is a creature with base power and toughness 5/5 in addition to its other types.')[1]
        context=ContinuousEffectSourceContext(source_object_id='aura',source_ref='aura',source_controller='A',source_timestamp=3,
            component_id='incarnation-test',attached_object=ContinuousObjectIdentity('body','body@0'))
        effects=FixedCharacteristicSettingsHandler().lower(descriptor,context)
        base=CharacteristicState(name='Artifact witness',controller='B',card_types={'Artifact'})
        result=evaluate_continuous_effects(base,effects,context={'object_id':'body','logical_object_id':'body@1','zone':'battlefield','owner':'B'}).characteristics
        self.assertEqual(['Artifact'],result['card_types']);self.assertIsNone(result['power'])
        malformed=deepcopy(descriptor);malformed['target']['predicate']['types_all']=['permanent']
        with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate(malformed)
        malformed=deepcopy(descriptor);malformed['modifier']['quantity']={'unexpected':0}
        with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate(malformed)


class StaticSettingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'settings.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/static-characteristic-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static setting witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_ensoul_offer_animation_counters_source_departure_and_replay(self):
        session=self.session(284001);engine=session.engine
        body=self.add(engine,'Generic Setting Artifact',seat='B');body.counters['+1/+1']=2
        aura=self.add(engine,'Ensoul Artifact',zone='hand')
        spell=self.add(engine,'Generic Setting Bounce',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-setting-c')
        nonartifact=self.add(engine,'Generic Bound Body',seat='B',ref='nonartifact-body')
        action=self.ready(session,aura,{'U':2,'C':1})
        self.assertIn(body.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(nonartifact.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(private.ref,str(session.packet('pilot:B',full=True)))
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        rejected=session.act('pilot:A',{'action_id':action['id'],'targets':[nonartifact.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        body=session.state.cards[body.object_id];aura=session.state.cards[aura.object_id]
        data=engine._effective_card_data(body)
        self.assertEqual({'artifact','creature'},engine._type_parts(data['type_line'])[0])
        self.assertEqual((7,7),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertEqual(aura.object_id,body.attachments[0])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        bounce=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':bounce['id'],'targets':[aura.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual({'artifact'},engine._type_parts(engine._effective_card_data(body)['type_line'])[0])
        self.replay(session,load=True)

    def test_actual_kudo_changes_other_creatures_across_controllers_with_replay(self):
        session=self.session(284002);engine=session.engine
        source=self.add(engine,'Kudo, King Among Bears',zone='hand')
        body=self.add(engine,'Generic Setting Large Body',seat='B');body.counters['+1/+1']=2
        action=self.ready(session,source,{'G':1,'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((4,4),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertEqual((2,2),(engine._numeric_stat(source.object_id,'power'),engine._numeric_stat(source.object_id,'toughness')))
        self.assertIn('bear',engine._type_parts(engine._effective_card_data(body)['type_line'])[1])
        self.replay(session,load=True)

    def test_static_setting_source_removal_and_ability_order_use_current_layers(self):
        session=self.session(284003);engine=session.engine
        body=self.add(engine,'Generic Setting Large Body',seat='B');body.counters['+1/+1']=2
        source=self.add(engine,'Humility',zone='hand')
        spell=self.add(engine,'Generic Setting Bounce',zone='hand')
        action=self.ready(session,source,{'W':2,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(body)
        self.assertEqual((3,3),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertNotIn('flying',tuple(k.lower() for k in data['keywords']))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        bounce=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':bounce['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(body)
        self.assertEqual((8,9),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertIn('flying',tuple(k.lower() for k in data['keywords']))
        self.replay(session,load=True)

    def test_actual_deep_freeze_keeps_original_types_and_own_defender(self):
        session=self.session(284004);engine=session.engine
        body=self.add(engine,'Generic Setting Large Body',seat='B');body.counters['+1/+1']=2
        aura=self.add(engine,'Deep Freeze',zone='hand')
        action=self.ready(session,aura,{'U':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(body)
        self.assertEqual((2,6),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        types,subtypes,_=engine._type_parts(data['type_line'])
        self.assertEqual({'artifact','creature'},types)
        self.assertEqual({'construct','wall'},subtypes)
        self.assertIn('U',data['colors'])
        self.assertEqual({'defender'},{k.lower() for k in data['keywords']})
        self.replay(session,load=True)

    def test_static_setting_started_effect_continues_when_source_loses_ability(self):
        session=self.session(284005);engine=session.engine
        source=self.add(engine,'Humility')
        body=self.add(engine,'Generic Setting Large Body',seat='B')
        spell=self.add(engine,'Generic Setting Animation',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertIn('creature',engine._type_parts(engine._effective_card_data(source)['type_line'])[0])
        self.assertEqual((1,1),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertNotIn('flying',{k.lower() for k in engine._effective_card_data(body)['keywords']})
        self.replay(session,load=True)

    def test_static_setting_color_timestamps_and_source_departure_replay(self):
        for first,second,expected in (('Darkest Hour','Thran Lens',set()),('Thran Lens','Darkest Hour',{'B'})):
            with self.subTest(first=first):
                session=self.session(284006 if first=='Darkest Hour' else 284007);engine=session.engine
                body=self.add(engine,'Generic Setting Large Body',seat='B')
                earlier=self.add(engine,first)
                earlier.zone_timestamp=0
                later=self.add(engine,second,zone='hand')
                bounce=self.add(engine,'Generic Setting Bounce',zone='hand')
                action=self.ready(session,later,{'B':1,'C':2});self.checkpoint(session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual(expected,set(engine._effective_card_data(body)['colors']))
                self.assertEqual(set(),set(engine._copyable_characteristics(body)['colors']))
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                action=next(row for row in actions if row.get('card')==bounce.ref)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[later.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual({'B'} if first=='Darkest Hour' else set(),set(engine._effective_card_data(body)['colors']))
                self.replay(session,load=True)

    def test_static_setting_older_removal_and_newer_grant_follow_timestamp_order(self):
        for removal_first in (True,False):
            with self.subTest(removal_first=removal_first):
                session=self.session(284008 if removal_first else 284009);engine=session.engine
                body=self.add(engine,'Generic Setting Large Body',seat='B')
                humility=self.add(engine,'Humility',zone='battlefield' if removal_first else 'hand')
                if removal_first:humility.zone_timestamp=0
                flight=self.add(engine,'Generic Setting Flight',zone='hand')
                action=self.ready(session,flight,{'W':2,'C':2});self.checkpoint(session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertIn('flying',{k.lower() for k in engine._effective_card_data(body)['keywords']})
                if not removal_first:
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==humility.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                    self.assertNotIn('flying',{k.lower() for k in engine._effective_card_data(body)['keywords']})
                self.replay(session,load=True)

    def test_actual_equipment_setting_tracks_reciprocal_recipient_and_reequip(self):
        session=self.session(284010);engine=session.engine
        first=self.add(engine,'Generic Setting Large Body',ref='first')
        second=self.add(engine,'Generic Setting Large Body',ref='second')
        source=self.add(engine,'Angelic Armaments')
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program=compile_best_available_card_program(self.db,self.db.lookup('Angelic Armaments'),semantic_registry=engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        engine.state.started=True;engine.state.active_player='A';engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['A'].mana_pool.update({'C':8});engine._grant_priority('A');engine.pump()
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('source')==source.ref)
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((8,9),(engine._numeric_stat(first.object_id,'power'),engine._numeric_stat(first.object_id,'toughness')))
        self.assertEqual({'construct','angel'},engine._type_parts(engine._effective_card_data(first)['type_line'])[1])
        self.assertIn('W',engine._effective_card_data(first)['colors'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('source')==source.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[second.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((6,7),(engine._numeric_stat(first.object_id,'power'),engine._numeric_stat(first.object_id,'toughness')))
        self.assertEqual((8,9),(engine._numeric_stat(second.object_id,'power'),engine._numeric_stat(second.object_id,'toughness')))
        self.assertEqual(second.object_id,source.attached_to)
        self.replay(session,load=True)

    def test_actual_counter_qualified_static_set_distinguishes_controller_and_enemy(self):
        session=self.session(284011);engine=session.engine
        marked=self.add(engine,'Generic Setting Large Body',ref='own-marked');marked.counters['+1/+1']=1
        unmarked=self.add(engine,'Generic Setting Large Body',ref='own-unmarked')
        enemy=self.add(engine,'Generic Setting Large Body',seat='B',ref='enemy-marked');enemy.counters['+1/+1']=1
        source=self.add(engine,"Sigarda's Summons",zone='hand')
        action=self.ready(session,source,{'W':2,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((5,5),(engine._numeric_stat(marked.object_id,'power'),engine._numeric_stat(marked.object_id,'toughness')))
        self.assertEqual((6,7),(engine._numeric_stat(unmarked.object_id,'power'),engine._numeric_stat(unmarked.object_id,'toughness')))
        self.assertEqual((7,8),(engine._numeric_stat(enemy.object_id,'power'),engine._numeric_stat(enemy.object_id,'toughness')))
        self.assertIn('angel',engine._type_parts(engine._effective_card_data(marked)['type_line'])[1])
        self.assertNotIn('angel',engine._type_parts(engine._effective_card_data(enemy)['type_line'])[1])
        self.replay(session,load=True)

    def test_static_setting_mutant_is_killed(self):
        from quorune.semantic_runtime import fixed_characteristic_settings as owner
        original=owner.fixed_characteristic_effects
        def omitted(node,context,*,common):
            return tuple(effect for effect in original(node,context,common=common) if effect.sublayer!='7b')
        with patch.object(owner,'fixed_characteristic_effects',side_effect=omitted):
            with self.assertRaises(AssertionError):self.test_actual_kudo_changes_other_creatures_across_controllers_with_replay()


if __name__=='__main__':unittest.main()
