from __future__ import annotations

"""CR305.7 and pinned rulings distinguish conversion from added land types."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_occurrences as controls


class ExpandedBasicTypeSettingTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedOccurrenceTests.session
    add=controls.AttachedOccurrenceTests.add
    prepare=controls.AttachedOccurrenceTests.prepare
    checkpoint=controls.AttachedOccurrenceTests.checkpoint
    resolve=controls.AttachedOccurrenceTests.resolve
    replay=controls.AttachedOccurrenceTests.replay
    binding=controls.AttachedOccurrenceTests.binding
    priority=controls.AttachedOccurrenceTests.priority
    cast=controls.AttachedOccurrenceTests.cast
    offered_cast=controls.AttachedOccurrenceTests.offered_cast
    private_replay=controls.AttachedOccurrenceTests.private_replay
    offered=controls.AttachedOccurrenceTests.offered
    advance=controls.AttachedOccurrenceTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'expanded-basic-setting.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/expanded-basic-type-setting-cards.json',ROOT/'tests/fixtures/fixed-basic-type-setting-cards.json',
            ROOT/'tests/fixtures/attached-occurrence-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Expanded basic setting',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_song_converts_foreign_creature_to_only_colorless_forest_and_restores(self):
        session=self.session(752000);source=self.add(session.engine,'Song of the Dryads',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        destroy=self.add(session.engine,'Naturalize',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Whole original Song must admit')
        self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session);self.offered_cast(session,source,{'G':1,'C':2},[body.ref]);self.assertIsNone(self.resolve(session))
        current=session.state.cards[body.object_id];data=session.engine._effective_card_data(current)
        kinds,subtypes,supertypes=session.engine._type_parts(data['type_line'])
        self.assertEqual({'land'},kinds);self.assertEqual({'forest'},subtypes);self.assertFalse(data['colors'])
        self.assertEqual(('B','B'),(current.owner,current.controller));self.assertEqual('Grizzly Bears',data['name'])
        self.priority(session,'B');mana=[a for a in self.offered(session,'B') if a.get('card')==body.ref or body.ref in a['id']]
        self.assertEqual(1,len(mana));result=session.act('pilot:B',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.players['B'].mana_pool.get('G',0))
        self.offered_cast(session,destroy,{'G':1,'C':1},[source.ref]);self.assertIsNone(self.resolve(session))
        restored=session.engine._effective_card_data(session.state.cards[body.object_id])
        self.assertEqual({'creature'},session.engine._type_parts(restored['type_line'])[0]);self.assertEqual({'G'},set(restored['colors']))
        self.assertEqual((2,2),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.private_replay(session,private)

    def test_complete_conversion_and_glaciers_register_their_upkeep_and_global_type_siblings(self):
        session=self.session(752001)
        for name in ('Conversion','Glaciers'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Whole original '+name+' must admit')

    def test_basic_subtype_scope_depends_on_an_effect_that_creates_its_applicability(self):
        from quorune.continuous_effects import CharacteristicState,ContinuousEffect,ContinuousOperation,Layer,evaluate_continuous_effects
        from quorune.object_predicate import ObjectQuerySpec
        for first_timestamp in (0,2):
            setter=ContinuousEffect('make-swamp','source-one',Layer.TYPE,'4',first_timestamp,
                (ContinuousOperation('set_basic_land_types',['swamp']),),applies=ObjectQuerySpec(types_all=('land',)))
            conversion=ContinuousEffect('swamp-to-plains','source-two',Layer.TYPE,'4',1,
                (ContinuousOperation('set_basic_land_types',['plains']),),applies=ObjectQuerySpec(types_all=('land',),subtypes_any=('swamp',)))
            result=evaluate_continuous_effects(CharacteristicState('Island',card_types={'land'},subtypes={'island'}),(conversion,setter))
            self.assertEqual({'plains'},set(result.characteristics['subtypes']))
            self.assertEqual(('make-swamp','swamp-to-plains'),result.applied_effects)

    def test_actual_conversion_and_glaciers_change_all_mountains_and_keep_full_upkeep_price(self):
        for index,(name,cast_payment,upkeep) in enumerate((('Conversion',{'W':2,'C':2},{'W':2}),('Glaciers',{'W':1,'U':1,'C':2},{'W':1,'U':1}))):
            session=self.session(752002+index);source=self.add(session.engine,name,zone='hand')
            mountain=self.add(session.engine,'Mountain',seat='B');snow=self.add(session.engine,'Snow-Covered Mountain',seat='D')
            island=self.add(session.engine,'Snow-Covered Island',seat='C');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana=cast_payment);self.checkpoint(session)
            self.offered_cast(session,source,cast_payment);self.assertIsNone(self.resolve(session))
            for card in (mountain,snow):
                data=session.engine._effective_card_data(session.state.cards[card.object_id]);self.assertEqual({'plains'},session.engine._type_parts(data['type_line'])[1])
                self.assertEqual({'intrinsic_plains'},{a.ability_id for a in session.engine._activated_abilities(session.state.cards[card.object_id])})
            self.assertIn('snow',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[snow.object_id])['type_line'])[2])
            self.assertEqual({'island'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[island.object_id])['type_line'])[1])
            self.priority(session,'B');actions=[a for a in self.offered(session,'B') if a.get('card')==mountain.ref or mountain.ref in a['id']]
            self.assertEqual(1,len(actions));result=session.act('pilot:B',{'action_id':actions[0]['id']});self.assertTrue(result.ok,result.summary)
            self.assertEqual((1,0),(session.state.players['B'].mana_pool.get('W',0),session.state.players['B'].mana_pool.get('R',0)))
            # Use canonical beginning-upkeep discovery without advancing through
            # unrelated turns; payment and its consequences use normal commands.
            session.state.players['A'].mana_pool.update(upkeep)
            session.engine._dispatch_semantic_event('step.begin',{'phase':'beginning','step':'upkeep','player':'A','active_player':'A'})
            from quorune.trigger_processing import begin_pending_trigger_batch
            begin_pending_trigger_batch(session.engine);self.checkpoint(session)
            decision=self.resolve(session);self.assertIsNotNone(decision);self.assertNotEqual('priority',decision.kind)
            from quorune.record import authoritative_state_hash
            before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:D',{'action_id':'choose','pay':True}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose','pay':True});self.assertTrue(result.ok,result.summary)
            self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
            self.assertTrue(all(session.state.players['A'].mana_pool.get(color,0)==0 for color in upkeep))
            self.private_replay(session,private)

    def test_extended_setting_codec_and_whole_sentence_grammar_preserve_old_shapes(self):
        from quorune.basic_type_setting_model import BasicTypeSettingSpec
        from quorune.compiler.basic_type_setting_templates import fixed_basic_type_setting_handler
        for scope in ('attached_land','nonbasic_lands','controller_lands','all_mountains','attached_colorless_forest_permanent'):
            spec=BasicTypeSettingSpec(scope,('forest',));self.assertEqual(spec,BasicTypeSettingSpec.from_dict(spec.to_dict()))
            self.assertEqual({'scope':scope,'basic_types':['forest']},spec.to_dict())
        for text in ('All Mountains are chosen land types.','All Mountains are Plains until end of turn.',
                'Enchanted permanent is a colorless Forest land and has flying.','Enchanted permanent is a Forest land.',
                'All nonbasic Mountains are Plains.'):
            self.assertIsNone(fixed_basic_type_setting_handler(text),text)
        for changed in ({'scope':'all_opponents_lands','basic_types':['forest']},
                {'scope':'attached_colorless_forest_permanent','basic_types':['island']},
                {'scope':'all_mountains','basic_types':['forest'],'extra':1}):
            with self.assertRaises(ValueError):BasicTypeSettingSpec.from_dict(changed)

    def test_song_turns_an_aura_into_unattached_land_then_restored_aura_dies(self):
        session=self.session(752004);song=self.add(session.engine,'Song of the Dryads',zone='hand')
        aura=self.add(session.engine,'Viridian Harvest',zone='hand');artifact=self.add(session.engine,'Darksteel Citadel')
        destroy=self.add(session.engine,'Naturalize',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':3,'C':3});self.checkpoint(session)
        self.offered_cast(session,aura,{'G':1},[artifact.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(artifact.object_id,session.state.cards[aura.object_id].attached_to)
        self.offered_cast(session,song,{'G':1,'C':2},[aura.ref]);self.assertIsNone(self.resolve(session))
        current=session.state.cards[aura.object_id];self.assertEqual('battlefield',current.zone);self.assertIsNone(current.attached_to)
        self.assertEqual(({'land'},{'forest'},set()),session.engine._type_parts(session.engine._effective_card_data(current)['type_line']))
        self.offered_cast(session,destroy,{'G':1,'C':1},[song.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone);self.assertEqual(40,session.state.players['A'].life)
        self.assertEqual('battlefield',session.state.cards[artifact.object_id].zone);self.private_replay(session,private)

    def test_declining_conversion_upkeep_sacrifices_source_and_restores_mountain(self):
        session=self.session(752005);source=self.add(session.engine,'Conversion',zone='hand');mountain=self.add(session.engine,'Mountain',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'W':2,'C':2});self.checkpoint(session)
        self.offered_cast(session,source,{'W':2,'C':2});self.assertIsNone(self.resolve(session))
        self.assertEqual({'plains'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[mountain.object_id])['type_line'])[1])
        session.engine._dispatch_semantic_event('step.begin',{'phase':'beginning','step':'upkeep','player':'A','active_player':'A'})
        from quorune.trigger_processing import begin_pending_trigger_batch
        begin_pending_trigger_batch(session.engine);self.checkpoint(session);decision=self.resolve(session);self.assertIsNotNone(decision)
        session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual({'mountain'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[mountain.object_id])['type_line'])[1])
        self.private_replay(session,private)

    def test_song_replaces_printed_types_and_abilities_but_keeps_supertypes_and_later_grants(self):
        from quorune.continuous_effects import CharacteristicState,ContinuousEffect,ContinuousOperation,Layer,evaluate_continuous_effects
        from quorune.semantic_runtime.basic_type_setting import FixedBasicTypeSettingHandler
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.compiler.basic_type_setting_templates import fixed_basic_type_setting_handler
        descriptor=fixed_basic_type_setting_handler('Enchanted permanent is a colorless Forest land.')[1]
        context=ContinuousEffectSourceContext('song','song-ref','A',1,'component',attached_object=ContinuousObjectIdentity('target','target@1'))
        effects=FixedBasicTypeSettingHandler().lower(descriptor,context)
        grant=ContinuousEffect('external-flying','other-source',Layer.ABILITY,'6',0,(ContinuousOperation('add_ability','Flying'),))
        result=evaluate_continuous_effects(CharacteristicState('Unchanged',card_types={'artifact','creature'},subtypes={'construct'},
            supertypes={'legendary','snow'},colors={'U'},abilities=['Trample'],power=4,toughness=5),(*effects,grant),
            context={'object_id':'target','logical_object_id':'target@1'})
        values=result.characteristics;self.assertEqual({'land'},set(values['card_types']));self.assertEqual({'forest'},set(values['subtypes']))
        self.assertEqual({'legendary','snow'},set(values['supertypes']));self.assertFalse(values['colors'])
        self.assertEqual(['Flying'],values['abilities']);self.assertEqual('Unchanged',values['name'])
        self.assertEqual(['intrinsic_forest'],[a['ability_id'] for a in values['activated_abilities']])

    def test_unrelated_attached_type_setter_creates_no_dependency_for_this_object(self):
        from quorune.continuous_effects import CharacteristicState,ContinuousEffect,ContinuousOperation,Layer
        from quorune.continuous_effect_model import ContinuousObjectIdentity,ContinuousEffectRelation,ContinuousEffectOrigin
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.continuous_type_dependencies import bind_type_applicability_dependencies
        setter=ContinuousEffect('other-setter','source',Layer.TYPE,'4',2,(ContinuousOperation('set_basic_land_types',['swamp']),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,related_object=ContinuousObjectIdentity('different','different@1'))
        reader=ContinuousEffect('reader','source-two',Layer.TYPE,'4',1,(ContinuousOperation('set_basic_land_types',['plains']),),
            applies=ObjectQuerySpec(types_all=('land',),subtypes_any=('swamp',)))
        result=bind_type_applicability_dependencies((reader,setter),CharacteristicState('Island',card_types={'land'},subtypes={'island'}),
            {'object_id':'target','logical_object_id':'target@1'})
        self.assertEqual((),result[0].depends_on)

    def test_runtime_setting_owner_is_required_after_full_original_compilation(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Song of the Dryads','Conversion','Glaciers'):
            session=self.session(752006);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(d.get('handler_id')=='continuous.basic_land_type.fixed-setting.v1' for d in p.handlers)]
            self.assertEqual(1,len(programs));program=programs[0];cap='continuous.basic_land_type.fixed_setting'
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict'],name);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_verified_setting_and_intrinsic_mana_withdrawals_block_all_three_originals(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'));known=set(discover_tests(ROOT).values())
        for cap in ('continuous.basic_land_type.fixed_setting','mana.intrinsic.basic_land_type'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn expanded basic setting owner'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ('Song of the Dryads','Conversion','Glaciers'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_compiler_lowerer_and_type_dependency_omissions_are_killed(self):
        from unittest.mock import patch
        cases=(('quorune.compiler.basic_type_setting_templates.fixed_basic_type_setting_handler',lambda *a:None,
                'test_actual_song_converts_foreign_creature_to_only_colorless_forest_and_restores'),
            ('quorune.semantic_runtime.basic_type_setting.FixedBasicTypeSettingHandler.lower',lambda *a:(),
                'test_actual_song_converts_foreign_creature_to_only_colorless_forest_and_restores'),
            ('quorune.continuous_type_dependencies.bind_type_applicability_dependencies',lambda effects,*a:tuple(effects),
                'test_basic_subtype_scope_depends_on_an_effect_that_creates_its_applicability'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_partial_upkeep_mana_cannot_pay_conversion_and_rejection_is_atomic(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752007);source=self.add(session.engine,'Conversion',zone='hand');self.add(session.engine,'Mountain',seat='B')
        self.prepare(session,mana={'W':2,'C':2});self.checkpoint(session);self.offered_cast(session,source,{'W':2,'C':2});self.assertIsNone(self.resolve(session))
        session.state.players['A'].mana_pool.update({'W':1})
        session.engine._dispatch_semantic_event('step.begin',{'phase':'beginning','step':'upkeep','player':'A','active_player':'A'})
        from quorune.trigger_processing import begin_pending_trigger_batch
        begin_pending_trigger_batch(session.engine);self.checkpoint(session);decision=self.resolve(session);self.assertIsNotNone(decision)
        before=authoritative_state_hash(session.state);result=session.act('pilot:A',{'action_id':'choose','pay':True})
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.replay(session,load=True)

    def test_explicit_type_dependency_cycle_keeps_timestamp_fallback(self):
        from quorune.continuous_effects import CharacteristicState,ContinuousEffect,ContinuousOperation,Layer,evaluate_continuous_effects
        from quorune.object_predicate import ObjectQuerySpec
        first=ContinuousEffect('first','source-one',Layer.TYPE,'4',1,(ContinuousOperation('set_basic_land_types',['swamp']),),
            depends_on=('second',),applies=ObjectQuerySpec(types_all=('land',),subtypes_any=('island',)))
        second=ContinuousEffect('second','source-two',Layer.TYPE,'4',2,(ContinuousOperation('set_basic_land_types',['plains']),),
            depends_on=('first',),applies=ObjectQuerySpec(types_all=('land',),subtypes_any=('swamp',)))
        result=evaluate_continuous_effects(CharacteristicState('Island',card_types={'land'},subtypes={'island'}),(second,first))
        self.assertEqual({'plains'},set(result.characteristics['subtypes']));self.assertEqual((('first','second'),),result.dependency_cycles)
