from __future__ import annotations

from dataclasses import FrozenInstanceError,replace
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from damage_replacement_support import DamageReplacementPipelineBase
from quorune.life_gain_permission import (ALLOW_ALL_LIFE_GAIN,FixedLifeGainProhibitionSpec,
    LifeGainPermissionSnapshot)
from quorune.life_change import (LifeChangeRequest,commit_life_change_batch,
    prepare_life_change_batch,validate_life_change_batch)
from quorune.replacement import AddAmount,MultiplyAmount
from test_life_change import replacement
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_land_search_view_equivalence as native
import test_ability_label_composition as journeys
from quorune.rules.evidence_storage import read_evidence_document


class LifeGainPermissionKernelTests(DamageReplacementPipelineBase):
    def test_fixed_life_gain_addition_only_changes_positive_controller_gains(self):
        from quorune.compiler.life_templates import static_life_handler
        from quorune.replacement import ReplaceableEvent, resolve_replacements
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantic_runtime.life_replacements import LifeGainAdditionHandler, LifeReplacementSourceContext

        text = 'If you would gain life, you gain that much life plus 1 instead.'
        _, descriptor, capability = static_life_handler(text)
        self.assertEqual('life.gain.replacement.static_addition', capability)
        context = LifeReplacementSourceContext('fixture:gain-source', 'A')
        handler = LifeGainAdditionHandler()
        effect = handler.replacement_effect(descriptor, context)
        for seat, direction, amount, expected in (('A', 'gain', 3, 4), ('B', 'gain', 3, 3),
                                                   ('A', 'gain', 0, 0), ('A', 'loss', 3, 3)):
            event = ReplaceableEvent(event_id=f'life:{seat}:{direction}:{amount}', kind='life.change',
                                     affected_player=seat, payload={'direction': direction, 'amount': amount})
            selections = (effect.effect_id,) if seat == 'A' and direction == 'gain' and amount > 0 else ()
            resolved = resolve_replacements(event, (effect,), selections=selections)
            self.assertEqual(expected, resolved.payload['amount'])
        for invalid in ({**descriptor, 'schema_version': True},
                        {**descriptor, 'condition': {'affected_player_relation': 'opponent'}},
                        *({**descriptor, 'modification': {'addition': amount}} for amount in (0, -1, True))):
            with self.assertRaises(SemanticNodeError):
                handler.replacement_effect(invalid, context)
        self.assertIsNone(static_life_handler(text.replace('plus 1', 'plus X')))
        self.assertIsNone(static_life_handler(text + ' Draw a card.'))

    def test_fixed_life_gain_addition_amount_mutation_is_detected(self):
        from quorune.semantic_runtime.life_replacements import LifeGainAdditionHandler

        original = LifeGainAdditionHandler.replacement_effect

        def different_amount(handler, descriptor, context):
            return original(handler, {**descriptor, 'modification': {'addition': 2}}, context)

        with patch.object(LifeGainAdditionHandler, 'replacement_effect', different_amount):
            with self.assertRaises(AssertionError):
                self.test_fixed_life_gain_addition_only_changes_positive_controller_gains()

    def test_closed_global_and_opponent_scopes_use_current_controller_and_immutable_snapshots(self):
        opponents = FixedLifeGainProhibitionSpec('opponent')
        self.assertEqual(opponents,FixedLifeGainProhibitionSpec.from_dict(opponents.to_dict()))
        a = opponents.permissions('A',tuple('ABCD'))
        b = opponents.permissions('B',tuple('ABCD'))
        self.assertEqual(tuple('BCD'),a.denied_players)
        self.assertEqual(tuple('ACD'),b.denied_players)
        self.assertTrue(a.allows_gain('A'))
        self.assertFalse(b.allows_gain('A'))
        self.assertEqual(tuple('ABCD'),FixedLifeGainProhibitionSpec('any').permissions('B',tuple('ABCD')).denied_players)
        with self.assertRaises(FrozenInstanceError):
            a.denied_players = ()
        for value in ('source_owner','self',[],None):
            with self.assertRaises(ValueError):FixedLifeGainProhibitionSpec(value)
        for value in ({'schema_version':True,'affected_player_relation':'any'},
                {'schema_version':2,'affected_player_relation':'any'},
                {'schema_version':1,'affected_player_relation':'any','future':True}):
            with self.assertRaises(ValueError):FixedLifeGainProhibitionSpec.from_dict(value)
        for players in (('B','A'),('A','A'),['A'],(False,)):
            with self.assertRaises(ValueError):LifeGainPermissionSnapshot(players)

    def test_prohibited_gain_precedes_replacement_choice_and_commits_no_life_change(self):
        engine = self.session(119_700_001).engine
        before = engine.state.players['B'].life
        effects = (replacement('gain-add',AddAmount(field='amount',amount=1)),
            replacement('gain-double',MultiplyAmount(field='amount',factor=2)))
        prepared = prepare_life_change_batch(engine,
            (LifeChangeRequest('prohibited-gain','B',3),),effects=effects,
            require_all_selections=False,gain_permissions=LifeGainPermissionSnapshot(('B',)))
        self.assertIsNone(prepared.pending)
        self.assertEqual((),prepared.journal)
        self.assertEqual((3,0),(prepared.records[0].requested_amount,prepared.records[0].amount))
        validate_life_change_batch(engine,prepared)
        result = commit_life_change_batch(engine,prepared)
        self.assertEqual(before,engine.state.players['B'].life)
        self.assertEqual((),result.changed_players)

    def test_prohibition_does_not_block_loss_or_another_players_gain(self):
        engine = self.session(119_700_002).engine
        a,b = (engine.state.players[p].life for p in 'AB')
        prepared = prepare_life_change_batch(engine,(
            LifeChangeRequest('denied','B',3),LifeChangeRequest('loss','B',-2),
            LifeChangeRequest('allowed','A',3)),
            effects=(replacement('double',MultiplyAmount(field='amount',factor=2)),),
            gain_permissions=LifeGainPermissionSnapshot(('B',)))
        commit_life_change_batch(engine,prepared)
        self.assertEqual((a+6,b-2),(engine.state.players['A'].life,engine.state.players['B'].life))
        self.assertEqual({'denied':0,'loss':2,'allowed':6},{r.event_id:r.amount for r in prepared.records})

    def test_mixed_gain_replacement_continuation_contains_only_permitted_events(self):
        engine = self.session(119_700_005).engine
        requests = (LifeChangeRequest('denied-choice','B',3),LifeChangeRequest('allowed-choice','A',3))
        effects = (replacement('mixed-add',AddAmount(field='amount',amount=1)),
            replacement('mixed-double',MultiplyAmount(field='amount',factor=2)))
        permission = LifeGainPermissionSnapshot(('B',))
        pending = prepare_life_change_batch(engine,requests,effects=effects,
            gain_permissions=permission,require_all_selections=False)
        self.assertIsNotNone(pending.pending)
        self.assertEqual(('allowed-choice',),tuple(e.event_id for e in pending.events))
        complete = prepare_life_change_batch(engine,requests,effects=effects,
            gain_permissions=permission,selections=('mixed-add',))
        self.assertEqual({'denied-choice':0,'allowed-choice':8},{r.event_id:r.amount for r in complete.records})
        before = {p:engine.state.players[p].life for p in 'AB'}
        commit_life_change_batch(engine,complete)
        self.assertEqual((before['A']+8,before['B']),(engine.state.players['A'].life,engine.state.players['B'].life))

    def test_no_prohibition_keeps_original_event_order_and_replacement_journal(self):
        engine = self.session(119_700_003).engine
        requests = (LifeChangeRequest('gain','B',3),LifeChangeRequest('loss','A',-2))
        effects = (replacement('add',AddAmount(field='amount',amount=1)),
            replacement('double',MultiplyAmount(field='amount',factor=2)))
        old = prepare_life_change_batch(engine,requests,effects=effects,selections=('add',))
        explicit = prepare_life_change_batch(engine,requests,effects=effects,selections=('add',),
            gain_permissions=ALLOW_ALL_LIFE_GAIN)
        self.assertEqual(old,explicit)
        self.assertEqual(('gain','loss'),tuple(e.event_id for e in old.events))
        self.assertEqual((8,2),tuple(r.amount for r in old.records))

    def test_exact_static_frames_and_current_scope_are_required_by_the_descriptor(self):
        from quorune.compiler.life_templates import static_life_gain_prohibition_handler
        from quorune.life_gain_permission import (decode_life_gain_prohibition,life_gain_prohibition_payload_dependencies,
            LIFE_GAIN_PROHIBITION_CAPABILITY)
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        for text,relation in (("Players can't gain life.",'any'),("Your opponents can't gain life.",'opponent')):
            result = static_life_gain_prohibition_handler(text)
            self.assertIsNotNone(result)
            self.assertEqual(relation,decode_life_gain_prohibition(result[1]).affected_player_relation)
            self.assertEqual((LIFE_GAIN_PROHIBITION_CAPABILITY,),life_gain_prohibition_payload_dependencies(result[1],coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,)))
            with self.assertRaises(ValueError):life_gain_prohibition_payload_dependencies(result[1])
            for change in ({'schema_version':True},{'event':'life.change'},{'future':1}):
                with self.assertRaises(ValueError):decode_life_gain_prohibition({**result[1],**change})
        for text in ("You can't gain life.","Players can't gain life this turn.",
                "If you control a creature, your opponents can't gain life.",
                "Your opponents can't gain life. Draw a card."):
            self.assertIsNone(static_life_gain_prohibition_handler(text))

    def test_lifelink_permission_is_applied_before_damage_result_replacement(self):
        from test_damage_result_events import DamageResultEventTests
        from quorune.damage import prepare_damage_batch
        from quorune.damage_results import prepare_damage_results,plan_damage_result_commit,commit_damage_result_plan
        engine = self.session(119_700_004).engine
        source = DamageResultEventTests.token(self,engine,'A','Permission lifelink witness',keywords=('Lifelink',))
        proposal = DamageResultEventTests.proposal(engine,source,'B',2,event_id='permission-lifelink')
        damage = prepare_damage_batch(engine,(proposal,))
        before = {seat:engine.state.players[seat].life for seat in 'AB'}
        result = prepare_damage_results(engine,damage.events,effects=(),gain_permissions=LifeGainPermissionSnapshot(('A',)))
        self.assertFalse(any(child.kind == 'life.change' and child.payload['direction'] == 'gain'
            for root in result.events for child in root.children))
        commit_damage_result_plan(engine,plan_damage_result_commit(engine,result))
        self.assertEqual((before['A'],before['B']-2),(engine.state.players['A'].life,engine.state.players['B'].life))

    def test_fresh_life_and_damage_gate_omissions_are_detected(self):
        method = 'test_prohibited_gain_precedes_replacement_choice_and_commits_no_life_change'
        with patch('quorune.life_change._permission_events',side_effect=lambda events,permissions:(tuple(events),())):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune import damage_results
        original = damage_results._append_accumulated_player_results
        method = 'test_lifelink_permission_is_applied_before_damage_result_replacement'
        with patch('quorune.damage_results._append_accumulated_player_results',side_effect=lambda state,permissions:original(state,ALLOW_ALL_LIFE_GAIN)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


NAMES = ('Giant Cindermaw',"Knight of Dusk's Shadow",'Erebos, God of the Dead','Tibalt, Rakish Instigator')


class LifeGainProhibitionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LandSearchViewEquivalenceTests.session
    add = native.LandSearchViewEquivalenceTests.add
    prepare = native.LandSearchViewEquivalenceTests.prepare
    checkpoint = native.LandSearchViewEquivalenceTests.checkpoint
    resolve = native.LandSearchViewEquivalenceTests.resolve
    replay = native.LandSearchViewEquivalenceTests.replay
    private_replay = native.LandSearchViewEquivalenceTests.private_replay
    priority = native.LandSearchViewEquivalenceTests.priority
    actions = native.LandSearchViewEquivalenceTests.actions
    cast = native.LandSearchViewEquivalenceTests.cast
    reject = native.LandSearchViewEquivalenceTests.reject
    binding = native.LandSearchViewEquivalenceTests.binding
    activation_command = native.LandSearchViewEquivalenceTests.activation_command
    activate = native.LandSearchViewEquivalenceTests.activate
    resume = native.LandSearchViewEquivalenceTests.resume
    advance = journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'life-prohibitions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/life-gain-prohibition-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Static life-gain prohibitions',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def settle(self,s,*,target=None):
        for _ in range(100):
            d = s.state.pending_decision
            if d is None:s.engine.pump();continue
            principal = s.pending_principals()[0]
            seat = principal.split(':')[-1]
            packet = s.packet(principal,full=True)
            if d.kind == 'priority':
                if not s.state.stack:return None
                command = {'action_id':'pass'}
            elif d.kind == 'trigger.order':
                command = {'action_id':'order','triggers':[r['id'] for r in packet['decision']['ctx']['triggers']]}
            elif d.kind == 'replacement.order':
                options = packet['decision']['ctx']['options']
                self.assertTrue(options)
                command = {'action_id':'choose','replacement':options[0]['id']}
                self.reject(s,command,seat='D' if seat != 'D' else 'C')
            elif d.kind == 'semantic.target' and target is not None:
                command = {'action_id':'choose','targets':[target]}
                self.reject(s,command,seat='D' if seat != 'D' else 'C')
            else:return d
            result = s.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('Life-gain native stack did not settle')

    def heal(self,s,spell,*,seat='A',gain=0,payment=None):
        before = s.state.players[seat].life
        hand = set(s.state.players[seat].zones['hand'])
        self.cast(s,spell,seat=seat,payment={'C':1,'W':1} if payment is None else payment)
        self.assertIsNone(self.settle(s))
        self.assertEqual(before+gain,s.state.players[seat].life)
        drawn = set(s.state.players[seat].zones['hand'])-hand
        self.assertEqual(1,len(drawn),'A prohibited gain must not skip the draw instruction')
        ref = s.state.cards[next(iter(drawn))].ref
        for other in 'ABCD':
            if other != seat:self.assertNotIn(ref,json.dumps(s.packet('pilot:'+other,full=True)))
        self.assertIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))

    def test_heron_and_boon_offer_both_replacement_orders_and_replay_privately(self):
        for index, (label, expected) in enumerate((('add to life gained', 8), ('multiply life gained', 7))):
            with self.subTest(first=label):
                session = self.session(119710907 + index)
                heron = self.add(session.engine, 'Heron of Hope', zone='hand')
                self.add(session.engine, 'Boon Reflection')
                gain = self.add(session.engine, 'Revitalize', zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.assertTrue(self.binding(session, heron)['strict_capability_ready'])
                self.prepare(session, mana={'C': 4, 'W': 2})
                self.checkpoint(session)
                self.cast(session, heron, payment={'C': 3, 'W': 1})
                self.assertIsNone(self.settle(session))
                self.cast(session, gain, payment={'C': 1, 'W': 1})
                decision = self.resolve(session)
                self.assertEqual('replacement.order', decision.kind)
                self.assertEqual(['pilot:A'], session.pending_principals())
                self.assertEqual(40, session.state.players['A'].life)
                options = session.packet('pilot:A', full=True)['decision']['ctx']['options']
                self.assertEqual(2, len(options))
                option = next(row for row in options if label in row['label'])
                command = {'action_id': 'choose', 'replacement': option['id']}
                self.reject(session, command, seat='B')
                self.assertNotIn(option['id'], json.dumps(session.packet('pilot:B', full=True)))
                session = self.replay(session, load=True)
                result = session.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.settle(session))
                self.assertEqual([40 + expected, 40, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])
                self.private_replay(session, private)

    def test_life_gain_addition_blocks_when_replacement_order_authority_is_withdrawn(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests

        path = ROOT / 'quorune/rules/capability-registry.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        owner = 'damage.result.replacement_order'
        next(row for row in raw['capabilities'] if row['id'] == owner).update(
            status='blocked', blockers=['withdrawn life replacement order'], dependency_fail_closed_status='not_run',
        )
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = self.db.lookup('Heron of Hope')
        self.assertEqual('exact', compile_oracle_card(card, capability_registry=self.registry).status)
        ir = compile_oracle_card(card, capability_registry=registry)
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any(owner in blocker for residual in ir.material_residuals for blocker in residual.blockers))

    def test_originals_normally_bind_complete_sources_and_current_permission_scopes(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.life_gain_permission import LIFE_GAIN_PROHIBITION_CAPABILITY as CAP,LIFE_GAIN_PROHIBITION_EVENT
        for i,name in enumerate(NAMES):
            s = self.session(119710000+i);source = self.add(s.engine,name,zone='hand')
            binding = self.binding(s,source)
            self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
            programs = s.engine.semantics.programs_for_oracle(source.oracle_id)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in programs))
            p = next(p for p in programs if p.event == LIFE_GAIN_PROHIBITION_EVENT)
            deps = tuple(c for c in p.capability_dependencies if c != CAP)
            missing = replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_real_knight_control_and_departure_change_gain_scope_without_skipping_draws(self):
        s = self.session(119710010);source = self.add(s.engine,NAMES[1],zone='hand')
        own = [self.add(s.engine,'Revitalize',zone='hand',ref='knight-own-heal-'+str(i)) for i in range(3)]
        other = [self.add(s.engine,'Revitalize',seat='B',zone='hand',ref='knight-foreign-heal-'+str(i)) for i in range(2)]
        own_listener = self.add(s.engine,'Bloodthirsty Aerialist',ref='knight-own-listener')
        other_listener = self.add(s.engine,'Bloodthirsty Aerialist',seat='B',ref='knight-other-listener')
        steal = self.add(s.engine,'Control Magic',seat='B',zone='hand')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        bounce = self.add(s.engine,'Unsummon',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':4,'U':2,'W':2})
        self.prepare(s,mana={'C':5,'B':2,'W':3,'U':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':1,'B':1});self.assertIsNone(self.settle(s))
        self.assertIn('menace',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.activate(s,source);self.assertIsNone(self.settle(s))
        self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'))
        self.heal(s,own[0],gain=3);self.heal(s,other[0],seat='B',gain=0)
        self.assertEqual(0,s.state.cards[other_listener.object_id].counters.get('+1/+1',0))
        self.cast(s,steal,[source.ref],seat='B',payment={'C':2,'U':2});self.assertIsNone(self.settle(s))
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        s = self.resume(s)
        self.heal(s,own[1],gain=0);self.heal(s,other[1],seat='B',gain=3)
        self.cast(s,bounce,[source.ref],payment={'U':1});self.assertIsNone(self.settle(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.heal(s,own[2],gain=3)
        self.assertEqual((46,43),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertEqual((2,1),(s.state.cards[own_listener.object_id].counters.get('+1/+1',0),s.state.cards[other_listener.object_id].counters.get('+1/+1',0)))
        self.private_replay(s,private)

    def test_real_global_source_blocks_doublers_and_lifelink_then_humble_revokes_it(self):
        s = self.session(119710020);source = self.add(s.engine,NAMES[0],zone='hand')
        heals = [self.add(s.engine,'Revitalize',zone='hand',ref='global-own-heal-'+str(i)) for i in range(2)]
        other_heal = self.add(s.engine,'Revitalize',seat='B',zone='hand')
        hawk = self.add(s.engine,"Healer's Hawk");hawk.acquired_control_turn_count = -1
        for i in range(2):self.add(s.engine,'Boon Reflection',ref='global-double-'+str(i))
        listener = self.add(s.engine,'Bloodthirsty Aerialist')
        humble = self.add(s.engine,'Humble',zone='hand')
        lands = [self.add(s.engine,'Plains',ref='global-white-land-'+str(i)) for i in range(4)]
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':1,'W':1})
        self.prepare(s,mana={'C':3,'R':1,'W':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1});self.assertIsNone(self.settle(s))
        self.assertIn('trample',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.heal(s,heals[0],gain=0);self.heal(s,other_heal,seat='B',gain=0)
        self.assertEqual(0,s.state.cards[listener.object_id].counters.get('+1/+1',0))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.attackers')
        result = s.act('pilot:A',{'action_id':'attack','atk':{hawk.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.phase == 'postcombat_main')
        self.assertEqual((40,39),(s.state.players['A'].life,s.state.players['B'].life))
        for land in lands[:2]:self.activate(s,land)
        self.cast(s,humble,[source.ref],payment={'W':2});self.assertIsNone(self.settle(s))
        self.assertNotIn('trample',s.engine._combat_keywords(s.state.cards[source.object_id]))
        for land in lands[2:]:self.activate(s,land)
        self.cast(s,heals[1],payment={'W':2})
        decision = self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        s = self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(52,s.state.players['A'].life)
        self.assertEqual(1,s.state.cards[listener.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_real_erebos_noncreature_permission_life_price_and_live_devotion(self):
        s = self.session(119710030);source = self.add(s.engine,NAMES[2],zone='hand')
        bodies = [self.add(s.engine,'Walking Corpse',zone='hand',ref='erebos-devotion-'+str(i)) for i in range(4)]
        heals = [self.add(s.engine,'Revitalize',seat='B',zone='hand',ref='erebos-other-heal-'+str(i)) for i in range(2)]
        bounce = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'W':2,'U':1})
        self.prepare(s,mana={'C':8,'B':6});self.checkpoint(s)
        self.cast(s,source,payment={'C':3,'B':1});self.assertIsNone(self.settle(s))
        self.assertNotIn('Creature',s.engine._effective_card_data(source)['type_line'])
        self.assertIn('indestructible',s.engine._combat_keywords(s.state.cards[source.object_id]))
        before = set(s.state.players['A'].zones['hand'])
        self.activate(s,source);self.assertIsNone(self.settle(s))
        self.assertEqual(38,s.state.players['A'].life)
        drawn = set(s.state.players['A'].zones['hand'])-before;self.assertEqual(1,len(drawn))
        ref = s.state.cards[next(iter(drawn))].ref
        for seat in 'BCD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.heal(s,heals[0],seat='B',gain=0)
        for body in bodies:self.cast(s,body,payment={'C':1,'B':1});self.assertIsNone(self.settle(s))
        self.assertIn('Creature',s.engine._effective_card_data(source)['type_line'])
        self.assertEqual((5,7),(s.engine._numeric_stat(source.object_id,'power'),s.engine._numeric_stat(source.object_id,'toughness')))
        self.cast(s,bounce,[bodies[0].ref],seat='B',payment={'U':1});self.assertIsNone(self.settle(s))
        self.assertNotIn('Creature',s.engine._effective_card_data(source)['type_line'])
        s = self.resume(s);self.heal(s,heals[1],seat='B',gain=0)
        self.assertEqual(40,s.state.players['B'].life)
        self.private_replay(s,private)

    def test_real_global_source_blocks_canonical_prevention_aftermath(self):
        from quorune.damage_modifier_state import (DamageModifierDuration,DamagePreventionShield,
            DamageSubject,GainLifePreventionAftermath,PreventionMode)
        s = self.session(119710035);source = self.add(s.engine,NAMES[0],zone='hand')
        attacker = self.add(s.engine,'Grizzly Bears');attacker.acquired_control_turn_count = -1
        listener = self.add(s.engine,'Bloodthirsty Aerialist',seat='B')
        for i in range(2):self.add(s.engine,'Boon Reflection',seat='B',ref='aftermath-double-'+str(i))
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        # A valid pre-existing typed prevention effect is part of the saved
        # starting position; this does not claim an Awe Strike compiler path.
        s.state.damage_prevention_shields.append(DamagePreventionShield(
            shield_id='life-prohibition-aftermath',source_id='fixture:life-prevention',controller='B',
            subject=DamageSubject(ref='B',kind='player',controller='B'),mode=PreventionMode.AMOUNT,
            remaining=2,duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=s.state.turn_sequence,aftermath=(GainLifePreventionAftermath(player='B',per_prevented=1),)))
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1});self.assertIsNone(self.settle(s))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.attackers')
        result = s.act('pilot:A',{'action_id':'attack','atk':{attacker.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.blockers')
        result = s.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.phase == 'postcombat_main')
        self.assertEqual((40,40),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertEqual(0,s.state.cards[listener.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_real_tibalt_loyalty_devil_death_and_source_removal(self):
        s = self.session(119710040);source = self.add(s.engine,NAMES[3],zone='hand')
        heals = [self.add(s.engine,'Revitalize',seat='B',zone='hand',ref='tibalt-other-heal-'+str(i)) for i in range(2)]
        shock = self.add(s.engine,'Shock',zone='hand');bolt = self.add(s.engine,'Lightning Bolt',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'W':2})
        self.prepare(s,mana={'C':2,'R':3});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1});self.assertIsNone(self.settle(s))
        self.assertEqual(5,s.state.cards[source.object_id].counters['loyalty'])
        self.activate(s,source)
        self.assertEqual(3,s.state.cards[source.object_id].counters['loyalty'])
        self.assertIsNone(self.settle(s))
        token = next(c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.printed_name == 'Devil Token')
        self.assertEqual((1,1),(s.engine._numeric_stat(token.object_id,'power'),s.engine._numeric_stat(token.object_id,'toughness')))
        self.heal(s,heals[0],seat='B',gain=0)
        self.cast(s,shock,[token.ref],payment={'R':1});self.assertIsNone(self.settle(s,target='C'))
        self.assertEqual(39,s.state.players['C'].life)
        self.cast(s,bolt,[source.ref],payment={'R':1});self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s = self.resume(s);self.heal(s,heals[1],seat='B',gain=3)
        self.private_replay(s,private)

    def test_verified_capability_withdrawal_is_a_specific_rules_block(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.life_gain_permission import LIFE_GAIN_PROHIBITION_CAPABILITY as CAP
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed = deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id'] == CAP).update(status='blocked',blockers=['withdrawn life-gain permission'],dependency_fail_closed_status='not_run')
        registry = CapabilityRegistry(changed)
        index = build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_fresh_current_ability_omission_is_detected(self):
        method = 'test_real_global_source_blocks_doublers_and_lifelink_then_humble_revokes_it'
        with patch('quorune.semantic_runtime.life_prohibitions.program_has_current_ability_fragments',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


class FirstLifeGainCompilerTests(unittest.TestCase):
    def test_first_life_gain_has_closed_timing_and_runtime_authority(self):
        from types import SimpleNamespace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.life_change import FIRST_LIFE_GAIN_CAPABILITY, FIRST_LIFE_GAIN_EVENT
        from quorune.oracle_ir import compile_oracle_card, generated_programs
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        options = dict(capability_registry=registry, capability_profile='commander_review')
        record = query_record('Whenever you gain life for the first time during each of your turns, draw a card.')
        ir = compile_oracle_card(record, **options)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(FIRST_LIFE_GAIN_EVENT, node.event)
        self.assertEqual({'all': [{'field': 'player', 'op': 'eq', 'value': '$source.controller'},
            {'field': 'active_player', 'op': 'eq', 'value': '$source.controller'}]}, node.event_condition)
        self.assertEqual((0, len(record.oracle_text)), (node.span.start, node.span.end))
        program, = generated_programs(SimpleNamespace(rulings=lambda _record: ()), record, trust_level='trusted', **options)
        bound = bind_semantic_program_runtime(program, capability_registry=registry, profile='commander_review')
        self.assertTrue(bound['strict'], bound['blockers'])
        deps = tuple(cap for cap in program.capability_dependencies if cap != FIRST_LIFE_GAIN_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=deps,
            capability_closure=registry.closure(deps, profile='commander_review').to_dict())
        result = bind_semantic_program_runtime(withdrawn, capability_registry=registry, profile='commander_review')
        self.assertFalse(result['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + FIRST_LIFE_GAIN_CAPABILITY, result['blockers'])
        second = query_record(record.oracle_text.replace('first time', 'second time'))
        self.assertNotEqual('exact', compile_oracle_card(second, **options).status)

    def test_first_life_gain_history_and_manifest_keep_legacy_absence(self):
        from types import SimpleNamespace
        from quorune.model import TurnHistory, TurnHistoryEvent
        from quorune.turn_history import record_first_life_gain, roll_turn_history

        old = TurnHistory(turn_sequence=2)
        wire = old.to_dict()
        self.assertEqual(wire, TurnHistory.from_dict(wire).to_dict())
        self.assertNotIn('first_life_gain_history_version', wire)
        self.assertFalse(record_first_life_gain(SimpleNamespace(state=SimpleNamespace(turn_history=old)), 'A'))
        fact = TurnHistoryEvent('first_life_gain', target='A', target_kind='player')
        current = TurnHistory(turn_sequence=2, first_life_gain_history_version=1, events=[fact])
        self.assertEqual(current, TurnHistory.from_dict(current.to_dict()))
        next_turn = roll_turn_history(current, next_turn_sequence=3, previous_active_player='A')
        self.assertEqual(1, next_turn.first_life_gain_history_version)
        self.assertEqual([], next_turn.events)
        with self.assertRaises(ValueError):
            TurnHistory(first_life_gain_history_version=True)
        with self.assertRaises(ValueError):
            TurnHistory(first_life_gain_history_version=1, events=[fact, fact])
        with self.assertRaises(ValueError):
            TurnHistory(events=[fact])
        with self.assertRaises(ValueError):
            TurnHistoryEvent('first_life_gain', target='A', target_kind='player', amount=1)


class FirstLifeGainNativeTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'first-life-gain.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/life-gain-prohibition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('First life gain', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    def first_gainers(self, session):
        return [event.target for event in session.state.turn_history.events if event.kind == 'first_life_gain']

    def cats(self, session):
        return [card for card in session.state.cards.values()
            if card.zone == 'battlefield' and card.is_token and card.controller == 'A'
            and 'cat' in session.engine._type_parts(session.engine._effective_card_data(card)['type_line'])[1]]

    def lifelink_pinger(self, session):
        pinger = self.add(session.engine, 'Prodigal Sorcerer')
        collar = self.add(session.engine, 'Basilisk Collar')
        pinger.acquired_control_turn_count = -1
        attach_objects(session.state.cards, collar, pinger, source_timestamp=collar.zone_timestamp,
            players=session.state.players)
        self.assert_closed(session, pinger)
        self.assert_closed(session, collar)
        return pinger

    def ping(self, session, pinger, target):
        self.priority(session, 'A')
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offer = next(row for row in actions if row['id'].startswith('activate:' + pinger.ref + ':'))
        result = session.act('pilot:A', {'action_id': offer['id'], 'targets': [target], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)

    def test_cat_collector_uses_the_gaining_player_and_current_turn(self):
        for seed, turn in enumerate('AB', 827101):
            with self.subTest(turn=turn):
                session = self.session(seed)
                source = self.add(session.engine, 'Cat Collector')
                gain = self.add(session.engine, "Heroes' Reunion", seat='B', zone='hand')
                session.state.players['B'].mana_pool.update({'W': 1, 'G': 1})
                self.assert_closed(session, source)
                self.prepare(session, active=turn)
                self.checkpoint(session)
                self.cast(session, 'B', gain, ['A'])
                self.assertIsNone(self.resolve(session))
                self.assertEqual((47, 40), tuple(session.state.players[seat].life for seat in 'AB'))
                self.assertEqual(int(turn == 'A'), len(self.cats(session)), 'Only the first gain on your turn creates a Cat')
                self.assertEqual(['A'], self.first_gainers(session))
                self.replay(session)

    def test_first_gain_dispatch_omission_is_detected(self):
        with patch('quorune.player_result_events.record_first_life_gain', return_value=False):
            with self.assertRaisesRegex(AssertionError, 'Only the first gain on your turn creates a Cat'):
                self._assert_first_gain_creates_cat()

    def _assert_first_gain_creates_cat(self):
        session = self.session(827107)
        self.add(session.engine, 'Cat Collector')
        gain = self.add(session.engine, "Heroes' Reunion", zone='hand')
        self.prepare(session, mana={'G': 1, 'W': 1})
        self.cast(session, 'A', gain, ['A'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.cats(session)), 'Only the first gain on your turn creates a Cat')

    def test_vanguard_seraph_observes_net_zero_lifelink_and_keeps_surveil_private(self):
        from quorune.record_state_provenance import format_state_versions, validate_state_versions

        session = self.session(827103)
        source = self.add(session.engine, 'Vanguard Seraph')
        pinger = self.lifelink_pinger(session)
        top = self.add(session.engine, 'Grizzly Bears', zone='library', ref='private-first-gain-surveil')
        top.known_to = []
        top.revealed_to = []
        self.assert_closed(session, source)
        self.prepare(session)
        provenance = format_state_versions(session.state)
        self.assertEqual(1, provenance['first_life_gain_history_version'])
        validate_state_versions({'format': provenance}, session.state)
        with self.assertRaisesRegex(ValueError, 'Qualified history provenance'):
            validate_state_versions({'format': {**provenance, 'first_life_gain_history_version': 0}}, session.state)
        self.checkpoint(session)
        before_life = session.state.players['A'].life
        self.ping(session, pinger, 'A')
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(['A'], decision.actors)
        self.assertEqual(before_life, session.state.players['A'].life)
        self.assertEqual(['A'], self.first_gainers(session))
        life_facts = [
            (event.kind, event.amount)
            for event in session.state.turn_history.events
            if event.target == "A" and event.kind in {"player_gained_life", "player_lost_life"}
        ]
        self.assertCountEqual([("player_gained_life", 1), ("player_lost_life", 1)], life_facts)
        self.assertIn(top.ref, json.dumps(session.packet('pilot:A', full=True)))
        for seat in 'BCD':
            self.assertNotIn(top.ref, json.dumps(session.packet('pilot:' + seat, full=True)))
        response = {'action_id': 'choose', 'cards': {'top': [top.ref], 'graveyard': []}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', response).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', response)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.replay(session)

    def test_simultaneous_lifelink_keeps_regular_source_events_and_one_first_gain(self):
        session = self.session(827106)
        collector = self.add(session.engine, 'Cat Collector')
        listener = self.add(session.engine, 'Bloodthirsty Aerialist')
        hawks = [self.add(session.engine, "Healer's Hawk", ref=f'first-gain-hawk-{i}') for i in range(2)]
        for hawk in hawks:
            hawk.acquired_control_turn_count = -1
        self.assert_closed(session, collector)
        self.assert_closed(session, listener)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(hawk, 'B') for hawk in hawks])
        self.until(session, lambda game: game.state.phase == 'postcombat_main')
        self.assertEqual((42, 38), tuple(session.state.players[seat].life for seat in 'AB'))
        self.assertEqual(2, session.state.cards[listener.object_id].counters['+1/+1'])
        self.assertEqual(1, len(self.cats(session)))
        self.assertEqual(['A'], self.first_gainers(session))
        self.replay(session, load=True)

    def test_healer_and_cleric_share_first_gain_after_prohibition_and_replacement(self):
        session = self.session(827104)
        healer = self.add(session.engine, 'Attended Healer')
        cleric = self.add(session.engine, "Cleric of Life's Bond")
        late = self.add(session.engine, 'Attended Healer', zone='hand', ref='late-attended-healer')
        blocker = self.add(session.engine, 'Giant Cindermaw', seat='B')
        self.add(session.engine, 'Boon Reflection')
        remove = self.add(session.engine, 'Lightning Bolt', zone='hand')
        denied = self.add(session.engine, "Heroes' Reunion", zone='hand', ref='prevented-first-life')
        allowed = self.add(session.engine, "Heroes' Reunion", zone='hand', ref='actual-first-life')
        for source in (healer, cleric, blocker):
            self.assert_closed(session, source)
        self.prepare(session, mana={'C': 8, 'G': 4, 'W': 6, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', denied, ['A'])
        self.settle(session)
        self.assertEqual(40, session.state.players['A'].life)
        self.assertEqual([], self.first_gainers(session))
        self.cast(session, 'A', remove, [blocker.ref])
        self.settle(session)
        self.cast(session, 'A', allowed, ['A'])
        self.settle(session)
        self.assertEqual(54, session.state.players['A'].life)
        self.assertEqual(1, len(self.cats(session)))
        self.assertEqual(1, session.state.cards[cleric.object_id].counters['+1/+1'])
        self.cast(session, 'A', late)
        self.settle(session)
        self.assertEqual(56, session.state.players['A'].life)
        self.assertEqual(1, len(self.cats(session)))
        self.assertEqual(1, session.state.cards[cleric.object_id].counters['+1/+1'])
        self.assertEqual(['A'], self.first_gainers(session))
        self.replay(session, load=True)

    def test_deathless_knight_uses_the_graveyard_before_state_actions_and_the_next_turn(self):
        session = self.session(827105)
        knight = self.add(session.engine, 'Deathless Knight')
        pinger = self.lifelink_pinger(session)
        later = self.add(session.engine, "Heroes' Reunion", zone='hand', ref='late-knight-gain')
        next_turn = self.add(session.engine, "Heroes' Reunion", seat='B', zone='hand', ref='next-turn-knight-gain')
        self.add(session.engine, 'Forest', seat='B')
        self.add(session.engine, 'Plains', seat='B')
        self.assert_closed(session, knight)
        self.prepare(session, mana={'G': 1, 'W': 1})
        self.checkpoint(session)
        self.ping(session, pinger, knight.ref)
        self.settle(session)
        self.assertEqual('graveyard', session.state.cards[knight.object_id].zone)
        self.assertEqual(41, session.state.players['A'].life)
        self.assertEqual(['A'], self.first_gainers(session))
        session = self.replay(session, load=True)
        self.cast(session, 'A', later, ['A'])
        self.settle(session)
        self.assertEqual('graveyard', session.state.cards[knight.object_id].zone)
        self.finish_turn(session)
        self.cast(session, 'B', next_turn, ['A'])
        self.settle(session)
        self.assertEqual('hand', session.state.cards[knight.object_id].zone)
        self.assertEqual(['A'], self.first_gainers(session))
        self.replay(session, load=True)


if __name__ == '__main__':unittest.main()
