"""CR 113.6m/113.8/400.7: exact source returns with complete-card admission."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_self_return import (
    GRAVEYARD_SELF_ENTRY_CAPABILITY as CAPABILITY,
    GRAVEYARD_SELF_ENTRY_VERSION as MARKER,
    graveyard_self_return_dependencies,
    graveyard_self_return_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_ability_source_counter_targets as native
import test_ability_label_composition as journeys


class TriggeredGraveyardBattlefieldCompilerTests(unittest.TestCase):
    def test_unknown_sibling_prevents_materialization_through_normal_binding(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        registry = load_default_capability_registry()
        text = 'Whenever a land you control enters, return this card from your graveyard to the battlefield.'
        for sibling, ready in (('', True), ('\nThe remembered prophecy comes true.', False)):
            record = query_record(text + sibling)
            program = compile_best_available_card_program(SimpleNamespace(rulings=lambda record: ()), record,
                semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile='commander_review')
            bound = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
            self.assertEqual(ready, bound['strict_capability_ready'], bound['blockers'])
            if not ready:
                self.assertTrue(any('complete source admission' in b for b in bound['blockers']), bound['blockers'])

    def test_existing_heads_preserve_owner_conditions_payment_and_tapped_result(self):
        registry = load_default_capability_registry()
        forms = (
            ('Whenever a land you control enters', 'you may return', ''),
            ('When a Dragon enters', 'you may return', ''),
            ('Whenever you cast a spell from anywhere other than your hand', 'return', ' tapped'),
            ('Whenever you commit a crime', 'you may pay {B}. If you do, return', ' tapped'),
            ('At the beginning of your end step, if you gained 3 or more life this turn', 'return', ' tapped'),
            ('At the beginning of combat on your turn, if you control a creature with power 4 or greater',
             'you may pay {R}. If you do, return', ''),
        )
        for head, verb, tapped in forms:
            with self.subTest(head=head):
                text = head + ', ' + verb + ' this card from your graveyard to the battlefield' + tapped + '.'
                ir = compile_oracle_card(query_record(text), capability_registry=registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual('graveyard', node.active_zone)
                self.assertIn(CAPABILITY, node.capability_dependencies)
                self.assertIn(MARKER, str(node.effects))
                self.assertEqual('you may pay' in verb, 'offer_optional_mana_payment' in str(node.effects))

    def test_unknown_heads_death_lookback_and_open_result_riders_stay_residual(self):
        registry = load_default_capability_registry()
        texts = (
            'Whenever another creature is put into your graveyard from the battlefield, you may pay {B}. If you do, return this card from your graveyard to the battlefield.',
            'Whenever a Dragon you control dies while this card is in your graveyard, you may return this card from your graveyard to the battlefield.',
            'When enchanted creature dies, return this card from your graveyard to the battlefield.',
            'Whenever you draw your third card in a turn, return this card from your graveyard to the battlefield tapped.',
            'Whenever a land you control enters, return this card from your graveyard to the battlefield with a +1/+1 counter on it.',
            'Whenever a land you control enters, return this card from your graveyard to the battlefield attached to target creature.',
        )
        for text in texts:
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry,
                                         capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)

    def test_withdrawn_battlefield_authority_preserves_legacy_hand_and_ordinary_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id'] == CAPABILITY).update(
            status='blocked', blockers=['Battlefield source return authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for body, expected in (('you may return this card from your graveyard to the battlefield.', False),
                               ('you may return this card from your graveyard to your hand.', True),
                               ('draw a card.', True)):
            ir = compile_oracle_card(query_record('At the beginning of your upkeep, ' + body),
                capability_registry=registry, capability_profile='commander_review')
            self.assertEqual(expected, ir.status == 'exact', ir.material_residuals)


class TriggeredGraveyardBattlefieldModelTests(unittest.TestCase):
    def test_legacy_optional_return_requires_closed_branches_matching_owner_and_complete_source(self):
        from quorune.card_programs.trigger_result_equivalence import fixed_graveyard_self_entry_views_equal as equal
        outer = {'op': 'offer_optional_effect', 'player': '$controller', 'effects': [self.effect()]}
        outer['effects'][0]['tapped'] = False
        current = self.program(effects=[outer], target_schema=None, capability_dependencies=[CAPABILITY],
            event_condition={'field': 'controller', 'op': 'eq', 'value': '$source.owner'})
        previous = {'op': 'choose_option', 'player': '$controller', 'prompt': 'Return the source?',
            'options': [{'id': 'return', 'label': 'Return'}, {'id': 'decline', 'label': 'Leave'}],
            'then_by_choice': {'return': [{'op': 'move_if_in_zone', 'card': '$source',
                'from': 'graveyard', 'destination': 'battlefield', 'controller': '$controller'}], 'decline': []}}
        old = SimpleNamespace(active_zone='graveyard', event='land.enter', target_schema=None,
            effects=[previous], event_condition={'field': 'controller', 'op': 'eq', 'value': '$source.controller'})
        self.assertTrue(equal(current, old))
        for change in ({'card': '$target.0'}, {'from': 'hand'}, {'destination': 'hand'}, {'controller': 'B'}, {'future': 1}):
            bad = deepcopy(previous)
            bad['then_by_choice']['return'][0].update(change)
            self.assertFalse(equal(current, SimpleNamespace(**{**vars(old), 'effects': [bad]})))
        for change in ({'provenance': {}}, {'capability_dependencies': []}, {'target_schema': {'count': 1}},
                       {'event_condition': {'field': 'controller', 'op': 'eq', 'value': '$source.controller'}}):
            self.assertFalse(equal(SimpleNamespace(**{**vars(current), **change}), old))
        bad = deepcopy(previous)
        bad['then_by_choice']['decline'] = [{'op': 'draw', 'count': 1}]
        self.assertFalse(equal(current, SimpleNamespace(**{**vars(old), 'effects': [bad]})))

    def test_source_identity_and_tapped_entry_use_existing_movement_owner(self):
        from quorune.self_zone_move import SelfZoneMoveIntent, resolve_self_zone_move
        source = SimpleNamespace(object_id='source', ref='C1', logical_object_id='source@1',
                                 zone='graveyard', phased_out=False, object_kind='card')
        calls = []
        def move(object_id, destination, **kwargs):
            calls.append((object_id, destination, kwargs))
            source.zone = destination
        host = SimpleNamespace(state=SimpleNamespace(cards={'source': source}), move_card=move)
        intent = SelfZoneMoveIntent('A', 'T1', 'source', 'C1', 'source@0', 'graveyard', 'battlefield', True, 'card')
        self.assertIsNone(resolve_self_zone_move(host, intent))
        self.assertFalse(calls)
        self.assertEqual('C1', resolve_self_zone_move(host, replace(intent, logical_object_id='source@1')))
        self.assertEqual(('source', 'battlefield'), calls[0][:2])
        self.assertEqual(('A', True), (calls[0][2]['controller'], calls[0][2]['tapped']))
        self.assertIsNone(resolve_self_zone_move(host, replace(intent, logical_object_id='source@1')))
        self.assertEqual(1, len(calls))

    def effect(self):
        return {'op': 'self_zone_move', 'origin': 'graveyard', 'destination': 'battlefield',
                'tapped': True, 'source_form': 'card', MARKER: 1}

    def program(self, **changes):
        fields = dict(effects=(self.effect(),), active_zone='graveyard', event='land.enter',
            provenance={'requires_complete_card_program': True, 'card_program_admission': {
                'schema_version': 1, 'oracle_ir_status': 'exact', 'material_residual_count': 0}})
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_marker_is_closed_and_legacy_hand_keeps_its_original_encoding(self):
        from quorune.rules.graveyard_self_return import GRAVEYARD_SELF_RETURN_VERSION
        effect = self.effect()
        self.assertEqual((CAPABILITY,), graveyard_self_return_dependencies(effect))
        for change in ({MARKER: True}, {'destination': 'hand'}, {'origin': 'hand'}, {'tapped': 0},
                       {'source_form': 'aura'}, {'future': 1}, {'_aura_target_ref': ''},
                       {GRAVEYARD_SELF_RETURN_VERSION: 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                graveyard_self_return_dependencies({**effect, **change})
        self.assertEqual(('zone.return.triggered_self_graveyard_hand',),
            graveyard_self_return_dependencies({'op': 'self_zone_move', 'origin': 'graveyard',
                'destination': 'hand', 'tapped': False, 'source_form': 'card', GRAVEYARD_SELF_RETURN_VERSION: 1}))

    def test_payload_requires_whole_source_certificate_and_graveyard_event(self):
        self.assertEqual((CAPABILITY,), graveyard_self_return_runtime_dependencies(self.program()))
        for changes in ({'provenance': {}}, {'event': 'activate'}, {'event': 'permanent.graveyard'},
                        {'active_zone': 'battlefield'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                graveyard_self_return_runtime_dependencies(self.program(**changes))
        for certificate in ({'schema_version': 1, 'oracle_ir_status': 'partial', 'material_residual_count': 1},
                            {'schema_version': 1, 'oracle_ir_status': 'exact', 'material_residual_count': 1}):
            with self.assertRaises(ValueError):
                graveyard_self_return_runtime_dependencies(self.program(provenance={
                    'requires_complete_card_program': True, 'card_program_admission': certificate}))

    def test_omitting_complete_source_admission_is_detected(self):
        method = 'test_payload_requires_whole_source_certificate_and_graveyard_event'
        baseline = unittest.TestResult()
        type(self)(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(), baseline.errors + baseline.failures)
        with patch('quorune.card_programs.admission.program_has_declared_complete_source_admission', return_value=True):
            changed = unittest.TestResult()
            type(self)(method).run(changed)
        self.assertFalse(changed.errors, changed.errors)
        self.assertTrue(changed.failures)


class TriggeredGraveyardBattlefieldRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay
    advance = journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'triggered-graveyard-entry.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/triggered-graveyard-battlefield-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Triggered graveyard entry', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def ready(self, session, *cards):
        for card in cards:
            binding = self.binding(session, card)
            self.assertTrue(binding['strict_capability_ready'], (card.printed_name, binding['blockers']))

    def choose(self, session, *, accept=True):
        self.assertEqual('semantic.choice', session.state.pending_decision.kind)
        self.assertEqual(['A'], session.state.pending_decision.actors)
        command = {'action_id': 'choose', 'choice': 'apply' if accept else 'decline'}
        self.reject(session, command, seat='B')
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def land(self, session, card, *, seat='A'):
        self.priority(session, seat)
        offered = next((r for r in self.actions(session, seat)
                        if r.get('kind') == 'play_land' and r.get('card') == card.ref), None)
        self.assertIsNotNone(offered, self.actions(session, seat))
        command = {'action_id': offered['id']}
        self.reject(session, command, seat='D')
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_land_is_owner_relative_and_optional_return_preserves_haste_condition(self):
        for active, accept, low_life in (('B', True, False), ('A', False, False),
                                       ('A', True, False), ('A', True, True)):
            with self.subTest(active=active, accept=accept):
                session = self.session(981_001 + int(active == 'A') + int(low_life))
                source = self.add(session.engine, 'Bloodghast', zone='graveyard')
                land = self.add(session.engine, 'Forest', seat=active, zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                if low_life:
                    session.state.players['B'].life = 10
                self.ready(session, source, land)
                self.prepare(session, actor=active, active=active)
                self.checkpoint(session)
                self.land(session, land, seat=active)
                if active == 'A':
                    self.assertEqual('semantic.choice', self.settle(session).kind)
                    session = self.private_replay(session, private)
                    self.choose(session, accept=accept)
                self.assertIsNone(self.settle(session))
                current = session.state.cards[source.object_id]
                self.assertEqual('battlefield' if active == 'A' and accept else 'graveyard', current.zone)
                if current.zone == 'battlefield':
                    self.assertEqual('A', current.controller)
                    self.assertFalse(current.tapped)
                    self.assertEqual(low_life,
                        'haste' in str(session.engine._effective_card_data(current).get('keywords', [])).casefold())
                self.private_replay(session, private)

    def test_opponent_dragon_entry_returns_owner_thrall_without_granting_flying(self):
        session = self.session(981_010)
        source = self.add(session.engine, "Bladewing's Thrall", zone='graveyard')
        dragon = self.add(session.engine, 'Shivan Dragon', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.ready(session, source, dragon)
        self.prepare(session, actor='B', active='B', mana={'C': 4, 'R': 2})
        self.checkpoint(session)
        self.cast(session, dragon, seat='B')
        self.assertEqual('semantic.choice', self.settle(session).kind)
        session = self.private_replay(session, private)
        self.choose(session)
        self.assertIsNone(self.settle(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(('battlefield', 'A', False), (current.zone, current.controller, current.tapped))
        self.assertEqual(('battlefield', 'B'), (session.state.cards[dragon.object_id].zone, dragon.controller))
        self.assertNotIn('flying', str(session.engine._effective_card_data(current).get('keywords', [])).casefold())
        self.private_replay(session, private)

    def test_hand_cast_does_not_return_poxwalkers_but_actual_flashback_returns_tapped_before_draw(self):
        session = self.session(981_020)
        source = self.add(session.engine, 'Poxwalkers', zone='graveyard')
        spell = self.add(session.engine, 'Think Twice', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.ready(session, source, spell)
        self.prepare(session, mana={'C': 3, 'U': 2})
        self.checkpoint(session)
        self.cast(session, spell)
        self.assertIsNone(self.settle(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        hand_count = len(session.state.players['A'].zones['hand'])
        self.cast(session, spell)
        # Resolve the source trigger alone, while the triggering flashback spell remains.
        for _ in range(16):
            if session.state.cards[source.object_id].zone == 'battlefield':
                break
            decision = session.state.pending_decision
            self.assertEqual('priority', decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        current = session.state.cards[source.object_id]
        self.assertEqual(('battlefield', 'A', True), (current.zone, current.controller, current.tapped))
        self.assertEqual(hand_count, len(session.state.players['A'].zones['hand']))
        self.assertEqual('stack', session.state.cards[spell.object_id].zone)
        session = self.private_replay(session, private)
        self.assertIsNone(self.settle(session))
        self.assertEqual('exile', session.state.cards[spell.object_id].zone)
        self.assertEqual(hand_count + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_actual_paid_landfall_charges_once_and_decline_keeps_source(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                session = self.session(981_030 + int(pay))
                source = self.add(session.engine, 'Akoum Firebird', zone='graveyard')
                land = self.add(session.engine, 'Mountain', zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.ready(session, source, land)
                self.prepare(session, mana={'C': 4, 'R': 2})
                self.checkpoint(session)
                self.land(session, land)
                self.assertEqual('semantic.choice', self.settle(session).kind)
                session = self.private_replay(session, private)
                self.assertEqual(['A'], session.state.pending_decision.actors)
                command = {'action_id': 'choose', 'pay': pay}
                self.reject(session, command, seat='B')
                result = session.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.settle(session))
                current = session.state.cards[source.object_id]
                self.assertEqual('battlefield' if pay else 'graveyard', current.zone)
                self.assertEqual((0, 0) if pay else (4, 2),
                    (session.state.players['A'].mana_pool.get('C', 0), session.state.players['A'].mana_pool.get('R', 0)))
                if pay:
                    self.assertEqual(('A', False), (current.controller, current.tapped))
                self.private_replay(session, private)

    def test_original_end_step_requires_actual_life_gain_before_tapped_return(self):
        for gain in (False, True):
            with self.subTest(gain=gain):
                session = self.session(981_040 + int(gain))
                source = self.add(session.engine, 'Silversmote Ghoul', zone='graveyard')
                heal = self.add(session.engine, 'Revitalize', zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.ready(session, source, heal)
                self.prepare(session, mana={'C': 1, 'W': 1})
                self.checkpoint(session)
                if gain:
                    self.cast(session, heal)
                    self.assertIsNone(self.settle(session))
                    self.assertEqual(43, session.state.players['A'].life)
                self.advance(session, lambda s: s.state.step == 'end_step')
                self.assertIsNone(self.settle(session))
                current = session.state.cards[source.object_id]
                self.assertEqual('battlefield' if gain else 'graveyard', current.zone)
                if gain:
                    self.assertEqual(('A', True), (current.controller, current.tapped))
                self.private_replay(session, private)
