"""CR109.5/113.6b/702.112b: source zones and designations are independent facts."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_restricted_mana_grants as native
import test_ring_native_actions as turns
from quorune.carddb import CardDatabase
from quorune.continuous_conditions import (
    FixedPublicStateConditionSpec,FixedPublicStateConditionKind,FixedPublicStateFact,
    GRAVEYARD_TYPED_GRANT_CAPABILITY_ID as CAPABILITY,
    GRAVEYARD_QUERY_GRANT_HANDLER_ID,GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID,
)
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


PORTAL='As long as this card is in your graveyard, lands you control have "{T}: Add {G} or {W}."'
CONDITIONAL='As long as this card is in your graveyard and you control an Island, lands you control have "{T}: Add {G}."'
RENOWN='As long as this creature is renowned, it has vigilance and "{T}: Add one mana of any color."'


class SourceZoneAbilityGrantCompilerTests(unittest.TestCase):
    def test_source_zone_and_renowned_fact_are_closed_and_independently_lowered(self):
        registry=load_default_capability_registry()
        for text,handler,zone in ((PORTAL,GRAVEYARD_QUERY_GRANT_HANDLER_ID,'graveyard'),
                (CONDITIONAL,GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID,'graveyard'),
                (RENOWN,'continuous.characteristics.fixed-public-state.v2','battlefield')):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                outer,child=ir.faces[0].nodes
                self.assertEqual(zone,outer.active_zone);self.assertEqual(handler,outer.handlers[0]['handler_id'])
                self.assertEqual('battlefield',child.active_zone);self.assertEqual('granted_mana_ability',child.kind)
                if zone=='graveyard':self.assertIn(CAPABILITY,outer.capability_dependencies)
                else:
                    self.assertIn('permanent.designation.renowned',outer.capability_dependencies)
                    self.assertEqual('source_renowned',outer.handlers[0]['source_condition']['fact'])
        for text in (PORTAL.replace('your graveyard','exile'),PORTAL.replace('lands you control','those cards'),
                CONDITIONAL.replace('you control an Island','the top card of your library is blue'),
                PORTAL.replace('Add {G} or {W}.','Add {G}, then rewrite history.'),
                RENOWN.replace('renowned','wearing a hat')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)
        mixed=compile_oracle_card(query_record(PORTAL.replace('have "','have vigilance and "')),
            capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',mixed.status,mixed.material_residuals)
        self.assertEqual(['Vigilance'],mixed.faces[0].nodes[0].handlers[0]['modifier']['add_abilities'])

    def test_renowned_fact_codec_does_not_replace_designation_with_counters(self):
        condition=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=1,fact=FixedPublicStateFact.SOURCE_RENOWNED,schema_version=4)
        self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
        for changes in ({'amount':2},{'schema_version':3},{'schema_version':True},{'fact_parameter':'counter'},
                {'counter_name':'+1/+1'},{'unexpected':False}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                FixedPublicStateConditionSpec.from_dict({**condition.to_dict(),**changes})

    def test_verified_outer_inner_and_designation_owners_withdraw_independently(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for text,owners in ((PORTAL,(CAPABILITY,'continuous.ability.graveyard_static_grant','mana.activated.fixed_output')),
                (RENOWN,('permanent.designation.renowned','continuous.ability.fixed_public_state_grant'))):
            for owner in (*owners,'variant.monarch.designate'):
                value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['source grant owner withdrawn'])
                registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',(text,owner))


class SourceZoneAbilityGrantNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.RestrictedManaGrantNativeTests.session
    add=native.RestrictedManaGrantNativeTests.add
    prepare=native.RestrictedManaGrantNativeTests.prepare
    cast=native.RestrictedManaGrantNativeTests.cast
    cast_for=native.RestrictedManaGrantNativeTests.cast_for
    action=native.RestrictedManaGrantNativeTests.action
    priority=native.RestrictedManaGrantNativeTests.priority
    checkpoint=native.RestrictedManaGrantNativeTests.checkpoint
    resolve=native.RestrictedManaGrantNativeTests.resolve
    replay=native.RestrictedManaGrantNativeTests.replay
    assert_closed=native.RestrictedManaGrantNativeTests.assert_closed
    granted_action=native.RestrictedManaGrantNativeTests.granted_action
    activate_mana=native.RestrictedManaGrantNativeTests.activate_mana
    until=turns.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-zone-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/source-zone-ability-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source facts',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def grants(self,session,card):
        return tuple(a for a in session.engine._activated_abilities(session.state.cards[card.object_id]) if a.ability_id.startswith('ability:granted:'))

    def test_actual_portal_uses_graveyard_owner_and_loses_future_grants_after_reclaim(self):
        session=self.session(113615001)
        source=self.add(session.engine,'Riftstone Portal',zone='graveyard');source.controller='B'
        first=self.add(session.engine,'Island');second=self.add(session.engine,'Wastes')
        opponent=self.add(session.engine,'Island',seat='B',ref='other-owner-land')
        reclaim=self.add(session.engine,'Reclaim',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.assertEqual((1,1,0),tuple(len(self.grants(session,c)) for c in (first,second,opponent)))
        self.activate_mana(session,'A',first,'G');self.activate_mana(session,'A',second,'W')
        self.cast(session,'A',reclaim,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('library',session.state.cards[source.object_id].zone)
        self.assertEqual((0,0),tuple(len(self.grants(session,c)) for c in (first,second)))
        self.assertEqual(1,session.state.players['A'].mana_pool['W'])
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_renown_survives_counter_removal_and_resets_after_blink(self):
        session=self.session(113615010)
        source=self.add(session.engine,'Honored Hierarch');source.acquired_control_turn_count=-1
        untap=self.add(session.engine,'Twiddle',zone='hand');self.add(session.engine,'Island')
        self.add(session.engine,'Plains',ref='renown-own-white')
        remove=self.add(session.engine,'Disenchant',zone='hand')
        loss=self.add(session.engine,'Dress Down',seat='B',zone='hand')
        blue=self.add(session.engine,'Island',seat='B',ref='renown-loss-blue')
        colorless=self.add(session.engine,'Wastes',seat='B',ref='renown-loss-colorless')
        hexmage=self.add(session.engine,'Vampire Hexmage',seat='B')
        flicker=self.add(session.engine,'Flicker of Fate',seat='B',zone='hand')
        self.add(session.engine,'Plains',seat='B');self.add(session.engine,'Wastes',seat='B')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.assertFalse(self.grants(session,source))
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.cards[source.object_id].renowned and not s.state.stack)
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0));self.assertEqual(1,len(self.grants(session,source)))
        self.assertIn('vigilance',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.cast(session,'A',untap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.activate_mana(session,'A',source,'R')
        self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        for card,color in ((blue,'U'),(colorless,'C')):
            self.priority(session,'B')
            action=next(r for r in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+card.ref+':'))
            result=session.act('pilot:B',{'action_id':action['id'],'mana_output':{color:1}});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',loss);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].renowned);self.assertFalse(self.grants(session,source))
        self.cast(session,'A',remove,[loss.ref]);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].renowned);self.assertEqual(1,len(self.grants(session,source)))
        self.priority(session,'B')
        offered=next(r for r in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+hexmage.ref+':'))
        result=session.act('pilot:B',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertTrue(session.state.cards[source.object_id].renowned);self.assertEqual(1,len(self.grants(session,source)))
        identity=session.state.cards[source.object_id].logical_object_id
        self.cast(session,'B',flicker,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertNotEqual(identity,session.state.cards[source.object_id].logical_object_id)
        self.assertFalse(session.state.cards[source.object_id].renowned);self.assertFalse(self.grants(session,source))
        self.replay(session,load=True)

    def test_runtime_parent_and_renowned_condition_minima_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(113615020)
        for name,owner in (('Riftstone Portal',CAPABILITY),('Honored Hierarch','permanent.designation.renowned')):
            card=self.add(session.engine,name,zone='hand');checked=0
            for program in session.engine.semantics.programs_for_oracle(card.oracle_id):
                if owner not in program.capability_dependencies:continue
                checked+=1;deps=tuple(cap for cap in program.capability_dependencies if cap!=owner)
                altered=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+owner,bound['blockers'])
            self.assertGreater(checked,0)

    def test_conditional_graveyard_grant_rechecks_owner_state_after_a_real_land_bounce(self):
        session=self.session(113615030)
        source=self.add(session.engine,'Generic Graveyard Mana Observer',zone='graveyard');source.controller='B'
        island=self.add(session.engine,'Island');land=self.add(session.engine,'Wastes')
        self.add(session.engine,'Island',seat='B',ref='condition-opponent-island-one')
        self.add(session.engine,'Island',seat='B',ref='condition-opponent-island-two')
        bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.assertEqual(1,len(self.grants(session,land)));self.activate_mana(session,'A',land,'G')
        self.cast(session,'B',bounce,[island.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[island.object_id].zone)
        self.assertFalse(self.grants(session,land));self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.replay(session,load=True)

    def test_graveyard_owner_mutant_is_killed_by_actual_recipient_membership(self):
        import quorune.continuous_conditions as owner
        with patch.object(owner.OwnerRelativeConditionSource,'controller',property(lambda value:value.source.controller)),self.assertRaises(AssertionError):
            self.test_actual_portal_uses_graveyard_owner_and_loses_future_grants_after_reclaim()

    def test_counter_proxy_mutant_is_killed_after_actual_counter_removal(self):
        import quorune.card_programs.runtime as owner
        original=owner._FixedPublicStateSnapshotResolver._public_fact_quantity
        def mutant(resolver,source,condition):
            if condition.fact is FixedPublicStateFact.SOURCE_RENOWNED:return int(source.counters.get('+1/+1',0)>0)
            return original(resolver,source,condition)
        session=self.session(113615040)
        source=self.add(session.engine,'Honored Hierarch');source.renowned=True;source.counters['+1/+1']=1
        hexmage=self.add(session.engine,'Vampire Hexmage',seat='B')
        self.prepare(session,actor='B');self.checkpoint(session)
        self.assertEqual(1,len(self.grants(session,source)))
        offered=next(r for r in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+hexmage.ref+':'))
        result=session.act('pilot:B',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].renowned)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(1,len(self.grants(session,source)))
        with patch.object(owner._FixedPublicStateSnapshotResolver,'_public_fact_quantity',mutant),self.assertRaises(AssertionError):
            self.assertEqual(1,len(self.grants(session,source)))
