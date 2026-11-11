"""Independent source/event predicates, prohibitions and event-time participation."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses

from quorune.ability_fragments import ability_fragment_to_dict,ability_fragment_from_dict
from quorune.compiler.trigger_participation_templates import static_trigger_multiplier_handler
from quorune.model import CardInstance
from quorune.trigger_participation import TriggerMultiplierSpec,TriggerMultiplierPredicate
from quorune.trigger_participation_query_model import trigger_participation_rule
from quorune.rules.trigger_participation_queries import participation_rule_matches
from quorune.rules.trigger_participation_runtime import typed_trigger_participations


def card(ref='SOURCE',controller='A',zone='battlefield'):
    return CardInstance(object_id='fixture:'+ref,ref=ref,oracle_id='fixture:participation',printed_name='Generic Source',
        owner='A',controller=controller,zone=zone)


def data(type_line='Creature — Wizard',**values):
    return {'type_line':type_line,'colors':[],'keywords':[],**values}


def spec(rule,subtype=None):
    return TriggerMultiplierSpec(TriggerMultiplierPredicate.TYPED_SOURCE_EVENT,schema_version=2,rule=rule,subtype=subtype)


class TypedTriggerParticipationOwnerTests(unittest.TestCase):
    def matches(self,rule,*,subtype=None,source_data=None,event='permanent.enter',context=None,source=None,modifier=None,modifier_data=None,active_zone=None):
        source=source or card();modifier=modifier or card('MODIFIER')
        return participation_rule_matches(spec(rule,subtype),modifier,modifier_data or data(),source,
            source_data or data(),event,context or {'types':['creature'],'controller':'A'},active_zone or source.zone)

    def test_old_schema_and_handler_bytes_are_unchanged_and_new_scope_is_closed(self):
        old=TriggerMultiplierSpec(TriggerMultiplierPredicate.ARTIFACT_OR_CREATURE_ENTERS)
        expected={'schema_version':1,'predicate':'artifact_or_creature_enters','additional_count':1,'exclude_self':False}
        self.assertEqual(expected,old.to_dict());self.assertEqual(expected,TriggerMultiplierSpec.from_dict(expected).to_dict())
        new=spec('source_subtype','wizard');wire=ability_fragment_to_dict(new)
        self.assertEqual(wire,ability_fragment_to_dict(ability_fragment_from_dict(wire)))
        for change in ({'schema_version':True},{'rule':'unrepresented'},{'subtype':'madeup'},
                {'additional_count':True},{'additional_count':2},{'exclude_self':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):TriggerMultiplierSpec.from_dict({**new.to_dict(),**change})
        with self.assertRaises(ValueError):TriggerMultiplierSpec.from_dict({**expected,'rule':None})
        with self.assertRaises(ValueError):TriggerMultiplierSpec.from_dict({**new.to_dict(),'extra':0})

    def test_source_predicates_do_not_match_causal_event_characteristics(self):
        self.assertTrue(self.matches('source_subtype',subtype='wizard',source_data=data(),context={'types':['land']}))
        self.assertFalse(self.matches('source_subtype',subtype='wizard',source_data=data('Creature — Human'),context={'types':['creature'],'subtypes':['wizard']}))
        same=card('MODIFIER')
        self.assertFalse(self.matches('another_source_subtype',subtype='elemental',source=same,source_data=data('Creature — Elemental')))
        self.assertTrue(self.matches('shaman_or_another_wizard',source=same,source_data=data('Creature — Shaman Wizard')))
        self.assertFalse(self.matches('shaman_or_another_wizard',source=same,source_data=data('Creature — Wizard')))
        self.assertTrue(self.matches('legendary_creature_source',source_data=data('Legendary Creature — Human')))
        self.assertFalse(self.matches('legendary_creature_source',source_data=data('Legendary Artifact')))

    def test_event_predicates_keep_controller_type_and_timing_distinctions(self):
        self.assertTrue(self.matches('land_enters',context={'types':['land'],'controller':'B'}))
        self.assertFalse(self.matches('land_enters',context={'types':['creature'],'controller':'A'}))
        self.assertTrue(self.matches('entering_subtype',subtype='wizard',context={'types':['creature'],'subtypes':['wizard'],'controller':'A'}))
        self.assertFalse(self.matches('entering_subtype',subtype='wizard',context={'types':['creature'],'subtypes':['wizard'],'controller':'B'}))
        self.assertFalse(self.matches('permanent_enters',source=card(zone='graveyard')))
        self.assertTrue(self.matches('creature_dies',event='creature.dies',context={'types':['creature']}))
        self.assertFalse(self.matches('creature_dies',event='permanent.leave',context={'types':['creature']}))
        self.assertTrue(self.matches('creature_dies',event='permanent.leave',context={'types':['artifact','creature'],'to':'graveyard'}))
        self.assertFalse(self.matches('creature_dies',event='permanent.leave',context={'types':['creature'],'to':'exile'}))
        self.assertTrue(self.matches('legendary_or_artifact_enters_leaves',event='permanent.leave',context={'types':['enchantment'],'supertypes':['legendary']}))
        self.assertFalse(self.matches('legendary_or_artifact_enters_leaves',event='permanent.leave',context={'types':['enchantment']}))

    def test_attack_damage_cast_and_copy_are_distinct_causal_events(self):
        self.assertTrue(self.matches('controlled_creature_attacks',event='creature.attacks',context={'types':['creature'],'controller':'A'}))
        self.assertFalse(self.matches('controlled_creature_attacks',event='creature.attacks',context={'types':['creature'],'controller':'B'}))
        damage={'combat':True,'amount':2,'target_kind':'player','source_types':['creature'],'source_controller':'A'}
        self.assertTrue(self.matches('controlled_combat_damage_player',event='damage.dealt',context=damage))
        for change in ({'combat':False},{'amount':0},{'target_kind':'permanent'},{'source_controller':'B'}):
            with self.subTest(change=change):self.assertFalse(self.matches('controlled_combat_damage_player',event='damage.dealt',context={**damage,**change}))
        for event in ('spell.cast','spell.cast_or_copy'):
            self.assertTrue(self.matches('cast_or_copy_instant_sorcery',event=event,context={'types':['instant'],'controller':'A'}))
            self.assertFalse(self.matches('cast_or_copy_instant_sorcery',event=event,context={'types':['creature'],'controller':'A'}))

    def test_compiler_uses_generic_subtype_and_source_references_and_rejects_siblings(self):
        for text,rule,subtype in (
            ('If a triggered ability of an Ally you control triggers, that ability triggers an additional time.','source_subtype','ally'),
            ('If a Wizard you control entering causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time.','entering_subtype','wizard'),
            ("Creatures entering or dying don't cause abilities to trigger.",'prevent_creature_enters_dies',None),
            ('As long as Generic Source is equipped, if a triggered ability of Generic Source or an Equipment attached to it triggers, that ability triggers an additional time.','equipped_source_or_attached_equipment',None),
            ('Room abilities of dungeons you own trigger an additional time.','owned_dungeon_room',None),
        ):
            value=static_trigger_multiplier_handler(text,source_name='Generic Source')
            self.assertIsNotNone(value)
            fragment=ability_fragment_from_dict(value[1]['fragment']);self.assertEqual((rule,subtype),(fragment.rule,fragment.subtype))
        base='If a triggered ability of an Ally you control triggers, that ability triggers an additional time.'
        for text in (base+' Draw a card.',base.replace('an Ally','a Madeup'),base.replace('an additional time','two additional times'),
                'Room abilities of dungeons your opponents own trigger an additional time.'):
            self.assertIsNone(static_trigger_multiplier_handler(text,source_name='Generic Source'))

    def test_event_time_sources_and_ability_removal_drive_participation_without_mutating_lki(self):
        modifier=card('OLD_MODIFIER');observer=card('OBSERVER')
        fragment=ability_fragment_to_dict(spec('creature_dies'))
        captured={modifier.object_id:data('Creature',ability_fragments=[fragment]),observer.object_id:data()}
        host=SimpleNamespace(_semantic_event_sources=lambda **kwargs:[],_effective_card_data=lambda source:data(),
            _type_parts=lambda text:(set(),set(),set()))
        before=deepcopy(captured)
        extra,prevented=typed_trigger_participations(host,source=observer,controller='A',event='creature.dies',
            context={'types':['creature'],'controller':'A'},sources=[modifier,observer],source_characteristics=captured,
            ability_characteristics=captured[observer.object_id])
        self.assertEqual(1,len(extra));self.assertFalse(prevented);self.assertEqual(before,captured)
        current,_=typed_trigger_participations(host,source=observer,controller='A',event='creature.dies',
            context={'types':['creature']},sources=[modifier],ability_characteristics=data())
        self.assertEqual((),current)

    def test_current_participation_read_scope_reuses_values_and_refreshes_after_ability_removal(self):
        from quorune.characteristic_read_frames import memoized_characteristic_read
        modifier=card('READ_SCOPE_MODIFIER')
        fragment=ability_fragment_to_dict(spec('creature_dies'))
        enabled=[True];reads=[]
        host=SimpleNamespace(_semantic_event_sources=lambda **kwargs:[modifier],
            _type_parts=lambda text:(set(),set(),set()))
        def effective(source):
            def read():
                reads.append(source.object_id)
                return data(ability_fragments=[fragment] if enabled[0] else [])
            return memoized_characteristic_read(host,('participation-input',source.object_id),read,identity=source)
        host._effective_card_data=effective
        def collect(**kwargs):
            return typed_trigger_participations(host,source=modifier,controller='A',event='creature.dies',
                context={'types':['creature'],'controller':'A'},**kwargs)
        extra,prevented=collect()
        self.assertEqual((1,False),(len(extra),prevented))
        self.assertEqual([modifier.object_id],reads)
        enabled[0]=False
        self.assertEqual(((),False),collect())
        self.assertEqual([modifier.object_id]*2,reads)
        captured=data(ability_fragments=[fragment]);before=deepcopy(captured)
        extra,prevented=collect(source_characteristics={modifier.object_id:captured},ability_characteristics=captured)
        self.assertEqual((1,False),(len(extra),prevented))
        self.assertEqual(before,captured)
        self.assertEqual([modifier.object_id]*2,reads)

    def test_prohibition_scope_beats_additions_and_preserves_opponent_relations(self):
        observer=card('OBSERVER');modifier=card('MODIFIER');orb=card('ORB','B')
        rows={modifier.object_id:data(ability_fragments=[ability_fragment_to_dict(spec('permanent_enters'))]),
            orb.object_id:data('Artifact',ability_fragments=[ability_fragment_to_dict(spec('prevent_creature_enters'))])}
        host=SimpleNamespace(_semantic_event_sources=lambda **kwargs:[modifier,orb],_effective_card_data=lambda c:rows[c.object_id],
            _type_parts=lambda text:(set(),set(),set()))
        extra,prevented=typed_trigger_participations(host,source=observer,controller='A',event='permanent.enter',
            context={'types':['creature']},ability_characteristics=data())
        self.assertTrue(prevented);self.assertEqual(1,len(extra))
        self.assertFalse(self.matches('prevent_opponent_permanent_enters',source=card(controller='A'),modifier=modifier))
        self.assertTrue(self.matches('prevent_opponent_permanent_enters',source=card(controller='B'),modifier=modifier))

    def test_equipped_condition_reads_event_time_equipment_and_excludes_phased_attachments(self):
        modifier=card('EQUIPPED_MODIFIER');equipment=card('GEAR');equipment.attached_to=modifier.ref
        captured={modifier.object_id:data(ability_fragments=[ability_fragment_to_dict(spec('equipped_source_or_attached_equipment'))]),
            equipment.object_id:data('Artifact — Equipment')}
        host=SimpleNamespace(_semantic_event_sources=lambda **kwargs:[modifier,equipment],
            _effective_card_data=lambda c:data('Artifact'),
            _type_parts=lambda text:({'artifact'},{'equipment'} if 'Equipment' in text else set(),set()))
        def count():
            extra,_=typed_trigger_participations(host,source=modifier,controller='A',event='permanent.enter',
                context={'types':['creature']},sources=[modifier,equipment],source_characteristics=captured,
                ability_characteristics=data())
            return len(extra)
        self.assertEqual(1,count())
        equipment.phased_out=True
        self.assertEqual(0,count())

    def test_canonical_authority_withdrawal_and_descriptor_inference_preserve_unrelated_draw(self):
        import json
        from dataclasses import replace,fields
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticProgram
        registry=load_default_capability_registry();raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');discovered=set(discover_tests(ROOT).values())
        control=replace(query_record('Draw a card.'),type_line='Sorcery',power=None,toughness=None)
        for text,capability in (
            ('If a permanent entering causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time.','trigger.participation.typed_source_event'),
            ("Creatures entering don't cause abilities to trigger.",'trigger.participation.prohibition')):
            source=query_record(text)
            self.assertEqual('exact',compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review').status)
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==capability).update(status='blocked',blockers=['Participation authority withdrawn'])
            blocked=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=discovered)
            blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=blocked,capability_profile='commander_review').status)
            self.assertEqual('exact',compile_oracle_card(control,capability_registry=blocked,capability_profile='commander_review').status)
            descriptor=static_trigger_multiplier_handler(text)[1]
            base=SemanticProgram('fixture:inference','Inference')
            forged=SimpleNamespace(**{f.name:getattr(base,f.name) for f in fields(base)})
            forged.handlers=[descriptor];forged.event='continuous';forged.active_zone='battlefield'
            binding=bind_semantic_program_runtime(forged,capability_registry=registry,profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertTrue(any(capability in blocker for blocker in binding['blockers']))


class TypedTriggerParticipationNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'participation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-state-trigger-cards.json',ROOT/'tests/fixtures/typed-trigger-participation-cards.json',
            ROOT/'tests/fixtures/typed-trigger-participation-controls.json',ROOT/'tests/fixtures/conditional-copy-value-cards.json',
            ROOT/'tests/fixtures/typed-trigger-participation-entry-controls.json',ROOT/'tests/fixtures/public-negative-keyword-cards.json',
            ROOT/'tests/fixtures/kicked-damage-replacement-cards.json',ROOT/'tests/fixtures/face-up-effect-cards.json',
            ROOT/'tests/fixtures/departure-trigger-limit-cards.json',ROOT/'tests/fixtures/source-self-optional-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Typed participation',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def closed(self,s,c):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        p=compile_best_available_card_program(self.db,self.db.by_oracle_id(c.oracle_id),semantic_registry=s.engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        result=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(result['strict_capability_ready'],result['blockers']);self.assertEqual('capability_closed',result['trust_basis'])

    def start(self,s):
        from quorune.turn_step_owner import TURN_STEPS
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None
        e.state.active_player='A';e.state.started=True;e.state.phase='precombat_main';e.state.step='main'
        e.state.phase_index=TURN_STEPS.index(('precombat_main','main'))
        for seat in 'ABCD':e.state.players[seat].mana_pool.update(W=10,U=10,B=10,R=10,G=10,C=20)
        e._grant_priority('A');e.pump();self.checkpoint(s)

    def cast(self,s,c,*,seat='A',targets=()):
        from quorune.record import authoritative_state_hash
        self.closed(s,c)
        for _ in range(12):
            if s.state.pending_decision.kind=='trigger.order':self.order_triggers(s);continue
            self.assertEqual('priority',s.state.pending_decision.kind)
            if s.pending_principals()[0]=='pilot:'+seat:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        actions=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in actions if row.get('card')==c.ref)
        before=authoritative_state_hash(s.state);payload={'action_id':offer['id'],'pay':'auto','targets':list(targets)}
        self.assertFalse(s.act('pilot:D',payload).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:'+seat,payload);self.assertTrue(result.ok,result.summary)

    def resolve(self,s):
        for _ in range(128):
            decision=s.state.pending_decision
            if decision.kind=='trigger.order':self.order_triggers(s);continue
            if decision.kind!='priority':return decision
            if not s.state.stack:return None
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Participation stack did not finish')

    def test_original_entry_modifiers_add_independently_and_orb_prevents_trigger_and_quota(self):
        s=self.session(650001);e=s.engine
        for name,ref in [('Yarok, the Desecrated','ENTRY_YAROK'),('Naban, Dean of Iteration','ENTRY_NABAN')]:
            c=self.add(e,name,ref=ref);self.closed(s,c)
        second_yarok=self.add(e,'Yarok, the Desecrated',seat='B',ref='OPPONENT_YAROK');self.closed(s,second_yarok)
        # An opponent's modifier is a real negative controller scope.
        visionary=self.add(e,'Cloudkin Seer',zone='hand',ref='ENTRY_WIZARD')
        vampire=self.add(e,'Welcoming Vampire',ref='CAPPED_ENTRY_SOURCE');self.closed(s,vampire)
        orb=self.add(e,'Torpor Orb',zone='hand',ref='ENTRY_ORB')
        second=self.add(e,'Elvish Visionary',zone='hand',ref='SUPPRESSED_VISIONARY')
        remove=self.add(e,'Naturalize',zone='hand',ref='REMOVE_TRIGGER_PROHIBITION')
        self.start(s)
        self.cast(s,orb);self.assertIsNone(self.resolve(s))
        before=len(s.state.players['A'].zones['hand'])
        self.cast(s,second);self.assertIsNone(self.resolve(s))
        self.assertEqual(before-1,len(s.state.players['A'].zones['hand']))
        self.assertNotIn('once_per_turn_triggers',s.state.cards[vampire.object_id].annotations)
        self.cast(s,remove,targets=[orb.ref]);self.assertIsNone(self.resolve(s))
        hand=len(s.state.players['A'].zones['hand'])
        self.cast(s,visionary);self.assertIsNone(self.resolve(s))
        # Three independent Wizard ETB draws plus the capped Vampire's one draw.
        self.assertEqual(hand+3,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)

    def test_original_death_modifiers_and_hushbringer_use_simultaneous_lookback_sources(self):
        for prohibited in (False,True):
            with self.subTest(prohibited=prohibited):
                s=self.session(650002+int(prohibited));e=s.engine
                teysa=self.add(e,'Teysa Karlov',ref='DEPARTING_TEYSA');self.closed(s,teysa)
                arsonist=self.add(e,'Goblin Arsonist',ref='DEPARTING_ARSONIST')
                if prohibited:
                    hush=self.add(e,'Hushbringer',ref='DEPARTING_HUSH');self.closed(s,hush)
                wipe=self.add(e,'Day of Judgment',zone='hand',ref='PARTICIPATION_WIPE')
                self.start(s);self.cast(s,wipe)
                for _ in range(24):
                    if s.state.pending_decision.kind!='priority' or not any(i.kind=='spell' for i in s.state.stack):break
                    result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard',s.state.cards[teysa.object_id].zone)
                triggers=list(s.state.pending_trigger_batches)
                count=sum(len(batch.items) for batch in triggers)+sum(i.kind=='triggered_ability' for i in s.state.stack)
                self.assertEqual(0 if prohibited else 2,count)
                if not prohibited:
                    # Each independently triggered Arsonist ability chooses a target.
                    chosen=[]
                    for _ in range(40):
                        d=s.state.pending_decision
                        if d.kind=='trigger.order':self.order_triggers(s);continue
                        if d.kind=='semantic.target':
                            result=s.act(s.pending_principals()[0],{'action_id':'choose','targets':['B' if not chosen else 'C']})
                            self.assertTrue(result.ok,result.summary);chosen.append(True);continue
                        if d.kind=='semantic.choice':
                            projected=s.packet(s.pending_principals()[0],full=True)['decision']['ctx']
                            self.assertEqual('offer_optional_effect',projected['operation'])
                            result=s.act(s.pending_principals()[0],{'action_id':'choose','choice':'apply'})
                            self.assertTrue(result.ok,result.summary);continue
                        if d.kind=='priority' and not s.state.stack:break
                        result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
                    self.assertEqual((39,39),(s.state.players['B'].life,s.state.players['C'].life))
                self.replay(s,load=True)

    def test_original_harmonic_and_veyran_match_ability_source_and_cast_copy_event_separately(self):
        s=self.session(650004);e=s.engine
        harmonic=self.add(e,'Harmonic Prodigy',ref='PRODIGY_SOURCE');self.closed(s,harmonic)
        veyran=self.add(e,'Veyran, Voice of Duality',ref='MAGECRAFT_SOURCE');self.closed(s,veyran)
        spell=self.add(e,'Divination',zone='hand',ref='CAST_CAUSAL_SORCERY')
        copy=self.add(e,'Reverberate',zone='hand',ref='COPY_CAUSAL_SORCERY')
        self.start(s);self.cast(s,spell)
        self.cast(s,copy,targets=[next(i.ref for i in s.state.stack if i.card_object_id==spell.object_id)])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(5,int(s.engine._effective_card_data(harmonic).get('power',1)))
        self.assertEqual(11,int(s.engine._effective_card_data(veyran).get('power',2)))
        self.replay(s,load=True)

    def test_original_isshin_doubles_actual_attack_trigger_with_principal_rollback_and_replay(self):
        from quorune.model import CombatState
        from quorune.record import authoritative_state_hash
        s=self.session(650005);e=s.engine
        isshin=self.add(e,'Isshin, Two Heavens as One',ref='ATTACK_ISSHIN');self.closed(s,isshin)
        observer=self.add(e,'Generic Participation Attack Observer',ref='ATTACK_OBSERVER');self.closed(s,observer)
        observer.acquired_control_turn_count=-1
        self.start(s);e.permissions.invalidate_current();e.state.pending_decision=None
        e.state.phase='combat';e.state.step='declare_attackers';e.state.phase_index=5;e.state.combat=CombatState()
        e._issue_attackers();self.checkpoint(s);hand=len(s.state.players['A'].zones['hand'])
        payload={'action_id':'attack','attackers':{observer.ref:'B'}};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C',payload).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(s.act('pilot:A',payload).ok)
        self.assertIsNone(self.resolve(s));self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)
