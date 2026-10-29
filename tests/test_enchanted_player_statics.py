"""Pinned player Aura rulings: current scope, layered characteristics and attack rules."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_type_replacement_grants as native
import test_ring_native_actions as turns
from quorune.ability_fragments import (ability_fragment_from_dict,ability_fragment_to_dict,
    AllCreatureTypesCharacteristicDefinitionSpec,ActivationProhibitionSpec)
from quorune.attachments import attach_to_player,current_attached_player_seat
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.enchanted_player_model import (EnchantedPlayerAttackRequirementSpec,
    ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY as CHARACTERISTICS,ENCHANTED_PLAYER_ATTACK_CAPABILITY as ATTACK)
from scripts.build_test_database import build_fixture_database

SPLENDOR='Creatures enchanted player controls lose all abilities and have base power and toughness 1/1.'
CONFORMITY='Nonlegendary creatures enchanted player controls have base power and toughness 3/3 and lose all creature types.'
HOLD='Creatures enchanted player controls get -1/-1.'
HUNT='Creatures enchanted player controls attack each combat if able.'
ARREST="Enchanted creature can't attack or block, and its activated abilities can't be activated."
ATTACHED_ACTIVATION='activation.restriction.attached_static_rule'


class EnchantedPlayerStaticCompilerTests(unittest.TestCase):
    def test_bare_and_compound_attached_restrictions_stay_on_the_giver(self):
        from quorune.compiler.conditional_declaration_grants import conditional_query_declaration_grant
        from quorune.compiler.public_state_queries import fixed_public_state_parts
        for text in ("Enchanted creature can't attack or block.","Enchanted creature gets +1/+1 and can't block."):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            handlers=ir.faces[0].nodes[0].handlers
            owned=[h['fragment'] for h in handlers if h.get('fragment',{}).get('kind')=='declaration_restriction']
            self.assertEqual(1,len(owned));self.assertEqual('attached',owned[0]['value']['scope'])
            self.assertFalse(any(h.get('modifier',{}).get('add_ability_fragments') for h in handlers))
        condition,body=fixed_public_state_parts("During your turn, enchanted creature can't block.",source_name='Fixture')
        result=conditional_query_declaration_grant(condition,body,source_name='Fixture')
        self.assertIsNotNone(result)
        spec=ability_fragment_from_dict(result[1]['fragment'])
        self.assertEqual('attached',spec.scope);self.assertEqual(condition,spec.source_condition)

    def test_complete_static_scopes_preserve_spans_and_independent_owners(self):
        registry=load_default_capability_registry()
        for text,owner in ((SPLENDOR,CHARACTERISTICS),(CONFORMITY,CHARACTERISTICS),(HOLD,CHARACTERISTICS),(HUNT,ATTACK),(ARREST,ATTACHED_ACTIVATION)):
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
            self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end));self.assertIn(owner,node.capability_dependencies)
        for text in (HUNT.replace('each combat','once'),CONFORMITY.replace('Nonlegendary','Chosen'),HOLD+' Draw a card.',
                SPLENDOR.replace('1/1','X/X'),SPLENDOR.replace('Creatures','Artifacts'),
                HOLD.replace('enchanted player','target player'),SPLENDOR.replace('lose all abilities','have flying and lose all abilities')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def test_closed_descriptors_cannot_bind_players_or_widen_legacy_scope(self):
        from quorune.semantic_runtime.enchanted_player_characteristics import EnchantedPlayerCharacteristicsHandler
        from quorune.semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.semantic_runtime.context import SemanticNodeError
        ir=compile_oracle_card(query_record(CONFORMITY),capability_registry=load_default_capability_registry())
        descriptor=ir.faces[0].nodes[0].handlers[0];handler=EnchantedPlayerCharacteristicsHandler()
        context=ContinuousEffectSourceContext('giver','aura-ref','A',8,'component',enchanted_player='B')
        effects=handler.lower(descriptor,context);self.assertEqual(2,len(effects))
        self.assertTrue(all(e.applies.controller=='B' for e in effects));self.assertTrue(all(e.applies.excluded_supertypes==('legendary',) for e in effects))
        self.assertEqual((),handler.lower(descriptor,replace(context,enchanted_player=None)))
        for mutate in (lambda v:v.update(schema_version=True),lambda v:v.update(player='B'),
                lambda v:v['target'].update(player='B'),lambda v:v['target'].update(nonlegendary=1),
                lambda v:v['modifier'].update(quantity={'choose':'all'})):
            changed=deepcopy(descriptor);mutate(changed)
            with self.assertRaises(ValueError):handler.validate(changed)
        with self.assertRaises(SemanticNodeError):FixedCharacteristicSettingsHandler().validate(descriptor)
        spec=EnchantedPlayerAttackRequirementSpec();self.assertEqual(spec,ability_fragment_from_dict(ability_fragment_to_dict(spec)))
        for value in ({'schema_version':True},{'schema_version':2},{'schema_version':1,'player':'B'}):
            with self.assertRaises(ValueError):EnchantedPlayerAttackRequirementSpec.from_dict(value)
        self.assertEqual({'schema_version':1,'scope':'all'},ActivationProhibitionSpec('all').to_dict())
        rule=ActivationProhibitionSpec('all',schema_version=2,relation='attached')
        self.assertEqual(rule,ActivationProhibitionSpec.from_dict(rule.to_dict()))
        for value in ({'schema_version':1,'scope':'all','relation':'self'},
                {'schema_version':2,'scope':'all'},{'schema_version':2,'scope':[],'relation':'attached'},
                {'schema_version':2,'scope':'all','relation':'self'}):
            with self.assertRaises(ValueError):ActivationProhibitionSpec.from_dict(value)

    def test_verified_scope_and_modifier_withdrawals_fail_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner,text in ((CHARACTERISTICS,SPLENDOR),('continuous.characteristics.fixed_public_setting',SPLENDOR),
                (ATTACK,HUNT),('combat.declaration.typed_components',HUNT),(ATTACHED_ACTIVATION,ARREST),('variant.monarch.designate',SPLENDOR)):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['player scope owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',owner)


class EnchantedPlayerStaticNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.TypeReplacementGrantNativeTests.session
    add=native.TypeReplacementGrantNativeTests.add
    prepare=native.TypeReplacementGrantNativeTests.prepare
    cast=native.TypeReplacementGrantNativeTests.cast
    cast_for=native.TypeReplacementGrantNativeTests.cast_for
    action=native.TypeReplacementGrantNativeTests.action
    priority=native.TypeReplacementGrantNativeTests.priority
    checkpoint=native.TypeReplacementGrantNativeTests.checkpoint
    resolve=native.TypeReplacementGrantNativeTests.resolve
    replay=native.TypeReplacementGrantNativeTests.replay
    assert_closed=native.TypeReplacementGrantNativeTests.assert_closed
    until=turns.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'enchanted-player.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/enchanted-player-static-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Enchanted player',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def stats(self,session,card):return tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness'))

    def activate_mana(self,session,seat,card,color):
        self.priority(session,seat)
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[card.object_id]) if a.mana_ability)
        bundle=next(dict(output.bundle) for output in ability.fixed_mana_outputs if output.bundle.get(color,0)>0)
        result=session.act('pilot:'+seat,{'action_id':'activate:'+card.ref+':'+ability.ability_id,'mana_output':bundle,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_actual_splendor_scopes_current_controller_suppresses_entry_and_keeps_noncreature_mana(self):
        session=self.session(303621001);aura=self.add(session.engine,'Overwhelming Splendor',zone='hand')
        body=self.add(session.engine,'Isamaru, Hound of Konda',seat='B');body.counters['+1/+1']=1
        own=self.add(session.engine,'Grizzly Bears');elf=self.add(session.engine,'Llanowar Elves',seat='B')
        ring=self.add(session.engine,'Sol Ring',seat='B');equipment=self.add(session.engine,'Short Sword',seat='B')
        self.add(session.engine,'Vedalken Orrery',seat='B');visionary=self.add(session.engine,'Elvish Visionary',seat='B',zone='hand')
        ballista=self.add(session.engine,'Walking Ballista',seat='B',zone='hand')
        control=self.add(session.engine,'Act of Treason',zone='hand');removal=self.add(session.engine,'Disenchant',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':9,'W':3,'R':1});session.state.players['B'].mana_pool.update({'C':1,'G':1});self.checkpoint(session)
        self.cast_for(session,'A',aura,['B'],reserve={'C':3,'W':1,'R':1});self.assertIsNone(self.resolve(session))
        self.assertEqual('B',current_attached_player_seat(session.state.cards[aura.object_id],session.state.players))
        self.assertEqual((2,2),self.stats(session,body));self.assertEqual((2,2),self.stats(session,own))
        self.assertFalse(session.engine._activated_abilities(session.state.cards[elf.object_id]))
        self.priority(session,'B');actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('activate:'+equipment.ref+':') for a in actions))
        self.activate_mana(session,'B',ring,'C')
        hand=len(session.state.players['B'].zones['hand']);self.cast(session,'B',visionary);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand-1,len(session.state.players['B'].zones['hand']));self.assertEqual((1,1),self.stats(session,visionary))
        self.priority(session,'B');offered=self.action(session,'B',session.state.cards[ballista.object_id])
        result=session.act('pilot:B',{'action_id':offered['id'],'x':1,'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual({},session.state.cards[ballista.object_id].counters);self.assertEqual((1,1),self.stats(session,ballista))
        self.cast_for(session,'A',control,[body.ref],reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual('A',session.state.cards[body.object_id].controller);self.assertEqual((3,3),self.stats(session,body))
        self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.engine._activated_abilities(session.state.cards[elf.object_id]))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_conformity_removes_changeling_types_but_preserves_forest_and_legendary_creatures(self):
        session=self.session(303621010);aura=self.add(session.engine,'Curse of Conformity',zone='hand')
        automaton=self.add(session.engine,'Universal Automaton',seat='B');automaton.counters['+1/+1']=1
        arbor=self.add(session.engine,'Dryad Arbor',seat='B');arbor.acquired_control_turn_count=-1
        legend=self.add(session.engine,'Isamaru, Hound of Konda',seat='B');other=self.add(session.engine,'Grizzly Bears')
        token=self.add(session.engine,'Memnite',seat='B');token.is_token=True;token.object_kind='token'
        removal=self.add(session.engine,'Disenchant',zone='hand')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':5,'W':2});self.checkpoint(session)
        self.cast_for(session,'A',aura,['B'],reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((4,4),self.stats(session,automaton));self.assertEqual((3,3),self.stats(session,arbor));self.assertEqual((3,3),self.stats(session,token))
        self.assertEqual((2,2),self.stats(session,legend));self.assertEqual((2,2),self.stats(session,other))
        data=session.engine._effective_card_data(session.state.cards[automaton.object_id]);self.assertEqual(set(),set(session.engine._type_parts(data['type_line'])[1]))
        self.assertTrue(any(isinstance(f,AllCreatureTypesCharacteristicDefinitionSpec)
            for f in session.engine._effective_ability_fragments(session.state.cards[automaton.object_id])))
        types,subtypes,_=session.engine._type_parts(session.engine._effective_card_data(session.state.cards[arbor.object_id])['type_line'])
        self.assertEqual({'creature','land'},set(types));self.assertEqual({'forest'},set(subtypes))
        self.activate_mana(session,'B',arbor,'G')
        self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((2,2),self.stats(session,automaton));self.assertIn('elf',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[automaton.object_id])['type_line'])[1])
        self.replay(session,load=True)

    def test_actual_deaths_hold_applies_additive_delta_only_to_enchanted_player_and_stabilizes_death(self):
        session=self.session(303621020);aura=self.add(session.engine,"Curse of Death's Hold",zone='hand')
        elf=self.add(session.engine,'Llanowar Elves',seat='B');bear=self.add(session.engine,'Grizzly Bears',seat='B');bear.counters['+1/+1']=1
        own=self.add(session.engine,'Llanowar Elves',ref='unaffected-owner-elf');removal=self.add(session.engine,'Disenchant',zone='hand')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':4,'B':2,'W':1});self.checkpoint(session)
        self.cast_for(session,'A',aura,['B'],reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[elf.object_id].zone);self.assertEqual((2,2),self.stats(session,bear));self.assertEqual((1,1),self.stats(session,own))
        self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session));self.assertEqual((3,3),self.stats(session,bear));self.replay(session,load=True)

    def test_actual_nightly_hunt_forces_only_able_attacks_after_receiver_ability_loss_and_keeps_defender_choice(self):
        session=self.session(303621030);aura=self.add(session.engine,'Curse of the Nightly Hunt',zone='hand')
        self.add(session.engine,'Vedalken Orrery');bear=self.add(session.engine,'Grizzly Bears',seat='B');bear.acquired_control_turn_count=-1
        tapped=self.add(session.engine,'Llanowar Elves',seat='B');tapped.tapped=True;tapped.acquired_control_turn_count=-1
        sick=self.add(session.engine,'Memnite',seat='B');sick.acquired_control_turn_count=session.state.players['B'].turns_begun
        pacified=self.add(session.engine,'Isamaru, Hound of Konda',seat='B');pacified.acquired_control_turn_count=-1
        pacifism=self.add(session.engine,'Pacifism',seat='C');pacifism.attached_to=pacified.object_id;pacified.attachments.append(pacifism.object_id)
        defender=self.add(session.engine,'Grizzly Bears',seat='B',ref='defender-grant-body');defender.acquired_control_turn_count=-1
        guard=self.add(session.engine,'Guard Duty',seat='D');guard.attached_to=defender.object_id;defender.attachments.append(guard.object_id)
        dress=self.add(session.engine,'Dress Down',seat='B',zone='hand');private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,aura);self.prepare(session,actor='A',active='B',mana={'C':2,'R':1});session.state.players['B'].mana_pool.update({'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',aura,['B']);self.assertIsNone(self.resolve(session));self.cast(session,'B',dress);self.assertIsNone(self.resolve(session))
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'attack','attackers':{}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{'action_id':'attack','attackers':{bear.ref:'C'}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:B',{'action_id':'attack','attackers':{bear.ref:'C'}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'attack','attackers':{bear.ref:'C',defender.ref:'D'}});self.assertTrue(result.ok,result.summary)
        self.assertEqual({bear.object_id,defender.object_id},set(session.state.combat.attackers))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_splendor_and_creature_depart_together_without_restoring_lookback_death_trigger(self):
        session=self.session(303622035);aura=self.add(session.engine,'Overwhelming Splendor',zone='hand')
        body=self.add(session.engine,'Doomed Traveler',seat='B');sweep=self.add(session.engine,'Planar Cleansing',zone='hand')
        self.assert_closed(session,aura);self.assert_closed(session,body);self.assert_closed(session,sweep)
        self.prepare(session,mana={'C':9,'W':5});self.checkpoint(session)
        self.cast_for(session,'A',aura,['B'],reserve={'C':3,'W':3});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',sweep);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone);self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertFalse(any(c.zone=='battlefield' and c.is_token for c in session.state.cards.values()));self.replay(session,load=True)

    def test_generated_trigger_component_obeys_current_presence_and_unmarked_program_contract_is_explicit(self):
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE,static_component_keys
        from quorune.trigger_discovery import _program_trigger_ability_is_present
        session=self.session(303622037);body=self.add(session.engine,'Elvish Visionary',seat='B')
        program=next(p for p in session.engine.semantics.programs_for_oracle(body.oracle_id) if p.event=='permanent.enter.self')
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE,program.coverage)
        data=session.engine._effective_card_data(body);self.assertIn(program.key,static_component_keys(data['ability_fragments']))
        self.assertTrue(_program_trigger_ability_is_present(program,data))
        aura=self.add(session.engine,'Overwhelming Splendor');attach_to_player(session.state.cards,session.state.players,aura,'B',source_timestamp=session.engine._next_zone_timestamp())
        data=session.engine._effective_card_data(body)
        def witness():self.assertFalse(_program_trigger_ability_is_present(program,data))
        witness()
        with patch('quorune.trigger_discovery.program_has_current_ability_fragments',return_value=True),self.assertRaises(AssertionError):witness()
        historical=replace(program,coverage=[c for c in program.coverage if c!=CURRENT_ABILITY_FRAGMENT_COVERAGE])
        self.assertTrue(_program_trigger_ability_is_present(historical,data))
        self.assertNotIn(CURRENT_ABILITY_FRAGMENT_COVERAGE,historical.to_dict()['coverage'])

    def test_runtime_scope_owners_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(303621040)
        for name,owner in (('Curse of Conformity',CHARACTERISTICS),('Curse of the Nightly Hunt',ATTACK),('Arrest',ATTACHED_ACTIVATION)):
            source=self.add(session.engine,name);checked=0
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if owner not in program.capability_dependencies:continue
                checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=owner)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+owner,bound['blockers'])
            self.assertGreater(checked,0)

    def test_actual_arrest_giver_blocks_later_granted_activation_after_receiver_blanking_until_removal(self):
        session=self.session(303623041);body=self.add(session.engine,'Llanowar Elves',seat='B');body.tapped=True;body.acquired_control_turn_count=-1
        self.add(session.engine,'Dress Down');equipment=self.add(session.engine,'Umbral Mantle',seat='B')
        equipment.attached_to=body.object_id;body.attachments.append(equipment.object_id)
        self.add(session.engine,'Vedalken Orrery');aura=self.add(session.engine,'Arrest',zone='hand');removal=self.add(session.engine,'Disenchant',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand',ref='arrest-private')
        self.assert_closed(session,aura);self.prepare(session,actor='A',active='B',mana={'C':3,'W':2});session.state.players['B'].mana_pool['C']=3;self.checkpoint(session)
        self.cast_for(session,'A',aura,[body.ref],reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session));self.priority(session,'B')
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[body.object_id]) if a.untap_source)
        command={'action_id':'activate:'+body.ref+':'+ability.ability_id,'pay':'auto'}
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'];self.assertNotIn(command['action_id'],[a['id'] for a in actions])
        self.assertFalse(any(isinstance(f,ActivationProhibitionSpec) for f in session.engine._effective_ability_fragments(session.state.cards[body.object_id])))
        self.assertTrue(any(isinstance(f,ActivationProhibitionSpec) and f.relation=='attached' for f in session.engine._effective_ability_fragments(session.state.cards[aura.object_id])))
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session));self.priority(session,'B')
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'];self.assertIn(command['action_id'],[a['id'] for a in actions])
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertFalse(session.state.cards[body.object_id].tapped)
        self.assertIsNone(self.resolve(session));self.assertEqual((3,3),self.stats(session,body))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_live_attachment_scope_tracks_reattachment_phasing_reciprocity_and_giver_presence(self):
        from quorune.declaration_requirement_runtime import typed_attacker_requirements
        session=self.session(303621045);aura=self.add(session.engine,"Curse of Death's Hold")
        body=self.add(session.engine,'Grizzly Bears',seat='B',ref='scope-body-b');other=self.add(session.engine,'Grizzly Bears',seat='C',ref='scope-body-c')
        attach_to_player(session.state.cards,session.state.players,aura,'B',source_timestamp=session.engine._next_zone_timestamp())
        self.assertEqual(((1,1),(2,2)),(self.stats(session,body),self.stats(session,other)))
        attach_to_player(session.state.cards,session.state.players,aura,'C',source_timestamp=session.engine._next_zone_timestamp())
        self.assertEqual(((2,2),(1,1)),(self.stats(session,body),self.stats(session,other)))
        session.engine.change_control(aura.object_id,'D',reason='independent Aura controller scope fixture')
        self.assertEqual((1,1),self.stats(session,other))
        aura=session.state.cards[aura.object_id];aura.phased_out=True;self.assertEqual((2,2),self.stats(session,other));aura.phased_out=False
        session.state.players['C'].attachments.remove(aura.object_id)
        self.assertIsNone(current_attached_player_seat(aura,session.state.players));self.assertEqual((2,2),self.stats(session,other))
        session.state.players['C'].attachments.append(aura.object_id)
        session.state.players['C'].in_game=False;self.assertIsNone(current_attached_player_seat(aura,session.state.players));session.state.players['C'].in_game=True
        hunt=self.add(session.engine,'Curse of the Nightly Hunt');attach_to_player(session.state.cards,session.state.players,hunt,'C',source_timestamp=session.engine._next_zone_timestamp())
        def requirements(card):return typed_attacker_requirements(session.engine,card,active_seats=session.engine.active_seats,error_type=ValueError)
        self.assertTrue(requirements(other));self.assertFalse(requirements(body))
        reader=session.engine._effective_ability_fragments
        with patch.object(session.engine,'_effective_ability_fragments',side_effect=lambda card,**kwargs:() if card.object_id==hunt.object_id else reader(card,**kwargs)):
            self.assertFalse(requirements(other))
        hunt.phased_out=True;self.assertFalse(requirements(other));hunt.phased_out=False;self.assertTrue(requirements(other))

    def test_wrong_aura_controller_and_omitted_giver_requirement_mutants_are_killed(self):
        from quorune.declaration_requirement_runtime import typed_attacker_requirements
        session=self.session(303621050);body=self.add(session.engine,'Grizzly Bears',seat='B')
        aura=self.add(session.engine,'Overwhelming Splendor');attach_to_player(session.state.cards,session.state.players,aura,'B',source_timestamp=session.engine._next_zone_timestamp())
        def stats_witness():self.assertEqual((1,1),self.stats(session,body))
        stats_witness()
        with patch('quorune.card_programs.runtime.current_attached_player_seat',return_value='A'),self.assertRaises(AssertionError):stats_witness()
        hunt=self.add(session.engine,'Curse of the Nightly Hunt');attach_to_player(session.state.cards,session.state.players,hunt,'B',source_timestamp=session.engine._next_zone_timestamp())
        def rule_witness():self.assertTrue(typed_attacker_requirements(session.engine,body,active_seats=session.engine.active_seats,error_type=ValueError))
        rule_witness()
        with patch('quorune.rules.enchanted_player_requirements.enchanted_player_attacker_requirements',return_value=()),self.assertRaises(AssertionError):rule_witness()
        from quorune.semantic_runtime.activation_restrictions import current_activation_prohibitions
        session=self.session(303623051);body=self.add(session.engine,'Llanowar Elves',seat='B')
        arrest=self.add(session.engine,'Arrest');arrest.attached_to=body.object_id;body.attachments.append(arrest.object_id)
        ability=next(a for a in session.engine._activated_abilities(body) if a.mana_ability)
        def activation_witness():self.assertTrue(current_activation_prohibitions(session.engine,body,ability,actor='B'))
        activation_witness()
        with patch('quorune.semantic_runtime.activation_restrictions.activation_prohibition_specs',return_value=()),self.assertRaises(AssertionError):activation_witness()
