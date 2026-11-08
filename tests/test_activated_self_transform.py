from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.transform_capability_shapes import source_transform_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class TransformTimestampVersionTests(unittest.TestCase):
    def test_scripted_pilot_uses_the_advertised_timestamp_permutation(self):
        from quorune.cli import _scripted_choice
        decision={'kind':'permanent.timestamp_order','ctx':{'cards':[{'id':'SECOND'},{'id':'FIRST'}]},
            'legal_actions':[{'id':'order','action':'order'}]}
        result=_scripted_choice({},decision,None)
        self.assertEqual('order',result['action_id']);self.assertEqual(['SECOND','FIRST'],result['cards'])

    def ordering_frame(self):
        from quorune.model import CardInstance
        from quorune.transform_order_model import TransformTimestampContinuation,TransformTimestampRequest
        from quorune.replacement.immutable import FrozenMap
        cards=tuple(CardInstance(object_id=ref,ref=ref,oracle_id='ordering-fixture',printed_name=ref,
            owner=actor,controller=actor,zone='battlefield') for actor,ref in
            (('C','C1'),('C','C2'),('A','A1'),('A','A2')))
        request=TransformTimestampRequest(tuple(FrozenMap(card.to_dict()) for card in cards),'day/night fixture',True,FrozenMap({}))
        return TransformTimestampContinuation(request,FrozenMap({'active_player':'B','turn_sequence':2,
            'phase_index':0,'phase':'beginning','step':'untap'}),'untap',None,())

    def test_ordering_frame_has_closed_codec_and_complete_controller_permutations(self):
        from quorune.transform_order_model import TransformTimestampModelError
        from quorune.transform_order_model import TransformTimestampContinuation
        frame=self.ordering_frame()
        self.assertEqual(frame,TransformTimestampContinuation.from_dict(frame.to_dict()))
        self.assertEqual(('C',('C1','C2')),frame.current_choice)
        next_frame=frame.with_order(['C2','C1'])
        self.assertEqual(('A',('A1','A2')),next_frame.current_choice)
        complete=next_frame.with_order(['A2','A1'])
        self.assertEqual(('C2','C1','A2','A1'),complete.ordered_ids)
        for values in (['C1'],['C1','C1'],['A1','C2'],[True,'C2']):
            with self.assertRaises(TransformTimestampModelError):frame.with_order(values)
        wire=frame.to_dict()
        for bad in ({**wire,'extra':1},{**wire,'schema_version':True},{**wire,'orders':['C2C1']},
                {**wire,'held_triggers':[{}]},
                {**wire,'context':{**wire['context'],'turn_sequence':True}},
                {**wire,'request':{**wire['request'],'subjects':'not-an-array'}}):
            with self.assertRaises(TransformTimestampModelError):TransformTimestampContinuation.from_dict(bad)

    def test_ordering_frame_rejects_stale_subjects_and_forged_apnap(self):
        from quorune.transform_order_model import TransformTimestampModelError
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.replacement.immutable import thaw_value
        frame=self.ordering_frame()
        cards={raw['object_id']:CardInstance.from_dict(thaw_value(raw)) for raw in frame.request.subjects}
        host=SimpleNamespace(state=SimpleNamespace(cards=cards),apnap_order=lambda:['B','C','D','A'])
        frame.request.validate_current(host)
        cards['C1'].transform_count=1
        with self.assertRaises(TransformTimestampModelError):frame.request.validate_current(host)
        cards['C1'].transform_count=0;host.apnap_order=lambda:['A','B','C','D']
        with self.assertRaises(TransformTimestampModelError):frame.request.validate_current(host)

    def test_simultaneous_timestamp_plan_uses_live_apnap_and_preserves_controller_input_order(self):
        from types import SimpleNamespace
        from quorune.transform_timestamp_model import ordered_transform_cards
        first=SimpleNamespace(controller='A',object_id='first');second=SimpleNamespace(controller='A',object_id='second');other=SimpleNamespace(controller='C',object_id='other')
        self.assertEqual((other,first,second),ordered_transform_cards((first,other,second),('B','C','D','A')))
        with self.assertRaises(ValueError):ordered_transform_cards((first,),('B','C','D'))
        with self.assertRaises(ValueError):ordered_transform_cards((first,),('A','A'))

    def test_shared_timestamp_clock_advances_only_for_current_successful_transforms(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.permanent_transform import transform_permanent
        from quorune.characteristic_evaluation import type_parts
        state=self.state();state.timestamp_sequence=9
        card=CardInstance(object_id='timestamp-source',ref='TIME_SOURCE',oracle_id='timestamp-source-oracle',
            printed_name='Clock fixture',owner='A',controller='A',zone='battlefield',zone_timestamp=4,active_face='front')
        record=SimpleNamespace(oracle_id=card.oracle_id,layout='transform',faces=({'name':'front','type_line':'Creature'},
            {'name':'back','type_line':'Creature'}))
        host=SimpleNamespace(state=state,card_record=lambda _card:record,_type_parts=type_parts,
            _effective_card_data=lambda _card:{'type_line':'Creature','ability_fragments':[]},
            semantic_program_is_current_trusted=lambda _program:False,
            semantics=SimpleNamespace(programs_for_oracle=lambda *_args,**_kwargs:()))
        identity=card.logical_object_id
        self.assertIsNotNone(transform_permanent(host,card,expected_transform_count=0,day_night_instruction=False))
        self.assertEqual((10,10,'back',1,identity),
            (state.timestamp_sequence,card.zone_timestamp,card.active_face,card.transform_count,card.logical_object_id))
        self.assertIsNone(transform_permanent(host,card,expected_transform_count=0,day_night_instruction=False))
        card.face_down=True
        self.assertIsNone(transform_permanent(host,card,expected_transform_count=1,day_night_instruction=False))
        self.assertEqual(10,state.timestamp_sequence)
        card.face_down=False;state.transform_timestamp_version=None
        self.assertIsNotNone(transform_permanent(host,card,expected_transform_count=1,day_night_instruction=False))
        self.assertEqual((10,10,'front'),(state.timestamp_sequence,card.zone_timestamp,card.active_face))

    def state(self):
        from quorune.model import GameConfig,GameState
        return GameState(game_id='transform-version-fixture',config=GameConfig(),players={},cards={},
            deck_names={},commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)

    def test_current_and_legacy_state_codecs_preserve_explicit_provenance(self):
        from quorune.model import GameState
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        state=self.state();wire=state.to_dict()
        self.assertEqual(1,wire['transform_timestamp_version'])
        self.assertEqual(wire,GameState.from_dict(wire).to_dict())
        modern={'format':format_state_versions(state)}
        self.assertEqual(1,modern['format']['transform_timestamp_version'])
        validate_state_versions(modern,state)
        old={key:value for key,value in wire.items() if key!='transform_timestamp_version'}
        legacy=GameState.from_dict(old)
        self.assertIsNone(legacy.transform_timestamp_version)
        self.assertEqual(old,legacy.to_dict())
        historical={'format':format_state_versions(legacy)}
        self.assertNotIn('transform_timestamp_version',historical['format'])
        validate_state_versions(historical,legacy)
        for manifest,value in ((historical,state),(modern,legacy)):
            with self.assertRaises(ValueError):validate_state_versions(manifest,value)
        for bad in (True,False,0,2,'1',{}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):GameState.from_dict({**wire,'transform_timestamp_version':bad})
                with self.assertRaises(ValueError):validate_state_versions(
                    {'format':{**modern['format'],'transform_timestamp_version':bad}},state)


    def test_modal_transform_eligibility_has_independent_replay_provenance(self):
        from quorune.model import GameState
        from quorune.record_state_provenance import format_state_versions, validate_state_versions

        key = "modal_dfc_transform_version"
        state = self.state()
        wire = state.to_dict()
        self.assertEqual(1, wire[key])
        self.assertEqual(wire, GameState.from_dict(wire).to_dict())
        modern = {"format": format_state_versions(state)}
        self.assertEqual(1, modern["format"][key])
        validate_state_versions(modern, state)

        old = {name: value for name, value in wire.items() if name != key}
        legacy = GameState.from_dict(old)
        self.assertIsNone(legacy.modal_dfc_transform_version)
        self.assertEqual(1, legacy.transform_timestamp_version)
        self.assertEqual(old, legacy.to_dict())
        historical = {"format": format_state_versions(legacy)}
        self.assertNotIn(key, historical["format"])
        validate_state_versions(historical, legacy)

        for manifest, value in ((historical, state), (modern, legacy)):
            with self.assertRaises(ValueError):
                validate_state_versions(manifest, value)
        for bad in (True, False, 0, 2, "1", {}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    GameState.from_dict({**wire, key: bad})
                with self.assertRaises(ValueError):
                    validate_state_versions({"format": {**modern["format"], key: bad}}, state)


class ActivatedTransformCompilerTests(unittest.TestCase):
    def test_fixed_self_transform_has_same_generic_owner_and_count_snapshot(self):
        registry=load_default_capability_registry();record=query_record('{6}: Transform this creature.')
        ir=compile_oracle_card(record,capability_registry=registry)
        self.assertEqual('exact',ir.status);node=ir.faces[0].nodes[0]
        self.assertEqual(('permanent.transform.face_change',),source_transform_node_capabilities(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics))
        self.assertEqual('$source.zone_object',node.effects[0]['card'])
        self.assertEqual('$source.transform_count',node.effects[0]['expected_transform_count'])

    def test_targeted_and_unrepresented_transform_variants_stay_residual(self):
        registry=load_default_capability_registry()
        for text in ('{6}: Transform target creature.','{6}: Transform this creature, then take an extra turn.','{6}: Convert this creature.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        effect={'op':'transform','card':'$source.zone_object','expected_transform_count':'$source.transform_count'}
        for change in ({'card':'$target.0'},{'expected_transform_count':True},{'extra':1}):
            self.assertFalse(source_transform_node_capabilities(effects=({**effect,**change},),target_schema=None,mechanic_ids=('fixed-activated-source-transform',)))
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in data['capabilities'] if c['id']=='permanent.transform.face_change').update(status='blocked',blockers=['missing transform owner'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(query_record('{6}: Transform this creature.'),capability_registry=registry).status)

    def test_copied_keyword_snapshot_is_not_reinterpreted_as_original_selected_face(self):
        from types import SimpleNamespace
        from quorune.rules.current_face_keywords import current_printed_face_keywords
        base={'keywords':['Flying','Defender']}
        record=SimpleNamespace(faces=({'name':'front'},{'name':'back'}))
        for values in ({'annotations':{'copy_overrides':{}},'is_token':False,'object_kind':'card'},
                       {'annotations':{},'is_token':True,'object_kind':'token'},
                       {'annotations':{},'is_token':False,'object_kind':'spell_copy'}):
            card=SimpleNamespace(zone='battlefield',**values)
            self.assertEqual(base['keywords'],current_printed_face_keywords(None,card,record,base))


class ActivatedTransformRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'activated-transform.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/activated-self-transform-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Activated transform',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    def add(self,engine,name,**kwargs):
        card=witnesses.BoundEffectProgramRuntimeTests.add(self,engine,name,**kwargs)
        record=self.db.by_oracle_id(card.oracle_id)
        if record.faces and card.zone=='battlefield':card.active_face=str(record.faces[0]['name'])
        return card
    def ready(self,session,source,mana):
        record=self.db.by_oracle_id(source.oracle_id)
        program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        for ability in program.abilities:session.engine.semantics.put(ability)
        return witnesses.BoundEffectProgramRuntimeTests.ready(self,session,source,mana)
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def activation(self,s,card):
        return next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+card.ref+':') and 'Transform' in a['label'])


    def test_actual_modal_transform_reaches_permanent_face_in_place(self):
        session = self.session(326007)
        engine = session.engine
        source = self.add(engine, "Monica Rambeau // Photon, Living Light", zone="hand")
        offer = self.ready(session, source, {"C": 4, "W": 3, "R": 1})
        self.assertEqual(f"cast:{source.ref}", offer["id"])
        self.checkpoint(session)
        cast = session.act("pilot:A", {
            "action_id": offer["id"], "pay": "auto", "reserve": {"C": 2, "W": 2, "R": 1},
        })
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(session))

        current = engine.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual("Monica Rambeau", current.active_face)
        identity, timestamp = current.logical_object_id, current.zone_timestamp
        command = {"action_id": self.activation(session, current)["id"], "pay": "auto"}
        before = authoritative_state_hash(engine.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        activated = session.act("pilot:A", command)
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(0, engine.state.stack[-1].context.get("source_transform_count"))
        self.assertFalse(any(engine.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(session))

        current = engine.state.cards[source.object_id]
        self.assertEqual("Photon, Living Light", current.active_face)
        self.assertEqual(identity, current.logical_object_id)
        self.assertEqual(1, current.transform_count)
        self.assertGreater(current.zone_timestamp, timestamp)
        events = [event for event in engine.state.events
                  if event.code == "permanent.transform" and event.details["object"] == source.ref]
        self.assertEqual(1, len(events))
        self.replay(session, load=True)

    def test_modal_transform_to_sorcery_leaves_the_land_unchanged(self):
        from quorune.permanent_transform import transform_permanent

        session = self.session(326008)
        engine = session.engine
        source = self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", zone="hand")
        self.ready(session, source, {})
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(action for action in actions if action["id"].startswith(f"play-land:{source.ref}"))
        self.checkpoint(session)
        played = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(played.ok, played.summary)
        current = engine.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual("Bala Ged Sanctuary", current.active_face)
        before = authoritative_state_hash(engine.state)
        self.assertIsNone(transform_permanent(
            engine, current, expected_transform_count=0, day_night_instruction=False,
        ))
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.replay(session, load=True)

    def test_actual_ulvenwald_transform_preserves_object_and_uses_back_mana_ability(self):
        self._ulvenwald_transform_journey(legacy=False)

    def test_historical_absent_version_transform_preserves_legacy_timestamp_and_replay(self):
        self._ulvenwald_transform_journey(legacy=True)

    def _ulvenwald_transform_journey(self,*,legacy):
        s=self.session(326001);e=s.engine;source=self.add(e,'Ulvenwald Captive // Ulvenwald Abomination');source.acquired_control_turn_count=-1;source.counters['+1/+1']=1;source.marked_damage=1
        if legacy:e.state.transform_timestamp_version=None
        self.ready(s,source,{'G':3,'C':6});self.checkpoint(s);identity=source.logical_object_id;timestamp=source.zone_timestamp
        action=self.activation(s,source);before=authoritative_state_hash(e.state)
        wrong=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual(0,e.state.stack[-1].context.get('source_transform_count'));self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('Ulvenwald Abomination',current.active_face)
        self.assertEqual(identity,current.logical_object_id)
        if legacy:self.assertEqual(timestamp,current.zone_timestamp)
        else:self.assertGreater(current.zone_timestamp,timestamp)
        self.assertEqual(1,current.counters['+1/+1']);self.assertEqual(1,current.marked_damage)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+current.ref+':') and 'Add {C}{C}' in a['label'])
        count=e.state.players['A'].mana_pool['C'];result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(count+2,e.state.players['A'].mana_pool['C']);self.replay(s,load=True)

    def test_two_actual_gargoyle_activations_transform_only_once_since_stack_placement(self):
        s=self.session(326002);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer')
        self.ready(s,source,{'C':12});self.checkpoint(s)
        for _ in range(2):
            result=s.act('pilot:A',{'action_id':self.activation(s,source)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(e.state.stack));sequence=e.state.timestamp_sequence
        self.resolve(s);current=e.state.cards[source.object_id]
        self.assertEqual(sequence+1,e.state.timestamp_sequence)
        self.assertEqual(e.state.timestamp_sequence,current.zone_timestamp)
        self.assertEqual('Stonewing Antagonizer',current.active_face);self.assertEqual(1,current.transform_count);self.assertIn('flying',e._combat_keywords(current));self.replay(s,load=True)

    def test_omitting_activation_transform_count_mutant_is_killed(self):
        with patch('quorune.rules.activation.commit.activation_transform_context',return_value={}):
            with self.assertRaises(AssertionError):self.test_actual_ulvenwald_transform_preserves_object_and_uses_back_mana_ability()

    def test_actual_gossipmonger_source_and_selected_tap_costs_transform_same_object(self):
        s=self.session(326003);e=s.engine;source=self.add(e,'Town Gossipmonger // Incited Rabble');source.acquired_control_turn_count=-1;body=self.add(e,'Generic Bound Body')
        self.ready(s,source,{});self.checkpoint(s);action=self.activation(s,source)
        before=authoritative_state_hash(e.state)
        rejected=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[source.ref],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(e.state.cards[source.object_id].tapped);self.assertTrue(e.state.cards[body.object_id].tapped)
        self.resolve(s);current=e.state.cards[source.object_id];self.assertEqual('Incited Rabble',current.active_face);self.assertTrue(current.tapped);self.replay(s,load=True)

    def test_actual_source_blink_before_activation_resolution_does_not_transform_new_object(self):
        s=self.session(326004);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer');blink=self.add(e,'Ephemerate',zone='hand')
        self.ready(s,source,{'W':2,'C':7});self.checkpoint(s);old_identity=source.logical_object_id
        result=s.act('pilot:A',{'action_id':self.activation(s,source)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==blink.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertNotEqual(old_identity,current.logical_object_id);self.assertEqual('Thraben Gargoyle',current.active_face);self.assertEqual(0,current.transform_count);self.replay(s,load=True)

    def test_actual_control_change_keeps_original_activation_controller_and_source_binding(self):
        s=self.session(326005);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer',seat='B');control=self.add(e,'Word of Seizing',zone='hand')
        self.ready(s,control,{'R':2,'C':4});e.state.players['B'].mana_pool['C']=7
        e.permissions.invalidate_current();e.state.pending_decision=None;e._grant_priority('B');e.pump();self.checkpoint(s)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:A':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==control.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve(s);self.assertEqual('A',e.state.cards[source.object_id].controller);self.assertEqual('Stonewing Antagonizer',e.state.cards[source.object_id].active_face);self.replay(s,load=True)

    def test_actual_gargoyle_cast_then_transform_uses_offered_front_and_new_flying_face(self):
        s=self.session(326006);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer',zone='hand')
        action=self.ready(s,source,{'C':9});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('Thraben Gargoyle',current.active_face);self.assertIn('defender',e._combat_keywords(current))
        result=s.act('pilot:A',{'action_id':self.activation(s,current)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('Stonewing Antagonizer',current.active_face);self.assertIn('flying',e._combat_keywords(current));self.assertNotIn('defender',e._combat_keywords(current));self.replay(s,load=True)

    def test_omitting_selected_face_keyword_filter_mutant_is_killed(self):
        with patch('quorune.rules.current_face_keywords.current_printed_face_keywords',side_effect=lambda host,card,record,base:base.get('keywords',[])):
            with self.assertRaises(AssertionError):self.test_actual_gargoyle_cast_then_transform_uses_offered_front_and_new_flying_face()


class TriggeredTransformRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "triggered-transform.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/activated-self-transform-cards.json",
            ROOT / "tests/fixtures/ability-label-composition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Triggered Transform", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    def set_front_face(self, card):
        card.active_face = str(self.db.by_oracle_id(card.oracle_id).faces[0]["name"])

    def sacrifice(self, session, altar, creature):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{altar.ref}:")]
        result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [creature.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[creature.object_id].zone)

    def transform(self, session, seat, card):
        self.priority(session, seat)
        actions = session.packet("pilot:" + seat, full=True)["decision"]["legal_actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{card.ref}:")]
        command = {"action_id": action["id"], "pay": "auto"}
        if action.get("cost_options"):
            option = next(row for row in action["cost_options"] if not row.get("life_payment"))
            command["cost_option"] = option["id"]
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_cult_counts_only_controlled_nonhuman_post_transform(self):
        session = self.session(70127046)
        engine = session.engine
        cult = self.add(engine, "Cult of the Waxing Moon")
        gargoyle = self.add(engine, "Thraben Gargoyle // Stonewing Antagonizer")
        foreign = self.add(engine, "Thraben Gargoyle // Stonewing Antagonizer", seat="B", ref="foreign-gargoyle")
        sentry = self.add(engine, "Thraben Sentry // Thraben Militia")
        altar = self.add(engine, "Ashnod's Altar")
        body = self.add(engine, "Generic Bound Body")
        for card in (gargoyle, foreign, sentry):
            self.set_front_face(card)
        for card in (cult, gargoyle, sentry):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 6})
        session.state.players["B"].mana_pool["C"] = 6
        self.checkpoint(session)

        self.transform(session, "A", gargoyle)
        self.resolve_top(session)
        self.assertTrue(any(item.source_object_id == cult.object_id for item in session.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.transform(session, "B", foreign)
        self.assertIsNone(self.resolve(session))
        self.sacrifice(session, altar, body)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

        self.assertEqual("Stonewing Antagonizer", session.state.cards[foreign.object_id].active_face)
        self.assertEqual("Thraben Militia", session.state.cards[sentry.object_id].active_face)
        wolves = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(wolves))
        self.assertEqual("A", wolves[0].controller)
        self.assertIn("wolf", session.engine._type_parts(session.engine._effective_card_data(wolves[0])["type_line"])[1])
        self.replay(session, load=True)

    def test_actual_blightsower_uses_post_transform_and_pre_death_faces(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.rules.transform_capability_shapes import PERMANENT_TRANSFORM_CAPABILITY_ID
        from quorune.transform_timestamp_model import TRANSFORM_EVENT

        session = self.session(70127047)
        engine = session.engine
        name = "Blightreaper Thallid // Blightsower Thallid"
        source = self.add(engine, name)
        front = self.add(engine, name, ref="another-front-thallid")
        altar = self.add(engine, "Ashnod's Altar")
        self.set_front_face(source)
        self.set_front_face(front)
        self.assert_closed(session, source)
        trigger, = [program for program in engine.semantics.programs_for_oracle(source.oracle_id)
                    if program.event == TRANSFORM_EVENT]
        deps = tuple(cap for cap in trigger.capability_dependencies if cap != PERMANENT_TRANSFORM_CAPABILITY_ID)
        incomplete = replace(
            trigger, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile="commander_review").to_dict(),
        )
        binding = bind_semantic_program_runtime(incomplete, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + PERMANENT_TRANSFORM_CAPABILITY_ID, binding["blockers"])
        self.prepare(session, mana={"C": 3, "G": 1})
        self.checkpoint(session)

        self.sacrifice(session, altar, front)
        self.assertFalse(session.state.stack)
        self.transform(session, "A", source)
        self.resolve_top(session)
        self.assertEqual("Blightsower Thallid", session.state.cards[source.object_id].active_face)
        self.assertEqual([source.object_id], [item.source_object_id for item in session.state.stack])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.sacrifice(session, altar, session.state.cards[source.object_id])
        self.assertEqual([source.object_id], [item.source_object_id for item in session.state.stack])
        self.assertIsNone(self.resolve(session))
        saprolings = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(saprolings))
        self.assertTrue(all("saproling" in session.engine._type_parts(
            session.engine._effective_card_data(card)["type_line"],
        )[1] for card in saprolings))
        self.replay(session, load=True)

    def test_actual_inquisitor_counters_the_sealed_transformed_incarnation(self):
        session = self.session(70127048)
        source = self.add(session.engine, "Norn's Inquisitor", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3, "W": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        identity = token.logical_object_id
        self.assertEqual(("Incubator Token", 2), (token.active_face, token.counters["+1/+1"]))

        self.transform(session, "A", token)
        self.resolve_top(session)
        item, = session.state.stack
        self.assertEqual(source.object_id, item.source_object_id)
        self.assertFalse(item.targets)
        self.assertEqual(identity, item.context["event_counter_recipient"]["logical_object_id"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        transformed = session.state.cards[token.object_id]
        self.assertEqual((identity, 3), (transformed.logical_object_id, transformed.counters["+1/+1"]))
        self.replay(session, load=True)

    def test_actual_brutal_cathar_night_entry_day_transform_and_linked_return(self):
        session = self.session(70127050)
        engine = session.engine
        cathar = self.add(engine, "Brutal Cathar // Moonrage Brute", zone="hand")
        second = self.add(engine, "Sol Ring", zone="hand")
        victim = self.add(engine, "Generic Bound Body", seat="B")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        self.add(engine, "Island", seat="B")
        for card in (cathar, second, bounce):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "W": 1})
        session.state.day_night = "night"
        self.checkpoint(session)

        self.cast(session, "A", cathar)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("Moonrage Brute", session.state.cards[cathar.object_id].active_face)
        self.assertEqual("battlefield", session.state.cards[victim.object_id].zone)
        self.cast(session, "A", second)
        self.assertIsNone(self.resolve(session))

        self.target(session, "A", [victim.ref])
        self.assertEqual("day", session.state.day_night)
        self.assertEqual("Brutal Cathar", session.state.cards[cathar.object_id].active_face)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[victim.object_id].zone)
        self.cast(session, "B", bounce, [cathar.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[victim.object_id].zone)
        self.replay(session, load=True)

    def test_legacy_transform_replay_omits_new_event_characteristics(self):
        session = self.session(70127049)
        engine = session.engine
        source = self.add(engine, "Thraben Gargoyle // Stonewing Antagonizer")
        self.set_front_face(source)
        self.assert_closed(session, source)
        session.state.transform_event_characteristics_version = None
        self.prepare(session, mana={"C": 6})
        self.checkpoint(session)
        with patch.object(engine, "_dispatch_semantic_event", wraps=engine._dispatch_semantic_event) as dispatch:
            self.transform(session, "A", source)
            self.assertIsNone(self.resolve(session))
        context, = [call.args[1] for call in dispatch.call_args_list
                    if call.args and call.args[0] == "permanent.transforms"]
        self.assertNotIn("transformed_name", context)
        self.assertNotIn("subtypes", context)
        replayed = self.replay(session, load=True)
        self.assertNotIn("transform_event_characteristics_version", replayed.state.to_dict())

    def test_actual_farmer_transforms_once_across_two_pending_deaths_and_replays(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE

        session = self.session(70127041)
        engine = session.engine
        farmer = self.add(engine, "Desperate Farmer // Depraved Harvester")
        self.set_front_face(farmer)
        altar = self.add(engine, "Ashnod's Altar")
        first = self.add(engine, "Generic Bound Body", ref="first-farmer-death")
        second = self.add(engine, "Generic Bound Body", ref="second-farmer-death")
        self.assert_closed(session, farmer)
        program = next(row for row in engine.semantics.programs_for_oracle(farmer.oracle_id)
            if "fixed-triggered-source-transform" in row.coverage)
        incomplete = replace(program, coverage=tuple(item for item in program.coverage
            if item != SPELL_HISTORY_TRANSFORM_COVERAGE))
        binding = bind_semantic_program_runtime(incomplete, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertTrue(any("runtime_triggered_source_transform" in value for value in binding["blockers"]))
        self.prepare(session)
        self.checkpoint(session)
        self.sacrifice(session, altar, first)
        self.sacrifice(session, altar, second)
        identity = farmer.logical_object_id
        self.assertEqual(2, len(session.state.stack))
        for item in session.state.stack:
            self.assertEqual(farmer.object_id, item.source_object_id)
            self.assertEqual((identity, 0), (item.context["source_logical_object_id"], item.context["source_transform_count"]))
            self.assertFalse(item.targets)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        result = session.state.cards[farmer.object_id]
        self.assertEqual((identity, "Depraved Harvester", 1),
            (result.logical_object_id, result.active_face, result.transform_count))
        self.replay(session, load=True)

    def test_actual_thraben_sentry_can_decline_or_apply_the_untargeted_transform(self):
        for choice in ("decline", "apply"):
            with self.subTest(choice=choice):
                session = self.session(70127042 + (choice == "apply"))
                engine = session.engine
                sentry = self.add(engine, "Thraben Sentry // Thraben Militia")
                self.set_front_face(sentry)
                altar = self.add(engine, "Ashnod's Altar")
                body = self.add(engine, "Generic Bound Body")
                self.assert_closed(session, sentry)
                self.prepare(session)
                self.checkpoint(session)
                self.sacrifice(session, altar, body)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
                command = {"action_id": "choose", "choice": choice}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:D", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                accepted = session.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[sentry.object_id]
                self.assertEqual(("Thraben Militia", 1) if choice == "apply" else ("Thraben Sentry", 0),
                    (current.active_face, current.transform_count))
                self.replay(session, load=True)

    def test_actual_autumnal_gloom_rechecks_graveyard_types_at_resolution(self):
        for remove_type in (False, True):
            with self.subTest(remove_type=remove_type):
                session = self.session(70127044 + remove_type)
                engine = session.engine
                source = self.add(engine, "Autumnal Gloom // Ancient of the Equinox")
                self.set_front_face(source)
                ring = self.add(engine, "Sol Ring", zone="graveyard")
                self.add(engine, "Generic Bound Body", zone="graveyard")
                self.add(engine, "Generic Bound Growth", zone="graveyard")
                self.add(engine, "Generic Bound Plains", zone="graveyard")
                response = self.add(engine, "Cremate", seat="B", zone="hand")
                self.add(engine, "Swamp", seat="B")
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.until(session, lambda game: game.state.active_player == "A"
                    and game.state.step == "end_step" and bool(game.state.stack))
                item, = session.state.stack
                self.assertEqual(source.object_id, item.source_object_id)
                self.assertEqual(0, item.context["source_transform_count"])
                if remove_type:
                    self.cast_for(session, "B", response, [ring.ref])
                    self.resolve_top(session)
                    self.assertEqual("exile", session.state.cards[ring.object_id].zone)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[source.object_id]
                self.assertEqual(("Autumnal Gloom", 0) if remove_type else ("Ancient of the Equinox", 1),
                    (current.active_face, current.transform_count))
                self.replay(session, load=True)
