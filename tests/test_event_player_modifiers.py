"""CR611.2c/613.4c: versioned player selection delegates stat mutation and membership locking."""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch
from quorune.event_player_modifier_model import EventPlayerModifierSpec,EVENT_PLAYER_SET_VERSION_FIELD,DEFENDING_PLAYER_REFERENCE
from quorune.object_predicate import ObjectQuerySpec


class EventPlayerModifierModelTests(unittest.TestCase):
    def test_closed_instruction_roundtrip_keeps_both_signed_stat_dimensions_and_is_immutable(self):
        spec=EventPlayerModifierSpec(-1,-1);value=spec.to_effect()
        self.assertEqual(spec,EventPlayerModifierSpec.from_effect(value))
        self.assertEqual(DEFENDING_PLAYER_REFERENCE,value['predicate']['controller'])
        self.assertEqual((-1,-1),(value['power'],value['toughness']))
        with self.assertRaises(FrozenInstanceError):spec.power=1
        value['power']=4;self.assertEqual(-1,spec.power)
        self.assertEqual(EventPlayerModifierSpec(2,-3),EventPlayerModifierSpec.from_effect(EventPlayerModifierSpec(2,-3).to_effect()))

    def test_unknown_versions_fields_zero_effect_and_noninteger_amounts_fail_closed(self):
        value=EventPlayerModifierSpec(-1,-1).to_effect()
        for change in ({EVENT_PLAYER_SET_VERSION_FIELD:True},{EVENT_PLAYER_SET_VERSION_FIELD:2},
                {'power':True},{'power':'-1'},{'toughness':1.0},{'power':0,'toughness':0},
                {'unknown':1},{'keywords':['flying']},{'op':'life'}):
            with self.assertRaises((ValueError,TypeError)):EventPlayerModifierSpec.from_effect({**value,**change})
        for field in value:
            with self.assertRaises((ValueError,TypeError)):EventPlayerModifierSpec.from_effect({k:v for k,v in value.items() if k!=field})

    def test_resolved_controller_is_distinct_from_actor_and_all_other_query_dimensions_stay_closed(self):
        spec=EventPlayerModifierSpec(-1,-1);value=spec.to_effect()
        for controller in ('$controller','$target.0',None,'B'):
            changed={**value,'predicate':{**value['predicate'],'controller':controller}}
            with self.assertRaises(ValueError):EventPlayerModifierSpec.from_effect(changed)
        for change in ({'zones':['graveyard']},{'types_all':['artifact']},{'owner':'A'},
                {'excluded_controllers':['A']},{'token':True},{'exclude_ref':'source'},{'subtypes_any':['dragon']}):
            with self.assertRaises(ValueError):EventPlayerModifierSpec.from_effect({**value,'predicate':{**value['predicate'],**change}})
        bound={**value,'predicate':{**value['predicate'],'controller':'B'}}
        self.assertEqual(spec,EventPlayerModifierSpec.from_effect(bound,resolved=True))
        with self.assertRaises(ValueError):EventPlayerModifierSpec.from_effect(value,resolved=True)

    def test_binding_yields_existing_canonical_operation_without_reinterpreting_old_unversioned_effects(self):
        spec=EventPlayerModifierSpec(-1,-1);bound=spec.bound_effect('B')
        self.assertNotIn(EVENT_PLAYER_SET_VERSION_FIELD,bound)
        self.assertEqual('B',bound['predicate']['controller'])
        self.assertEqual(ObjectQuerySpec(zones=('battlefield',),controller='B',types_all=('creature',)),ObjectQuerySpec.from_dict(bound['predicate']))
        old={**spec.to_effect()};old.pop(EVENT_PLAYER_SET_VERSION_FIELD)
        with self.assertRaises(ValueError):EventPlayerModifierSpec.from_effect(old)
        from quorune.compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        self.assertFalse(fixed_resolution_characteristic_query_is_closed(ObjectQuerySpec.from_dict(spec.to_effect()['predicate']),target_schema=None))
        self.assertTrue(fixed_resolution_characteristic_query_is_closed(ObjectQuerySpec(zones=('battlefield',),controller='$controller',types_all=('creature',)),target_schema=None))




from types import SimpleNamespace as Row
from quorune.compiler.qualified_attack_subjects import qualified_attack_binding_spec
from quorune.compiler.event_player_modifiers import event_player_modifier_result
from quorune.rules.event_player_modifiers import event_player_modifier_program_dependencies,event_player_modifier_node_capabilities
from quorune.event_player_modifier_model import EVENT_PLAYER_SET_CAPABILITY,EVENT_PLAYER_SET_MECHANIC


class EventPlayerModifierCompilerTests(unittest.TestCase):
    TEXT='creatures defending player controls get -1/-1 until end of turn.'

    def binding(self):
        spec=qualified_attack_binding_spec('Whenever a Dragon you control attacks, '+self.TEXT)
        from quorune.compiler.trigger_event_vocabulary import FixedCounterTriggerEvent
        return Row(event=FixedCounterTriggerEvent(spec.event),event_condition=spec.condition,variant=spec.variant)

    def test_closed_qualified_defender_result_reuses_untargeted_existing_stat_set_owner(self):
        value=event_player_modifier_result(self.binding(),self.TEXT);self.assertIsNotNone(value)
        self.assertIsNone(value[0][2]);effect=value[0][1][0]
        self.assertEqual(EventPlayerModifierSpec(-1,-1),EventPlayerModifierSpec.from_effect(effect))
        required=event_player_modifier_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=value[0][3])
        self.assertIn(EVENT_PLAYER_SET_CAPABILITY,required)
        self.assertIn('continuous.resolution.fixed_characteristics_until_end_of_turn',required)

    def test_unsupported_quantities_keywords_targets_and_material_tails_stay_outside_this_schema(self):
        for text in (self.TEXT.replace('-1/-1','-X/-X'),self.TEXT+' Draw a card.',
                self.TEXT.replace('creatures','target creature'),self.TEXT.replace('get -1/-1','gain flying'),
                self.TEXT.replace('-1/-1','+0/+0'),self.TEXT.replace('until end of turn','until your next turn')):
            self.assertIsNone(event_player_modifier_result(self.binding(),text),text)

    def test_raw_frame_infers_recipient_and_result_owners_without_coverage_and_rejects_hidden_mixed_frames(self):
        effect=EventPlayerModifierSpec(-1,-1).to_effect();binding=self.binding()
        def frame(**changes):return Row(**{'event':'creature.attacks','event_condition':binding.event_condition,
            'active_zone':'battlefield','target_schema':None,'cost_schema':None,'effects':(effect,),
            'coverage':(),'capability_dependencies':(),**changes})
        required=event_player_modifier_program_dependencies(frame())
        self.assertTrue({EVENT_PLAYER_SET_CAPABILITY,'trigger.event.qualified_attack_singular',
            'trigger.event.normalized_public_action','trigger.placement.apnap'}<=set(required))
        for changes in ({'event':'attack.batch'},{'event_condition':None},{'active_zone':'graveyard'},
                {'target_schema':{'count':1}},{'cost_schema':{'mana':'{1}'}},
                {'effects':(effect,{'op':'draw','count':1,'player':'$controller'})},
                {'effects':({'op':'offer_optional_effect','effects':[effect]},)}):
            with self.assertRaises(ValueError):event_player_modifier_program_dependencies(frame(**changes))

    def test_capability_coverage_requires_its_shape_owner_and_does_not_trust_unknown_mechanics(self):
        from quorune.rules.capabilities import capability_covered_mechanics,capability_dependencies_for_node
        effect=EventPlayerModifierSpec(-1,-1).to_effect()
        mechanics=(EVENT_PLAYER_SET_MECHANIC,'cr-611-continuous-effects')
        self.assertIn(EVENT_PLAYER_SET_MECHANIC,capability_covered_mechanics((EVENT_PLAYER_SET_CAPABILITY,)))
        self.assertNotIn(EVENT_PLAYER_SET_MECHANIC,capability_covered_mechanics(('continuous.resolution.fixed_characteristics_until_end_of_turn',)))
        self.assertIn(EVENT_PLAYER_SET_CAPABILITY,capability_dependencies_for_node(effects=(effect,),target_schema=None,mechanic_ids=mechanics))
        bad={**effect,EVENT_PLAYER_SET_VERSION_FIELD:2}
        self.assertNotIn(EVENT_PLAYER_SET_CAPABILITY,capability_dependencies_for_node(effects=(bad,),target_schema=None,mechanic_ids=mechanics))

    def test_full_program_promotion_requires_the_proved_frame_and_every_runtime_owner(self):
        from quorune.rules.event_player_modifiers import is_closed_event_player_modifier_program
        b=self.binding();effect=EventPlayerModifierSpec(-1,-1).to_effect()
        values={'event':'creature.attacks','event_condition':b.event_condition,'active_zone':'battlefield',
            'cost_schema':None,'target_schema':None,'effects':(effect,),'coverage':(),'capability_dependencies':()}
        required=event_player_modifier_program_dependencies(Row(**values));values['capability_dependencies']=required
        self.assertTrue(is_closed_event_player_modifier_program(Row(**values)))
        for missing in required:
            self.assertFalse(is_closed_event_player_modifier_program(Row(**{**values,'capability_dependencies':tuple(c for c in required if c!=missing)})))
        self.assertFalse(is_closed_event_player_modifier_program(Row(**{**values,'event_condition':None})))

    def test_old_unversioned_result_is_not_claimed_by_new_owner_and_source_attack_requires_exact_scope(self):
        effect=EventPlayerModifierSpec(-1,-1).to_effect();old={k:v for k,v in effect.items() if k!=EVENT_PLAYER_SET_VERSION_FIELD}
        program=Row(event='resolve',effects=(old,));self.assertEqual((),event_player_modifier_program_dependencies(program))
        from quorune.rules.event_player_modifiers import event_player_modifier_scope_dependencies
        source={'field':'card','op':'eq','value':'$source.ref'}
        self.assertEqual(('trigger.event.normalized_self_attack',),event_player_modifier_scope_dependencies('creature.attacks',source))
        self.assertEqual((),event_player_modifier_scope_dependencies('creature.blocks',source))


class EventPlayerModifierRuntimeContractTests(unittest.TestCase):
    def effect(self,player='B'):
        effect=EventPlayerModifierSpec(-1,-1).to_effect();effect['predicate']['controller']=player
        return effect

    def test_runtime_projection_uses_bound_recipient_and_preserves_server_source_without_mutation(self):
        from quorune.rules.event_player_modifiers import prepare_event_player_modifier_runtime
        host=Row(state=Row(players=dict.fromkeys('ABCD')),active_seats=tuple('ABCD'))
        effect={**self.effect(),'_runtime_source':{'ref':'public-source','controller':'A'}}
        projected=prepare_event_player_modifier_runtime(host,effect)
        self.assertNotIn(EVENT_PLAYER_SET_VERSION_FIELD,projected);self.assertEqual('B',projected['predicate']['controller'])
        self.assertEqual(effect['_runtime_source'],projected['_runtime_source']);self.assertIn(EVENT_PLAYER_SET_VERSION_FIELD,effect)

    def test_known_absent_recipient_yields_empty_selection_and_unknown_or_forged_instruction_rejects(self):
        from quorune.rules.event_player_modifiers import prepare_event_player_modifier_runtime
        host=Row(state=Row(players=dict.fromkeys('ABCD')),active_seats=('A','C','D'))
        self.assertIsNone(prepare_event_player_modifier_runtime(host,self.effect()))
        for effect in (self.effect('Z'),{**self.effect(),EVENT_PLAYER_SET_VERSION_FIELD:True},
                {**self.effect(),EVENT_PLAYER_SET_VERSION_FIELD:2},{**self.effect(),'forged':True}):
            with self.assertRaises(ValueError):prepare_event_player_modifier_runtime(host,effect)

    def test_canonical_shape_owner_and_program_minimum_keep_new_instruction_scope_without_changing_old(self):
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities,fixed_resolution_characteristics_runtime_dependencies
        spec=EventPlayerModifierSpec(-1,-1);effect=spec.to_effect()
        required=fixed_resolution_characteristic_set_node_capabilities(effects=(effect,),target_schema=None,
            mechanic_ids=(EVENT_PLAYER_SET_MECHANIC,'cr-611-continuous-effects'))
        self.assertIn(EVENT_PLAYER_SET_CAPABILITY,required)
        b=qualified_attack_binding_spec('Whenever a Dragon you control attacks, creatures defending player controls get -1/-1 until end of turn.')
        program=Row(event='creature.attacks',event_condition=b.condition,active_zone='battlefield',cost_schema=None,target_schema=None,
            effects=(effect,),coverage=(),capability_dependencies=(),handlers=())
        self.assertIn(EVENT_PLAYER_SET_CAPABILITY,fixed_resolution_characteristics_runtime_dependencies(program))
        self.assertFalse(fixed_resolution_characteristic_set_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=('cr-611-continuous-effects',)))
        old=spec.bound_effect('B');old['predicate']['controller']='$controller'
        self.assertNotIn(EVENT_PLAYER_SET_CAPABILITY,fixed_resolution_characteristic_set_node_capabilities(effects=(old,),target_schema=None,mechanic_ids=('cr-611-continuous-effects',)))

    def test_installed_canonical_runtime_rejects_unknown_version_and_ignores_absent_recipient_before_selection(self):
        from quorune.effect_runtime.zones_and_attachments import _apply_modify_all_matching_permanents_until_end_of_turn
        from quorune.errors import GameRuleError
        host=Row(state=Row(players=dict.fromkeys('ABCD')),active_seats=('A','C','D'))
        self.assertEqual([],_apply_modify_all_matching_permanents_until_end_of_turn(host,self.effect(),actor='A',operation=self.effect()['op'],reason='absent recipient'))
        for change in ({EVENT_PLAYER_SET_VERSION_FIELD:2},{'forged':True}):
            with self.assertRaises(GameRuleError):_apply_modify_all_matching_permanents_until_end_of_turn(host,{**self.effect(),**change},actor='A',operation=self.effect()['op'],reason='invalid marker')

    def test_fresh_actor_projection_mutation_is_killed_after_positive_bound_defender_selection(self):
        from unittest.mock import patch
        from quorune.rules.event_player_modifiers import prepare_event_player_modifier_runtime
        host=Row(state=Row(players=dict.fromkeys('ABCD')),active_seats=tuple('ABCD'))
        self.assertEqual('B',prepare_event_player_modifier_runtime(host,self.effect())['predicate']['controller'])
        original=EventPlayerModifierSpec.bound_effect;observed=[]
        def actor(spec,player):observed.append(player);return original(spec,'A')
        with patch.object(EventPlayerModifierSpec,'bound_effect',actor):
            with self.assertRaises(AssertionError):self.assertEqual('B',prepare_event_player_modifier_runtime(host,self.effect())['predicate']['controller'])
        self.assertEqual(['B'],observed)

    def test_full_source_emits_versioned_result_but_missing_capability_authority_never_becomes_exact(self):
        from quorune.oracle_ir import compile_oracle_card
        from test_bound_effect_programs import record
        from dataclasses import replace
        text='Flying, hexproof\nWhenever a Dragon you control attacks, creatures defending player controls get -1/-1 until end of turn.'
        card=replace(record(text,'Creature'),keywords=('Flying','Hexproof'))
        ir=compile_oracle_card(card,capability_registry=None,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        nodes=[n for f in ir.faces for n in f.nodes if n.event=='creature.attacks']
        self.assertEqual(1,len(nodes));self.assertEqual(1,nodes[0].effects[0][EVENT_PLAYER_SET_VERSION_FIELD]);self.assertFalse(nodes[0].exact)




from pathlib import Path
import json,tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class EventPlayerModifierNativeTests(InheritedNativeFixture,unittest.TestCase):
    start=recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject=recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish=recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/event-player-modifier-cards.json'],folder/'event-player-modifier.sqlite3')
        cls.db=CardDatabase(folder/'event-player-modifier.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Event player fixed creature modifiers',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_complete_silumgar_normally_binds_keywords_and_independent_modifier_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from dataclasses import replace
        s,_=self.start(726000);source=self.add(s.engine,'Silumgar, the Drifting Death',zone='hand');self.assert_closed(s,source)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='creature.attacks')
        for cap in (EVENT_PLAYER_SET_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','trigger.event.qualified_attack_singular'):
            deps=tuple(d for d in program.capability_dependencies if d!=cap)
            raw=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(raw,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def attack_world(self,seed,*,extras=(),assignments=None,defer_attack=False):
        s,private=self.start(seed);source=self.add(s.engine,'Silumgar, the Drifting Death',zone='hand')
        haste=self.add(s.engine,'Fervor');own=self.add(s.engine,'Grizzly Bears',ref='caster-bear')
        bear=self.add(s.engine,'Grizzly Bears',seat='B',ref='defender-bear')
        memnite=self.add(s.engine,'Memnite',seat='B',ref='defender-zero-toughness')
        shroud=self.add(s.engine,'Deadly Insect',seat='B',ref='defender-shroud')
        foreign=self.add(s.engine,'Grizzly Bears',seat='D',ref='other-defender-bear')
        for card in (source,haste,own,bear,memnite,shroud,foreign):self.assert_closed(s,card)
        for name,seat,zone,ref in extras:self.assert_closed(s,self.add(s.engine,name,seat=seat,zone=zone,ref=ref))
        self.prepare(s,mana={'C':4,'U':1,'B':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertTrue({'flying','hexproof'}<=set(s.engine._combat_keywords(s.state.cards[source.object_id])))
        attacks=[(source,'B')] if assignments is None else [(source if ref=='source' else next(c for c in s.state.cards.values() if c.ref==ref),seat) for ref,seat in assignments]
        if not defer_attack:
            self.attack(s,attacks);self.assertEqual(len(attacks),self.trigger_count(s,source))
        return s,private,source,own,bear,memnite,shroud,foreign

    def test_actual_six_mana_original_attack_modifies_only_defender_creatures_without_targeting_and_replays_privately(self):
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726001)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),tuple(s.engine._numeric_stat(bear.object_id,f) for f in ('power','toughness')))
        for card in (memnite,shroud):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        for card in (own,foreign):self.assertEqual((2,2),tuple(s.engine._numeric_stat(card.object_id,f) for f in ('power','toughness')))
        self.assertEqual((3,7),tuple(s.engine._numeric_stat(source.object_id,f) for f in ('power','toughness')))
        self.finish(s,private)

    def test_actual_source_bounce_preserves_queued_defender_modifier_and_locked_ability_controller(self):
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726010,
            extras=(('Unsummon','A','hand','own-source-bounce'),('Island','A','battlefield','bounce-island')))
        response=next(c for c in s.state.cards.values() if c.ref=='own-source-bounce')
        self.cast(s,'A',response,[source.ref]);self.resolve_top(s);self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.engine._numeric_stat(bear.object_id,'toughness'))
        self.assertEqual(2,s.engine._numeric_stat(own.object_id,'toughness'));self.finish(s,private)

    def test_actual_countered_attack_trigger_keeps_paid_original_but_changes_no_creature_stats(self):
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726011,
            extras=(('Stifle','B','hand','counter-modifier-trigger'),('Island','B','battlefield','counter-island')))
        response=next(c for c in s.state.cards.values() if c.ref=='counter-modifier-trigger')
        self.cast(s,'B',response,[s.state.stack[-1].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.engine._numeric_stat(bear.object_id,'toughness'))
        self.assertEqual('battlefield',s.state.cards[memnite.object_id].zone);self.assertEqual('battlefield',s.state.cards[shroud.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_defender_concession_resolves_as_empty_set_without_touching_other_players_creatures(self):
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726012)
        self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(result.ok,result.summary);self.assertNotIn('B',s.engine.active_seats)
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack);self.assertFalse(s.state.game_over)
        for card in (own,foreign):self.assertEqual(2,s.engine._numeric_stat(card.object_id,'toughness'))
        self.finish(s,private)

    def test_actual_locked_membership_survives_control_change_but_not_blink_or_later_entry_and_expires(self):
        extras=[('Word of Seizing','A','hand','steal-selected-bear'),('Cloudshift','B','hand','blink-selected-bear'),
            ('Walking Corpse','B','hand','later-defender-creature'),('Vedalken Orrery','B','battlefield','defender-flash'),
            ('Grizzly Bears','B','battlefield','second-selected-bear'),('Mountain','A','battlefield','word-red-1'),
            ('Mountain','A','battlefield','word-red-2'),('Swamp','B','battlefield','later-black')]
        extras.extend(('Plains','A','battlefield','word-generic-'+str(i)) for i in range(3))
        extras.extend(('Plains','B','battlefield','defender-white-'+str(i)) for i in range(2))
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726020,extras=extras)
        self.assertIsNone(self.resolve(s));blinked=next(c for c in s.state.cards.values() if c.ref=='second-selected-bear')
        self.assertEqual(1,s.engine._numeric_stat(blinked.object_id,'toughness'))
        steal=next(c for c in s.state.cards.values() if c.ref=='steal-selected-bear');self.cast(s,'A',steal,[bear.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('A',s.state.cards[bear.object_id].controller);self.assertEqual(1,s.engine._numeric_stat(bear.object_id,'toughness'))
        old=s.state.cards[blinked.object_id].logical_object_id;blink=next(c for c in s.state.cards.values() if c.ref=='blink-selected-bear')
        self.cast(s,'B',blink,[blinked.ref]);self.assertIsNone(self.resolve(s));self.assertNotEqual(old,s.state.cards[blinked.object_id].logical_object_id)
        self.assertEqual(2,s.engine._numeric_stat(blinked.object_id,'toughness'))
        later=next(c for c in s.state.cards.values() if c.ref=='later-defender-creature');self.cast(s,'B',later);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.engine._numeric_stat(later.object_id,'toughness'))
        self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.assertEqual('B',s.state.cards[bear.object_id].controller)
        for card in (bear,blinked,later):self.assertEqual(2,s.engine._numeric_stat(card.object_id,'toughness'))
        self.finish(s,private)

    def test_actual_resolution_selects_before_entry_tokens_and_excludes_later_tokens(self):
        extras=[('Raise the Alarm','B','hand','before-modifier-tokens'),('Raise the Alarm','B','hand','after-modifier-tokens')]
        extras.extend(('Plains','B','battlefield','raise-payer-'+str(i)) for i in range(4))
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726021,extras=extras)
        before=next(c for c in s.state.cards.values() if c.ref=='before-modifier-tokens');self.cast(s,'B',before);self.resolve_top(s)
        created=[c.object_id for c in s.state.cards.values() if not c.is_card_object and c.controller=='B' and c.zone=='battlefield'];self.assertEqual(2,len(created))
        self.assertIsNone(self.resolve(s));self.assertTrue(all(oid not in s.state.cards or s.state.cards[oid].zone!='battlefield' for oid in created))
        after=next(c for c in s.state.cards.values() if c.ref=='after-modifier-tokens');self.cast(s,'B',after);self.assertIsNone(self.resolve(s))
        later=[c for c in s.state.cards.values() if not c.is_card_object and c.controller=='B' and c.zone=='battlefield'];self.assertEqual(2,len(later))
        for card in later:self.assertEqual(1,s.engine._numeric_stat(card.object_id,'toughness'))
        self.finish(s,private)

    def test_actual_three_dragons_keep_independent_defenders_and_stack_each_resolution(self):
        extras=(('Universal Automaton','A','battlefield','second-dragon'),('Universal Automaton','A','battlefield','third-dragon'),
            ('Walking Corpse','C','battlefield','twice-modified-defender'))
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726022,extras=extras,
            assignments=(('source','B'),('second-dragon','C'),('third-dragon','C')))
        twice=next(c for c in s.state.cards.values() if c.ref=='twice-modified-defender')
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.engine._numeric_stat(bear.object_id,'toughness'))
        self.assertEqual('graveyard',s.state.cards[twice.object_id].zone);self.assertEqual(2,s.engine._numeric_stat(foreign.object_id,'toughness'))
        self.finish(s,private)

    def test_actual_planeswalker_attack_uses_its_controller_as_defender_and_leaves_noncreature_unchanged(self):
        extras=[('Jace Beleren','B','hand','defending-planeswalker'),('Vedalken Orrery','B','battlefield','walker-flash'),
            ('Island','B','battlefield','walker-blue-1'),('Island','B','battlefield','walker-blue-2'),('Plains','B','battlefield','walker-generic')]
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726030,extras=extras,defer_attack=True)
        walker=next(c for c in s.state.cards.values() if c.ref=='defending-planeswalker');self.cast(s,'B',walker);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[walker.object_id].counters.get('loyalty',0))
        self.attack(s,[(source,walker.ref)]);self.assertEqual(1,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.engine._numeric_stat(bear.object_id,'toughness'));self.assertEqual(2,s.engine._numeric_stat(foreign.object_id,'toughness'))
        self.assertEqual(3,s.state.cards[walker.object_id].counters.get('loyalty',0));self.finish(s,private)

    def test_actual_post_journal_fault_restores_stats_membership_and_pending_trigger_then_retries_once(self):
        import quorune.effect_runtime.zones_and_attachments as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,private,source,own,bear,memnite,shroud,foreign=self.attack_world(726031)
        original=owner.create_resolution_continuous_effect;observed=[];failed=False
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append((s.engine._numeric_stat(bear.object_id,'toughness'),s.engine._numeric_stat(own.object_id,'toughness')))
            raise GameRuleError('Injected post-modifier journal failure')
        with patch('quorune.effect_runtime.zones_and_attachments.create_resolution_continuous_effect',side_effect=fault):
            for _ in range(8):
                before=authoritative_state_hash(s.state);result=s.act(s.pending_principals()[0],{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected post-modifier',result.summary);self.assertEqual(before,authoritative_state_hash(s.state));failed=True;break
        self.assertTrue(failed);self.assertEqual([(1,2)],observed)
        self.assertEqual(2,s.engine._numeric_stat(bear.object_id,'toughness'));self.assertEqual('battlefield',s.state.cards[memnite.object_id].zone)
        self.assertTrue(any(i.source_object_id==source.object_id for i in s.state.stack))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(1,s.engine._numeric_stat(bear.object_id,'toughness'))
        self.assertEqual('graveyard',s.state.cards[memnite.object_id].zone);self.finish(s,private)

    def test_fresh_native_actor_selection_mutation_is_killed_after_actual_positive_original_attack(self):
        self.test_actual_six_mana_original_attack_modifies_only_defender_creatures_without_targeting_and_replays_privately()
        original=EventPlayerModifierSpec.bound_effect;observed=[]
        def actor(spec,player):observed.append(player);return original(spec,'A')
        with patch.object(EventPlayerModifierSpec,'bound_effect',actor):
            with self.assertRaises(AssertionError):self.test_actual_six_mana_original_attack_modifies_only_defender_creatures_without_targeting_and_replays_privately()
        self.assertEqual(['B'],observed)

    def test_genuinely_verified_event_set_and_canonical_modifier_withdrawals_block_full_source(self):
        from copy import deepcopy
        from quorune.oracle_ir import compile_oracle_card,register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        base='continuous.resolution.fixed_characteristics_until_end_of_turn'
        for missing in (EVENT_PLAYER_SET_CAPABILITY,base):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Modifier owner withdrawn'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name,expected in (('Silumgar, the Drifting Death',False),('Infest',missing!=base),('Grizzly Bears',True)):
                card=self.db.lookup(name);semantics=SemanticRegistry()
                register_generated_programs(self.db,semantics,(card,),capability_registry=registry,capability_profile='commander_review',
                    promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                self.assertEqual(expected,bound['strict_capability_ready'],(missing,name,bound['blockers']))


if __name__=='__main__':unittest.main()
