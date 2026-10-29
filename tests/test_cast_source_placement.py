"""CR601.2a source placement and additive historical checkpoint provenance."""
import tempfile,unittest
from pathlib import Path
import state_trigger_fixture as fixtures
from quorune.cast_source_placement_model import cast_source_placement_version
from quorune.model import GameState
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions,validate_state_versions

class CastSourcePlacementTests(fixtures.StateTriggerFixture,unittest.TestCase):
    def test_omitted_prospective_characteristic_binding_mutant_is_killed(self):
        from unittest.mock import patch
        self.test_public_cost_predicate_uses_layered_characteristics_after_source_leaves_hand()
        with patch('quorune.rules.casting.source_placement.prospective_characteristic_read_host',side_effect=lambda host,*_args:host),self.assertRaises(AssertionError):
            self.test_public_cost_predicate_uses_layered_characteristics_after_source_leaves_hand()

    def test_new_state_subscriptions_fail_closed_on_a_legacy_placement_checkpoint(self):
        from quorune.rules.state_triggers import observe_public_state_triggers
        from quorune.errors import GameRuleError
        session=self.session(612651003);self.add(session.engine,'Emperor Crocodile',zone='hand');session.state.cast_source_placement_version=None;before=authoritative_state_hash(session.state)
        with self.assertRaises(GameRuleError):observe_public_state_triggers(session.engine)
        self.assertEqual(before,authoritative_state_hash(session.state))
    def test_public_cost_predicate_uses_layered_characteristics_after_source_leaves_hand(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.rules.casting.public_conditions import self_public_condition_multiplier
        session=self.session(612651040)
        for oid in tuple(session.state.players['A'].zones['hand']):session.engine.move_card(oid,'library',reason='Prepared public cost condition hand',semantic_events=False)
        spell=self.add(session.engine,'Leatherback Baloth',zone='hand');self.add(session.engine,'Shock',zone='hand');body=self.add(session.engine,'Maro');self.prepare(session);condition=fixed_public_state_condition('you control a creature with toughness 2 or greater',source_name=spell.printed_name);self.assertIsNotNone(condition);self.assertEqual(2,session.engine._numeric_stat(body.object_id,'toughness'));before=authoritative_state_hash(session.state)
        self.assertEqual(0,self_public_condition_multiplier(session.engine,'A',spell,condition));self.assertEqual(before,authoritative_state_hash(session.state));session.state.cast_source_placement_version=None;self.assertEqual(1,self_public_condition_multiplier(session.engine,'A',spell,condition))
    def test_advertised_dynamic_mana_accounts_for_the_spell_leaving_hand(self):
        from quorune.attachments import attach_objects
        for hand_count in (2,3):
            with self.subTest(hand_count=hand_count):
                session=self.session(612651030+hand_count)
                for oid in tuple(session.state.players['A'].zones['hand']):session.engine.move_card(oid,'library',reason='Prepared bounded hand before mana source exists',semantic_events=False)
                spell=self.add(session.engine,'Leatherback Baloth',zone='hand');source=self.add(session.engine,'Viridian Joiner');source.acquired_control_turn_count=-1;armor=self.add(session.engine,'Empyrial Armor')
                for i in range(hand_count-1):self.add(session.engine,'Shock',zone='hand',ref='mana-hand-'+str(i))
                attach_objects(session.state.cards,armor,source,source_timestamp=session.engine._next_zone_timestamp(),players=session.state.players);self.assert_closed(session,source);self.assert_closed(session,armor);self.assert_closed(session,spell);self.prepare(session);self.checkpoint(session)
                self.assertEqual(1+hand_count,session.engine._numeric_stat(source.object_id,'power'));before_offer=authoritative_state_hash(session.state);actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'];offered=next((a for a in actions if a['id']=='cast:'+spell.ref),None);self.assertEqual(before_offer,authoritative_state_hash(session.state))
                if hand_count==2:
                    self.assertIsNone(offered);before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+spell.ref,'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
                else:
                    self.assertIsNotNone(offered);result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.settle(session);self.assertEqual('battlefield',session.state.cards[spell.object_id].zone);self.replay(session,load=True)

    def test_marker_roundtrip_rejects_boolean_and_legacy_absence_keeps_old_payload(self):
        session=self.session(612651001);raw=session.state.to_dict();self.assertEqual(1,raw['cast_source_placement_version']);self.assertEqual(raw,GameState.from_dict(raw).to_dict())
        raw.pop('cast_source_placement_version');old=GameState.from_dict(raw);self.assertIsNone(old.cast_source_placement_version);self.assertEqual(raw,old.to_dict());self.assertEqual(1,cast_source_placement_version(1));self.assertIsNone(cast_source_placement_version(None))
        for value in (True,False,0,2,'1'):
            with self.assertRaises(ValueError):GameState.from_dict({**raw,'cast_source_placement_version':value})

    def test_manifest_binds_initial_source_placement_marker_and_legacy_absence(self):
        session=self.session(612651002);state=session.state;manifest={'format':format_state_versions(state)};validate_state_versions(manifest,state)
        for value in (None,0,True,2):
            changed={'format':dict(manifest['format'])}
            if value is None:changed['format'].pop('cast_source_placement_version')
            else:changed['format']['cast_source_placement_version']=value
            with self.assertRaises(ValueError):validate_state_versions(changed,state)
        state.cast_source_placement_version=None;legacy={'format':format_state_versions(state)};self.assertNotIn('cast_source_placement_version',legacy['format']);validate_state_versions(legacy,state)

    def test_real_additional_sacrifice_cast_preserves_versioned_timestamp_order_and_replay(self):
        for current in (False,True):
            with self.subTest(current=current):
                session=self.session(612651010+current);session.state.cast_source_placement_version=1 if current else None;spell=self.add(session.engine,"Altar's Reap",zone='hand');body=self.add(session.engine,'Grizzly Bears');self.prepare(session,mana={'C':1,'B':1});self.checkpoint(session);offered=self.action(session,'A',spell)
                before=authoritative_state_hash(session.state)
                denied=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto','sacrifice_cards':[]});self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto','sacrifice_cards':[body.ref]});self.assertTrue(accepted.ok,accepted.summary);self.assertEqual('stack',session.state.cards[spell.object_id].zone);self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
                a=session.state.cards[spell.object_id].zone_timestamp;b=session.state.cards[body.object_id].zone_timestamp;self.assertEqual(current,a<b)
                session=self.replay(session,load=True);self.settle(session);self.replay(session,load=True)
