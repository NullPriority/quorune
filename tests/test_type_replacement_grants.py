"""CR205.1a and pinned Aura rulings: types, correlated subtypes and abilities."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_composite_quoted_grants as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.composite_grants import TYPE_REPLACEMENT_GRANT_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database


MOON='Enchanted permanent is a colorless land with "{T}: Add {C}" and loses all other card types and abilities.'
TREASURE='Enchanted permanent is a Treasure artifact with "{T}, Sacrifice this artifact: Add one mana of any color," and it loses all other abilities.'
FOOD='Enchanted permanent is a colorless Food artifact with "{2}, {T}, Sacrifice this artifact: You gain 3 life" and loses all other card types and abilities.'
STARS='Enchanted permanent is an enchantment and loses all other card types.'


class TypeReplacementGrantCompilerTests(unittest.TestCase):
    def test_version_two_descriptor_locks_recipient_and_rejects_unrepresented_operations(self):
        from quorune.semantic_runtime.composite_grants import FixedTypeReplacementGrantHandler,CompositeAbilityGrantHandler
        from quorune.continuous_effect_model import ContinuousObjectIdentity,Layer
        ir=compile_oracle_card(query_record(MOON),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        descriptor=ir.faces[0].nodes[0].handlers[0];handler=FixedTypeReplacementGrantHandler()
        identity=ContinuousObjectIdentity('recipient','recipient:1')
        context=SimpleNamespace(source_object_id='giver',source_ref='giver-ref',source_controller='A',source_timestamp=9,
            component_id='component',attached_object=identity)
        effects=handler.lower(descriptor,context)
        self.assertEqual((Layer.TYPE,Layer.COLOR,Layer.ABILITY),tuple(effect.layer for effect in effects))
        self.assertEqual({identity},{effect.related_object for effect in effects})
        with self.assertRaises(ValueError):CompositeAbilityGrantHandler().validate(descriptor)
        for mutate in (lambda value:value.update(schema_version=True),lambda value:value.update(unexpected=None),
                lambda value:value['target']['predicate'].update(types_all=['creature']),
                lambda value:value['modifier']['type_operations'][0].update(values=['Creature']),
                lambda value:value['modifier'].update(color_operations=[{'op':'set_colors','values':['G']}]),
                lambda value:value['modifier']['type_operations'].append({'op':'remove_types','field':'card_types','values':['Land']})):
            changed=deepcopy(descriptor);mutate(changed)
            with self.assertRaises(ValueError):handler.validate(changed)
    def test_complete_replacement_quotes_and_plain_type_change_use_explicit_new_handler(self):
        registry=load_default_capability_registry()
        for text,count in ((MOON,1),(TREASURE,1),(FOOD,1),(STARS,0)):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                parent,*children=ir.faces[0].nodes;self.assertEqual(count,len(children))
                self.assertEqual(2,parent.handlers[0]['schema_version']);self.assertIn(CAPABILITY,parent.capability_dependencies)
                self.assertEqual([],parent.handlers[0]['target']['predicate']['types_all'])
        for text in (MOON.replace('colorless land','chosen permanent'),MOON.replace('Add {C}','Rewrite history'),
                STARS+' Draw a card.',STARS.replace('Enchanted permanent','Creatures you control'),
                MOON.replace('land with','Forest land with')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)

    def test_type_replacement_cleanup_retains_only_correlated_subtypes_and_independent_supertypes(self):
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer
        for kind,expected in (('Land',{'Forest'}),('Artifact',{'Food'}),('Enchantment',{'Shrine'}),
                ('Creature',{'Elf'}),('Kindred',{'Elf'}),('Planeswalker',{'Jace'})):
            base=CharacteristicState(name='Generic replacement object',card_types={'Artifact','Creature','Land','Enchantment','Planeswalker','Kindred'},
                subtypes={'Elf','Food','Forest','Shrine','Jace'},supertypes={'Legendary','Snow'})
            effect=ContinuousEffect('replace','giver',Layer.TYPE,'4',1,(ContinuousOperation('set_types',[kind],field='card_types'),))
            result=evaluate_continuous_effects(base,(effect,)).characteristics
            self.assertEqual(expected,set(result['subtypes']),kind);self.assertEqual({'Legendary','Snow'},set(result['supertypes']))
        base=CharacteristicState(name='Generic instant object',card_types={'Instant','Kindred'},subtypes={'Arcane','Elf'})
        effect=ContinuousEffect('replace','giver',Layer.TYPE,'4',1,(ContinuousOperation('set_types',['Land'],field='card_types'),))
        result=evaluate_continuous_effects(base,(effect,)).characteristics
        self.assertEqual({'Instant','Land'},set(result['card_types']));self.assertEqual({'Arcane'},set(result['subtypes']))

    def test_verified_replacement_and_child_owners_withdraw_with_unrelated_positive_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner in (CAPABILITY,'continuous.ability.fixed_composite_grant','mana.activated.fixed_output','variant.monarch.designate'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['replacement owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(query_record(MOON),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',owner)


class TypeReplacementGrantNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.CompositeQuotedGrantNativeTests.session
    add=native.CompositeQuotedGrantNativeTests.add
    prepare=native.CompositeQuotedGrantNativeTests.prepare
    cast=native.CompositeQuotedGrantNativeTests.cast
    cast_for=native.CompositeQuotedGrantNativeTests.cast_for
    action=native.CompositeQuotedGrantNativeTests.action
    priority=native.CompositeQuotedGrantNativeTests.priority
    checkpoint=native.CompositeQuotedGrantNativeTests.checkpoint
    resolve=native.CompositeQuotedGrantNativeTests.resolve
    replay=native.CompositeQuotedGrantNativeTests.replay
    assert_closed=native.CompositeQuotedGrantNativeTests.assert_closed
    activate_mana=native.CompositeQuotedGrantNativeTests.activate_mana
    granted_action=native.CompositeQuotedGrantNativeTests.granted_action

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'type-replacement.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/type-replacement-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Replacement types',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def parts(self,session,card):
        return session.engine._type_parts(session.engine._effective_card_data(session.state.cards[card.object_id])['type_line'])

    def test_actual_moon_preserves_land_types_and_supertypes_detaches_equipment_and_restores_on_departure(self):
        for index,name in enumerate(('Isamaru, Hound of Konda','Island')):
            with self.subTest(target=name):
                session=self.session(205619001+index)
                aura=self.add(session.engine,'Imprisoned in the Moon',zone='hand')
                target=self.add(session.engine,name,seat='B',ref='moon-target');target.counters['+1/+1']=1
                removal=self.add(session.engine,'Disenchant',zone='hand')
                if index==0:
                    equipment=self.add(session.engine,'Short Sword',seat='B');equipment.attached_to=target.object_id;target.attachments.append(equipment.object_id)
                private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
                self.assert_closed(session,aura);self.prepare(session,mana={'C':3,'U':1,'W':1});self.checkpoint(session)
                self.cast_for(session,'A',aura,[target.ref],reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
                types,subtypes,supertypes=self.parts(session,target)
                self.assertEqual({'land'},set(types));self.assertEqual({'island'} if index else set(),set(subtypes))
                self.assertIn('basic' if index else 'legendary',supertypes)
                self.assertEqual(1,session.state.cards[target.object_id].counters['+1/+1'])
                self.assertEqual(0,session.engine._numeric_stat(target.object_id,'power'))
                if index==0:self.assertIsNone(session.state.cards[equipment.object_id].attached_to)
                self.activate_mana(session,'B',target,'C')
                self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session))
                self.assertIn('land' if index else 'creature',self.parts(session,target)[0])
                self.assertEqual(1,session.state.players['B'].mana_pool['C'])
                for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
                self.replay(session,load=True)

    def test_actual_containment_sacrifices_nontoken_treasure_for_its_controller_and_keeps_colors(self):
        session=self.session(205619010)
        aura=self.add(session.engine,'Minimus Containment',zone='hand')
        target=self.add(session.engine,'Llanowar Elves',seat='B')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
        self.cast(session,'A',aura,[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual({'artifact'},set(self.parts(session,target)[0]));self.assertEqual({'treasure'},set(self.parts(session,target)[1]))
        self.assertEqual(['G'],session.engine._effective_card_data(session.state.cards[target.object_id])['colors'])
        self.assertFalse(session.state.cards[target.object_id].is_token)
        self.activate_mana(session,'B',target,'R')
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.assertEqual(1,session.state.players['B'].mana_pool['R']);self.replay(session,load=True)

    def test_actual_sugar_cost_and_stacked_life_gain_survive_source_and_aura_departure(self):
        session=self.session(205619020)
        aura=self.add(session.engine,'Sugar Coat',zone='hand')
        target=self.add(session.engine,'Llanowar Elves',seat='B')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':2,'U':1})
        session.state.players['B'].mana_pool['C']=2;self.checkpoint(session)
        self.cast(session,'A',aura,[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(({'artifact'},{'food'}),tuple(set(part) for part in self.parts(session,target)[:2]))
        self.assertEqual([],session.engine._effective_card_data(session.state.cards[target.object_id])['colors'])
        self.priority(session,'B');offered=self.granted_action(session,'B',target)
        command={'action_id':offered['id'],'pay':'auto'};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertTrue(session.state.stack)
        self.assertEqual(40,session.state.players['B'].life);self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((40,43),(session.state.players['A'].life,session.state.players['B'].life));self.replay(session,load=True)

    def test_actual_stars_keeps_printed_mana_and_color_without_creature_stats(self):
        session=self.session(205619030)
        aura=self.add(session.engine,'One with the Stars',zone='hand')
        target=self.add(session.engine,'Llanowar Elves',seat='B')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',aura,[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(({'enchantment'},set()),tuple(set(part) for part in self.parts(session,target)[:2]))
        self.assertEqual(['G'],session.engine._effective_card_data(session.state.cards[target.object_id])['colors'])
        self.assertEqual(0,session.engine._numeric_stat(target.object_id,'power'))
        self.priority(session,'B');ability=next(a for a in session.engine._activated_abilities(session.state.cards[target.object_id]) if a.mana_ability)
        result=session.act('pilot:B',{'action_id':'activate:'+target.ref+':'+ability.ability_id,'mana_output':{'G':1}})
        self.assertTrue(result.ok,result.summary);self.assertEqual(1,session.state.players['B'].mana_pool['G']);self.replay(session,load=True)

    def test_actual_deep_retains_split_second_then_a_tapped_nontoken_clue_draws_for_its_controller(self):
        session=self.session(205619060)
        aura=self.add(session.engine,'In Too Deep',zone='hand')
        target=self.add(session.engine,'Llanowar Elves',seat='B');target.tapped=True
        response=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        island=self.add(session.engine,'Island',seat='B')
        self.assert_closed(session,aura);self.prepare(session,mana={'U':2})
        session.state.players['B'].mana_pool['C']=2;self.checkpoint(session)
        self.cast(session,'A',aura,[target.ref]);self.priority(session,'B')
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id']=='cast:'+response.ref for row in actions))
        self.assertTrue(any(row['id'].startswith('activate:'+island.ref+':') for row in actions))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':'cast:'+response.ref,'targets':[target.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(({'artifact'},{'clue'}),tuple(set(part) for part in self.parts(session,target)[:2]))
        self.assertTrue(session.state.cards[target.object_id].tapped);self.assertFalse(session.state.cards[target.object_id].is_token)
        self.priority(session,'B');offered=self.granted_action(session,'B',target)
        hand=len(session.state.players['B'].zones['hand'])
        result=session.act('pilot:B',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual(hand,len(session.state.players['B'].zones['hand']))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+1,len(session.state.players['B'].zones['hand']))
        drawn=session.state.cards[session.state.players['B'].zones['hand'][-1]].ref
        for seat in 'ACD':self.assertNotIn(drawn,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(drawn,json.dumps(session.packet('pilot:B',full=True)));self.replay(session,load=True)

    def test_runtime_type_replacement_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(205619040);aura=self.add(session.engine,'Imprisoned in the Moon',zone='hand');checked=0
        for program in session.engine.semantics.programs_for_oracle(aura.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(cap for cap in program.capability_dependencies if cap!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.assertGreater(checked,0)

    def test_omitted_correlated_cleanup_mutant_is_killed_by_original_moon(self):
        import quorune.type_subtype_vocabulary as owner
        session=self.session(205619050)
        target=self.add(session.engine,'Isamaru, Hound of Konda',seat='B',ref='mutation-moon-target')
        aura=self.add(session.engine,'Imprisoned in the Moon');aura.attached_to=target.object_id;target.attachments.append(aura.object_id)
        self.assert_closed(session,aura);self.prepare(session);self.checkpoint(session)
        self.assertFalse(self.parts(session,target)[1])
        with patch.object(owner,'correlated_subtypes',side_effect=lambda subtypes,types:set(subtypes)),self.assertRaises(AssertionError):
            self.assertFalse(self.parts(session,target)[1])
