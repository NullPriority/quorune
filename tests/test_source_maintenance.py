from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.semantic_choices import (
    SemanticChoiceContext, SemanticChoiceContinuation, SemanticChoiceError,
    SemanticChoiceFrame, SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.payments import PAYMENT_CHOICE_HANDLERS
from quorune.source_maintenance import SourceMaintenanceSpec
from quorune.compiler.source_maintenance_nodes import source_maintenance_template
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_effect_payment_shapes import fixed_effect_payment_node_capabilities


def source_row(**changes):
    return replace(ObjectQueryResult(
        object_id='source', ref='SOURCE', printed_name='Maintenance witness',
        owner='B', controller='A', zone='battlefield', types=('creature',),
        logical_object_id='source:1',
    ), **changes)


def query_with(*rows, life=5):
    return SnapshotSemanticChoiceQuery(
        seat_order=('A', 'B', 'C', 'D'), active_order=('A', 'B', 'C', 'D'),
        object_rows=rows, life_by_seat={'A': life, 'B': 40, 'C': 40, 'D': 40},
    )


def context(query, **changes):
    return replace(SemanticChoiceContext(
        actor='A', stack_ref='STACK', stack_controller='A', stack_label='Maintenance',
        source_ref='SOURCE', card_ref=None, semantic_program_id='witness',
        semantic_program_version=1, query=query, source_logical_object_id='source:1',
    ), **changes)


def continuation(effect):
    return SemanticChoiceContinuation(
        handler_id='choice.payment.optional-fixed-effect.v1', handler_version=1,
        stack_ref='STACK', effect=effect, remaining=(), destination=None, note='Maintenance',
        semantic_frame=SemanticChoiceFrame(
            semantic_program_id='witness', semantic_program_version=1,
            stack_object='STACK', instruction_pointer=0, controller='A',
        ),
    )


def runtime_effect(payment=None, *, source='SOURCE'):
    return {**SourceMaintenanceSpec(payment).effect(), 'player': 'A', 'source': source}


class SourceMaintenanceChoiceTests(unittest.TestCase):
    # Independent contract: CR 118.3 and 118.12a require complete optional
    # payment or the printed default; CR 201.5/400.7 pin the source object;
    # CR 603.3a freezes the trigger controller, and CR 701.21a permits only
    # that player's currently controlled permanent to be sacrificed.
    def setUp(self):
        self.handler = next(h for h in PAYMENT_CHOICE_HANDLERS if h.operation == 'offer_optional_mana_payment')

    def test_decline_sacrifices_same_controlled_source_to_owner_graveyard(self):
        query = query_with(source_row())
        effect = runtime_effect(FixedEffectPaymentSpec('life', amount=2))
        prepared = self.handler.prepare(effect, context(query))
        complete = self.handler.complete(continuation(prepared.continuation_effect), {'pay': False}, query)
        self.assertEqual(1, len(complete.intents))
        intent = complete.intents[0]
        self.assertEqual(('SOURCE',), intent.object_refs)
        self.assertEqual('sacrifice', intent.transition_kind.value)
        self.assertTrue(intent.controlled_only)
        self.assertEqual('A', intent.actor)
        self.assertFalse(complete.prepend_effects)

    def test_source_departure_blink_phasing_and_control_do_not_erase_payment(self):
        payment = FixedEffectPaymentSpec('life', amount=2)
        variants = (
            (None, None), (source_row(logical_object_id='source:2'), None),
            (source_row(phased_out=True), None), (source_row(controller='B'), 'SOURCE'),
        )
        for row, bound in variants:
            with self.subTest(row=row):
                query = query_with(*(row,) if row else ())
                prepared = self.handler.prepare(runtime_effect(payment, source=bound), context(query))
                self.assertEqual((True, False), prepared.request.choice.legal_values)
                resumed = continuation(prepared.continuation_effect)
                decline = self.handler.complete(resumed, {'pay': False}, query)
                self.assertFalse(decline.intents)
                paid = self.handler.complete(resumed, {'pay': True}, query)
                self.assertEqual('PayLifeIntent', type(paid.intents[0]).__name__)
                self.assertEqual(2, paid.intents[0].amount)

    def test_source_is_revalidated_after_choice_without_changing_stack_payer(self):
        query = query_with(source_row())
        prepared = self.handler.prepare(runtime_effect(FixedEffectPaymentSpec('life', amount=2)), context(query))
        for row in (source_row(controller='B'), source_row(logical_object_id='source:2'), source_row(zone='graveyard')):
            with self.subTest(row=row):
                complete = self.handler.complete(continuation(prepared.continuation_effect), {'pay': False}, query_with(row))
                self.assertFalse(complete.intents)

    def test_mandatory_sacrifice_auto_continues_and_restores_identity(self):
        query = query_with(source_row())
        prepared = self.handler.prepare(runtime_effect(), context(query))
        self.assertIsNone(prepared.request)
        self.assertEqual('sacrifice', prepared.preparation_intents[0].transition_kind.value)
        restored = self.handler.prepare(prepared.continuation_effect, context(query))
        self.assertEqual(prepared, restored)
        with self.assertRaises(SemanticChoiceError):
            self.handler.prepare(prepared.continuation_effect, context(query, source_logical_object_id='source:2'))

    def test_two_card_payment_is_private_atomic_and_rejects_stale_objects(self):
        hand = tuple(ObjectQueryResult(
            object_id=ref, ref=ref, printed_name='Private card', owner='A',
            controller='A', zone='hand', logical_object_id=ref + ':1',
        ) for ref in ('FIRST', 'SECOND'))
        opponent = replace(hand[0], object_id='other', ref='OTHER', owner='B', controller='B')
        query = query_with(source_row(), *hand, opponent)
        payment = FixedEffectPaymentSpec('discard', amount=2, predicate=ObjectQuerySpec(zones=('hand',), owner='A', known_to_actor=True))
        prepared = self.handler.prepare(runtime_effect(payment), context(query))
        self.assertEqual(('FIRST', 'SECOND'), prepared.request.choice.legal_refs)
        self.assertEqual('actor_private', prepared.request.choice.visibility)
        self.assertEqual([0, 2], prepared.request.choice.choice_schema()['allowed_cardinalities'])
        resumed = continuation(prepared.continuation_effect)
        for cards in (['FIRST'], ['FIRST', 'FIRST'], ['FIRST', 'OTHER']):
            with self.subTest(cards=cards), self.assertRaises(SemanticChoiceError):
                self.handler.complete(resumed, {'cards': cards}, query)
        stale = query_with(source_row(), hand[0], replace(hand[1], logical_object_id='SECOND:2'))
        with self.assertRaisesRegex(SemanticChoiceError, 'stale'):
            self.handler.complete(resumed, {'cards': ['FIRST', 'SECOND']}, stale)
        paid = self.handler.complete(resumed, {'cards': ['FIRST', 'SECOND']}, query)
        self.assertEqual(('FIRST', 'SECOND'), paid.intents[0].object_refs)
        self.assertEqual('discard', paid.intents[0].transition_kind.value)
        self.assertFalse(paid.prepend_effects)
        decline = self.handler.complete(resumed, {'cards': []}, query)
        self.assertEqual(('SOURCE',), decline.intents[0].object_refs)

    def test_malformed_payment_and_wrong_stack_actor_fail_closed(self):
        query = query_with(source_row())
        effect = runtime_effect(FixedEffectPaymentSpec('life', amount=2))
        for mutant in ({**effect, 'effects': []}, {**effect, 'schema_version': True}, {**effect, 'player': 'B'}, {**effect, 'source': 'OTHER'}):
            with self.subTest(mutant=mutant), self.assertRaises(SemanticChoiceError):
                self.handler.prepare(mutant, context(query))
        with self.assertRaisesRegex(SemanticChoiceError, 'stack identity'):
            self.handler.prepare(effect, context(query, stack_controller='B'))
        prepared = self.handler.prepare(effect, context(query))
        with self.assertRaisesRegex(SemanticChoiceError, 'no longer payable'):
            self.handler.complete(continuation(prepared.continuation_effect), {'pay': True}, query_with(source_row(), life=1))
        for key, value in (('_source_logical_object_id', ''), ('_legal_objects', [{'ref': 'FIRST'}])):
            with self.subTest(key=key), self.assertRaises(SemanticChoiceError):
                self.handler.complete(continuation({**dict(prepared.continuation_effect), key: value}), {'pay': False}, query)


class SourceMaintenanceCompilerTests(unittest.TestCase):
    def test_entry_and_fixed_steps_preserve_source_spans_and_closed_costs(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        variants = (
            ('When this creature enters, sacrifice it unless you discard two cards.', 'permanent.enter.self', 'discard'),
            ('At the beginning of your upkeep, sacrifice this creature unless you pay {1}{G}.', 'step.begin', 'mana'),
            ('At the beginning of the end step, sacrifice this creature.', 'step.begin', None),
            ('At the beginning of your upkeep, sacrifice this enchantment unless you sacrifice a Pegasus.', 'step.begin', 'sacrifice'),
        )
        registry = load_default_capability_registry()
        for text, event, kind in variants:
            with self.subTest(text=text):
                ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
                node = ir.faces[0].nodes[0]
                self.assertEqual('fixed-source-maintenance-v1', node.template_id)
                self.assertEqual(event, node.event)
                self.assertEqual(text, node.text)
                self.assertEqual(text, ir.faces[0].oracle_text[node.span.start:node.span.end])
                payment = node.effects[0]['payment']
                self.assertEqual(kind, payment['kind'] if payment else None)
                required = fixed_effect_payment_node_capabilities(effects=node.effects, target_schema=None, mechanic_ids=node.mechanics)
                self.assertIn('trigger.source.fixed_maintenance', required)
                self.assertIn('effect.choice.optional_fixed_mana_payment', required)
                # The provisional capability must not silently acquire trust.
                self.assertEqual(registry.closure(('trigger.source.fixed_maintenance',), profile='commander_review').trusted, node.exact)

    def test_open_payments_other_sources_and_whole_card_siblings_remain_residual(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        exclusions = (
            'At the beginning of your upkeep, sacrifice this creature unless you pay {X}.',
            'At the beginning of your upkeep, sacrifice this creature unless you pay {1} or discard a card.',
            'At the beginning of your upkeep, sacrifice this creature unless you pay 2 life. When you do, draw a card.',
            'When another creature enters, sacrifice it unless you pay {1}.',
            'At the beginning of the end step, sacrifice another creature.',
        )
        for text in exclusions:
            with self.subTest(text=text):
                self.assertIsNone(source_maintenance_template(text, card_name='Maintenance witness'))
        ir = compile_oracle_card(payment_record('At the beginning of your upkeep, sacrifice this creature unless you pay {1}.\nWhenever a player sneezes, draw a card.'))
        self.assertTrue(ir.faces[0].nodes[0].lowerable)
        self.assertFalse(ir.faces[0].nodes[1].exact)
        self.assertNotEqual('exact', ir.status)

    def test_maintenance_shape_rejects_missing_source_wrong_payer_and_target(self):
        from quorune.fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
        from quorune.source_maintenance import SOURCE_MAINTENANCE_MECHANIC
        effect = SourceMaintenanceSpec().effect()
        mechanics = (FIXED_EFFECT_PAYMENT_MECHANIC, SOURCE_MAINTENANCE_MECHANIC)
        for mutant in ({**effect, 'source': None}, {**effect, 'player': '$active'}, {**effect, 'effects': []}, {**effect, 'schema_version': True}):
            with self.subTest(mutant=mutant):
                self.assertFalse(fixed_effect_payment_node_capabilities(effects=(mutant,), target_schema=None, mechanic_ids=mechanics))
        self.assertFalse(fixed_effect_payment_node_capabilities(effects=(effect,), target_schema={'kind': 'object'}, mechanic_ids=mechanics))

    def test_maintenance_dependency_and_source_guard_mutants_fail_closed(self):
        import json
        from common import ROOT
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        original = load_default_capability_registry()
        for dependency in ('trigger.source.fixed_maintenance', *original.capability('trigger.source.fixed_maintenance')['dependencies']):
            blocked = json.loads(json.dumps(raw))
            cap = next(row for row in blocked['capabilities'] if row['id'] == dependency)
            cap['status'] = 'blocked'; cap['blockers'] = ['Independent dependency mutation']
            registry = CapabilityRegistry(blocked)
            with self.subTest(dependency=dependency):
                ir = compile_oracle_card(payment_record('At the beginning of the end step, sacrifice this creature.'), capability_registry=registry)
                self.assertNotEqual('exact', ir.status)
        from quorune.semantic_choices import source_maintenance as owner
        from quorune.semantic_choices.model import SemanticChoiceCompletion
        case = SourceMaintenanceChoiceTests(); case.setUp()
        with mock.patch.object(owner, '_declined_source_sacrifice', return_value=SemanticChoiceCompletion()):
            with self.assertRaises(AssertionError):
                case.test_decline_sacrifices_same_controlled_source_to_owner_graveyard()
        original_decline = owner._declined_source_sacrifice
        def omit_control(effect, query):
            row = query.object(effect['source'], zones=('battlefield',)) if effect['source'] else None
            if row is None: return original_decline(effect, query)
            altered = query_with(*(replace(value, controller='A') if value.ref == row.ref else value for value in query.object_rows))
            return original_decline(effect, altered)
        with mock.patch.object(owner, '_declined_source_sacrifice', omit_control):
            with self.assertRaises(AssertionError):
                case.test_source_is_revalidated_after_choice_without_changing_stack_payer()
        def omit_incarnation(effect, query):
            row = query.object(effect['source'], zones=('battlefield',)) if effect['source'] else None
            return original_decline({**effect, '_source_logical_object_id': row.logical_object_id} if row else effect, query)
        with mock.patch.object(owner, '_declined_source_sacrifice', omit_incarnation):
            with self.assertRaises(AssertionError):
                case.test_source_is_revalidated_after_choice_without_changing_stack_payer()


class SourceMaintenanceActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'maintenance.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json',
            ROOT / 'tests/fixtures/scryfall-exact-lists.json',
            ROOT / 'tests/fixtures/source-maintenance-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Maintenance deck', [
            DeckEntry('Generic Payment Commander', 1, 'commander'),
            DeckEntry('Generic Payment Plains', 30),
        ], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        from copy import deepcopy
        from common import keep_all
        from quorune.model import GameConfig
        from quorune.session import CommanderSession
        from quorune.oracle_ir import register_generated_programs
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in 'ABCD'}, first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        register_generated_programs(self.db, engine.semantics, tuple(self.db.iter_cards()), trust_level='trusted', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='battlefield', seat='A'):
        from quorune.model import CardInstance
        record = self.db.lookup(name)
        card = CardInstance(object_id='maintenance:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=seat, controller=seat, zone=zone, zone_timestamp=engine._next_zone_timestamp(), known_to=[seat] if zone == 'hand' else list(engine.seats), revealed_to=[] if zone == 'hand' else list(engine.seats))
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self, session, source, mana):
        engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.active_player = 'A'; engine.state.started = True
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine._grant_priority('A'); engine.pump()
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(program) for program in programs))
        return session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']

    def checkpoint(self, session):
        from quorune.record import checkpoint_envelope
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear(); session.decisions.clear()

    def resolve(self, session):
        for _ in range(24):
            if session.state.pending_decision and session.state.pending_decision.kind != 'priority': return
            if not session.state.stack: return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Maintenance stack did not resolve')

    def replay(self, session):
        from quorune.record import authoritative_state_hash, replay_record
        from quorune.session import CommanderSession
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'maintenance-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {k: v for k, v in result.items() if k != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_trusted_rupture_spire_land_action_pays_or_sacrifices_and_replays(self):
        for pay in (True, False):
            with self.subTest(pay=pay):
                session = self.session(1181200 + int(pay)); engine = session.engine
                source = self.add(engine, 'Rupture Spire', zone='hand', ref='SPIRE')
                actions = self.ready(session, source, {'C': 1})
                action = next(row for row in actions if 'SPIRE' in row['id'])
                self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': action['id']})
                self.assertTrue(result.ok, result.summary)
                self.assertTrue(source.tapped)
                self.resolve(session)
                self.assertEqual(['A'], engine.state.pending_decision.actors)
                paid = session.act('pilot:A', {'action_id': 'choose', 'pay': pay})
                self.assertTrue(paid.ok, paid.summary)
                self.assertEqual('battlefield' if pay else 'graveyard', source.zone)
                self.assertEqual(0 if pay else 1, engine.state.players['A'].mana_pool['C'])
                self.replay(session)

    def test_trusted_avatar_cast_private_full_payment_rollback_pending_and_replay(self):
        from quorune.record import authoritative_state_hash
        from quorune.session import CommanderSession
        session = self.session(1181202); engine = session.engine
        source = self.add(engine, 'Avatar of Discord', zone='hand', ref='AVATAR')
        first = self.add(engine, 'Generic Payment Plains', zone='hand', ref='FIRST')
        second = self.add(engine, 'Generic Payment Plains', zone='hand', ref='SECOND')
        self.add(engine, 'Generic Payment Plains', zone='hand', seat='B', ref='OTHER')
        actions = self.ready(session, source, {'B': 3})
        cast = next(row for row in actions if row['id'] == 'cast:AVATAR')
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': cast['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        packet = session.packet('pilot:A', full=True)['decision']
        self.assertEqual([0, 2], packet['legal_actions'][0]['choice_schema']['allowed_cardinalities'])
        for seat in 'BCD': self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
        before = authoritative_state_hash(engine.state)
        for principal, cards in (('pilot:A', ['FIRST']), ('pilot:B', ['FIRST', 'SECOND']), ('pilot:A', ['FIRST', 'OTHER'])):
            bad = session.act(principal, {'action_id': 'choose', 'cards': cards})
            self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(engine.state))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-avatar'; session.save(path)
            session = CommanderSession.load(self.db, path)
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': [first.ref, second.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        for card in (first, second): self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        self.replay(session)

    def test_trusted_ball_lightning_every_players_end_step_and_blink_replays(self):
        from quorune.engine import TURN_STEPS
        for blink in (False, True):
            with self.subTest(blink=blink):
                session = self.session(1181203 + int(blink)); engine = session.engine
                source = self.add(engine, 'Ball Lightning', ref='BALL')
                self.add(engine, 'Cloudshift', zone='hand', ref='BLINK')
                engine.state.started = True; engine.state.active_player = 'B'
                engine.state.phase_index = TURN_STEPS.index(('postcombat_main', 'main'))
                engine.state.phase = 'postcombat_main'; engine.state.step = 'main'
                engine.state.players['B'].mana_pool.update({'W': 1})
                engine._grant_priority('B'); engine.pump(); self.checkpoint(session)
                for _ in range(4):
                    result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
                    self.assertTrue(result.ok, result.summary)
                self.assertEqual('end_step', engine.state.step)
                self.assertEqual(1, len(engine.state.stack))
                self.assertEqual('A', engine.state.stack[-1].controller)
                if blink:
                    result = session.act('pilot:B', {'action_id': 'pass'})
                    self.assertTrue(result.ok, result.summary)
                    engine.state.players['A'].mana_pool.update({'W': 1})
                    engine.permissions.invalidate_current(); engine.state.pending_decision = None
                    engine._grant_priority('A'); engine.pump(); self.checkpoint(session)
                    action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:BLINK')
                    old_identity = source.logical_object_id
                    result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'targets': [source.ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.resolve(session)
                    self.assertNotEqual(old_identity, source.logical_object_id)
                    self.assertEqual('battlefield', source.zone)
                else:
                    self.resolve(session); self.assertEqual('graveyard', source.zone)
                self.replay(session)

    def test_trusted_sacred_mesa_priority_activation_then_controlled_pegasus_payment(self):
        from quorune.engine import TURN_STEPS
        from quorune.record import authoritative_state_hash
        session = self.session(1181205); engine = session.engine
        source = self.add(engine, 'Sacred Mesa', ref='MESA')
        opponent = self.add(engine, 'Pegasus Courser', seat='B', ref='OTHER-PEGASUS')
        engine.state.started = True; engine.state.active_player = 'A'
        engine.state.players['A'].mana_pool.update({'W': 1, 'C': 1})
        engine.state.phase_index = TURN_STEPS.index(('beginning', 'upkeep'))
        engine._enter_step(); engine.pump()
        self.assertEqual(1, len(engine.state.stack))
        self.checkpoint(session)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:MESA:'))
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        token = next(card for card in engine.state.cards.values() if card.controller == 'A' and card.is_token and card.zone == 'battlefield')
        self.assertIn('Pegasus', token.printed_name)
        before = authoritative_state_hash(engine.state)
        for selected in ([source.ref], [opponent.ref]):
            result = session.act('pilot:A', {'action_id': 'choose', 'cards': selected})
            self.assertFalse(result.ok); self.assertEqual(before, authoritative_state_hash(engine.state))
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': [token.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertNotIn(token.object_id, session.state.players['A'].zones['battlefield'])
        self.replay(session)

    def test_trusted_avatar_payment_replacement_pending_resumes_once_and_replays(self):
        from quorune.session import CommanderSession
        session = self.session(1181206); engine = session.engine
        source = self.add(engine, 'Avatar of Discord', zone='hand', ref='REPLACED-AVATAR')
        cards = [self.add(engine, 'Generic Payment Plains', zone='hand', ref=ref) for ref in ('FIRST', 'SECOND')]
        self.add(engine, 'Generic Payment Destination Replacement', seat='B', ref='EXILE-ONE')
        self.add(engine, 'Generic Payment Competing Replacement', seat='C', ref='EXILE-TWO')
        action = next(row for row in self.ready(session, source, {'B': 3}) if row['id'] == 'cast:REPLACED-AVATAR')
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn('replacement', engine.state.pending_decision.kind)
        self.assertTrue(all(card.zone == 'hand' for card in cards))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-maintenance-replacement'; session.save(path)
            session = CommanderSession.load(self.db, path)
        for _ in range(8):
            decision = session.state.pending_decision
            if decision is None or 'replacement' not in decision.kind: break
            principal = session.pending_principals()[0]
            packet = session.packet(principal, full=True)['decision']
            result = session.act(principal, {'action_id': 'choose', 'replacement': packet['ctx']['options'][0]['id']})
            self.assertTrue(result.ok, result.summary)
        for card in cards:
            current = session.state.cards[card.object_id]
            self.assertEqual('exile', current.zone)
            self.assertEqual(1, sum(current.counters.values()))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_trusted_queued_mesa_retains_payer_after_source_control_changes(self):
        from quorune.engine import TURN_STEPS
        session = self.session(1181207); engine = session.engine
        source = self.add(engine, 'Sacred Mesa', ref='CONTROLLED-MESA')
        engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase_index = TURN_STEPS.index(('beginning', 'upkeep'))
        engine._enter_step(); engine.pump()
        self.assertEqual('A', engine.state.stack[-1].controller)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.change_control(source.object_id, 'B', reason='Independent trigger-time control witness')
        engine._grant_priority('A'); engine.pump(); self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(['A'], engine.state.pending_decision.actors)
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_trusted_granted_maintenance_uses_recipient_after_grant_source_leaves(self):
        from quorune.engine import TURN_STEPS
        session = self.session(1181208); engine = session.engine
        grant = self.add(engine, 'Energy Flux', seat='B', ref='FLUX')
        recipient = self.add(engine, 'Sol Ring', ref='RECIPIENT')
        engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase_index = TURN_STEPS.index(('beginning', 'upkeep'))
        engine._enter_step(); engine.pump()
        self.assertEqual(1, len(engine.state.stack))
        trigger = engine.state.stack[-1]
        self.assertEqual('A', trigger.controller)
        self.assertEqual(recipient.object_id, trigger.source_object_id)
        self.assertEqual(recipient.logical_object_id, trigger.context['source_logical_object_id'])
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.move_card(grant.object_id, 'graveyard', reason='Independent removal after trigger placement')
        engine._grant_priority('A'); engine.pump(); self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(['A'], engine.state.pending_decision.actors)
        result = session.act('pilot:A', {'action_id': 'choose', 'pay': False})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[recipient.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[grant.object_id].zone)
        self.replay(session)
