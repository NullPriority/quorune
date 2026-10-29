"""Pinned CR701.54 originals through normal binding, commands and records."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
import test_job_select as actions
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.ring_model import (
    RING_TEMPTATION_CAPABILITY, RING_BEARER_CAPABILITY, RING_STATIC_CAPABILITY,
    RING_ATTACK_CAPABILITY, RING_BLOCK_CAPABILITY, RING_DAMAGE_CAPABILITY,
)
from quorune.ring_state import current_ring_state, prepare_ring_temptation, commit_ring_bearer_choice
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class RingCompilerAdmissionTests(unittest.TestCase):
    def test_temptation_and_choice_observers_keep_distinct_occurrence_conditions(self):
        from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
        condition = {'field': 'player', 'op': 'eq', 'value': '$source.controller'}
        for phrase, chosen in (('the Ring tempts you', False),
                               ('you choose a creature as your Ring-bearer', True)):
            text = 'Whenever ' + phrase + ', draw a card.'
            spec = fixed_public_action_event_binding_spec(text)
            self.assertEqual('player.ring_tempted', spec.event)
            self.assertEqual((RING_TEMPTATION_CAPABILITY,), spec.capabilities)
            expected = {'all': [condition, {'field': 'creature_chosen', 'op': 'eq', 'value': True}]} if chosen else condition
            self.assertEqual(expected, spec.condition)
            record = replace(query_record(text), type_line='Enchantment', keywords=())
            ir = compile_oracle_card(record, capability_registry=load_default_capability_registry())
            self.assertEqual('exact', ir.status)
            triggered = [node for face in ir.faces for node in face.nodes if node.kind == 'triggered_ability']
            self.assertEqual(1, len(triggered))
            self.assertIn(RING_TEMPTATION_CAPABILITY, triggered[0].capability_dependencies)
        for phrase in ('a creature becomes your Ring-bearer', 'an opponent chooses a Ring-bearer',
                       'you choose another creature as your Ring-bearer'):
            self.assertIsNone(fixed_public_action_event_binding_spec('Whenever ' + phrase + ', draw a card.'))

    def test_ring_choice_flag_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import fixed_public_action_event_bindings as bindings
        original = bindings._public_ring_spec
        def mutated(text):
            spec = original(text)
            if spec is not None and spec.variant == 'public_ring_bearer_choice':
                return replace(spec, condition={'field': 'player', 'op': 'eq', 'value': '$source.controller'})
            return spec
        self.test_temptation_and_choice_observers_keep_distinct_occurrence_conditions()
        with patch.object(bindings, '_public_ring_spec', mutated), self.assertRaises(AssertionError):
            self.test_temptation_and_choice_observers_keep_distinct_occurrence_conditions()

    def test_ring_instruction_dependencies_and_unknown_modifiers_fail_closed(self):
        record = replace(query_record('The Ring tempts you.'), type_line='Sorcery', keywords=())
        registry = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        def verified_variant(value):
            changed_registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations,
                                discovered_test_ids=discovered)
            changed_registry.mark_evidence_verified(validate_capability_evidence_index(
                index, registry=changed_registry))
            return changed_registry
        for capability in (RING_TEMPTATION_CAPABILITY, RING_BEARER_CAPABILITY, RING_STATIC_CAPABILITY,
                           RING_ATTACK_CAPABILITY, RING_BLOCK_CAPABILITY, RING_DAMAGE_CAPABILITY,
                           'zone.draw.library_to_hand', 'zone.discard.typed_cause', 'life.change.effect',
                           'trigger.placement.apnap', 'trigger.event.normalized_damage',
                           'trigger.event.normalized_self_attack'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['omitted independent Ring authority'])
            with self.subTest(capability=capability):
                self.assertNotEqual('exact', compile_oracle_card(
                    record, capability_registry=verified_variant(changed)).status)
        for text in ('The Ring tempts you twice.', 'The Ring tempts target opponent.',
                     'The Ring tempts you unless an unknown condition holds.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(
                    replace(record, oracle_text=text), capability_registry=registry).status)
        unrelated = deepcopy(raw)
        next(row for row in unrelated['capabilities'] if row['id'] == 'variant.monarch.designate').update(
            status='blocked', blockers=['unrelated control'])
        self.assertEqual('exact', compile_oracle_card(
            record, capability_registry=verified_variant(unrelated)).status)

    def test_ring_shape_cannot_admit_targets_other_players_or_continuation_fields(self):
        from quorune.rules.ring_capability_shapes import ring_temptation_node_capabilities
        effect = {'op': 'tempt_with_the_ring', 'player': '$controller'}
        args = {'effects': (effect,), 'target_schema': None, 'mechanic_ids': {'the-ring-tempts-you'}}
        self.assertEqual((RING_TEMPTATION_CAPABILITY,), ring_temptation_node_capabilities(**args))
        for changed in ({'player': '$target.0'}, {'player': 'B'}, {'_ring_stage': 'choose'},
                        {'count': 2}, {'unknown': True}):
            self.assertEqual((), ring_temptation_node_capabilities(
                **{**args, 'effects': ({**effect, **changed},)}))
        self.assertEqual((), ring_temptation_node_capabilities(**{**args, 'target_schema': {}}))


class RingNativeActionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.JobSelectRuntimeTests.session
    add = actions.JobSelectRuntimeTests.add
    prepare = actions.JobSelectRuntimeTests.prepare
    cast = actions.JobSelectRuntimeTests.cast
    checkpoint = actions.JobSelectRuntimeTests.checkpoint
    resolve = actions.JobSelectRuntimeTests.resolve
    replay = actions.JobSelectRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'ring.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                                ROOT / 'tests/fixtures/ring-original-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Ring originals', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 24)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def assert_original_closed(self, name):
        card = self.db.lookup(name)
        program = compile_best_available_card_program(self.db, card,
            semantic_registry=SemanticRegistry(), capability_registry=self.registry,
            capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry,
                                            profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], name)

    def until(self, session, predicate):
        for _ in range(100):
            session.engine.pump()
            if predicate(session):
                return
            decision = session.state.pending_decision
            self.assertIsNotNone(decision)
            principal = session.pending_principals()[0]
            if decision.kind == 'priority':
                command = {'action_id': 'pass'}
            elif decision.kind == 'combat.blockers':
                command = {'action_id': 'block', 'blocks': {}}
            elif decision.kind == 'combat.attackers':
                command = {'action_id': 'attack', 'attackers': {}}
            elif decision.kind == 'trigger.order':
                group = next(group for group in session.state.pending_trigger_batches[0].groups
                             if group.controller == decision.actors[0])
                command = {'action_id': 'order', 'triggers': [item.ref for item in group.items]}
            else:
                self.fail('Unexpected native Ring decision: ' + decision.kind)
            result = session.act(principal, command)
            self.assertTrue(result.ok, result.summary)
        self.fail('Native Ring action did not reach the expected state')

    def birthday_fixture(self, seed, *, count=0, borrowed=False):
        self.assert_original_closed('Birthday Escape')
        session = self.session(seed)
        source = self.add(session.engine, 'Birthday Escape', zone='hand', ref='native-ring-spell')
        bearer = self.add(session.engine, 'Generic Bound Body', seat='B' if borrowed else 'A',
                          ref='native-ring-bearer')
        if borrowed:
            session.engine.change_control(bearer.object_id, 'A', reason='pre-action borrowed bearer fixture')
        for _ in range(count):
            plan = prepare_ring_temptation(session.engine, player='A', reason='pre-action Ring progress fixture')
            commit_ring_bearer_choice(session.engine, plan, bearer.ref)
        self.prepare(session, mana={'U': 4, 'B': 4, 'C': 20})
        return session, source, bearer

    def resolve_birthday(self, session, source, bearer):
        self.cast(session, 'A', source)
        decision = self.resolve(session)
        self.assertIsNotNone(decision, 'The mandatory Ring creature choice must be offered')
        self.assertEqual('semantic.choice', decision.kind)
        self.assertIn(bearer.ref, json.dumps(session.packet('pilot:A', full=True)['decision']))
        result = session.act('pilot:A', {'action_id': 'choose', 'objects': [bearer.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

    def test_birthday_escape_public_borrowed_choice_private_draw_and_pending_replay(self):
        session, source, bearer = self.birthday_fixture(701_054_101, borrowed=True)
        enemy = self.add(session.engine, 'Generic Bound Body', seat='C', ref='native-ring-enemy')
        self.checkpoint(session)
        before = {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        self.cast(session, 'A', source)
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        packet = json.dumps(session.packet('pilot:A', full=True)['decision'])
        self.assertIn(bearer.ref, packet)
        self.assertNotIn(enemy.ref, packet)
        self.assertEqual(before['A'], len(session.state.players['A'].zones['hand']))
        drawn_id = session.state.players['A'].zones['hand'][-1]
        drawn_ref = session.state.cards[drawn_id].ref
        for seat in 'BCD':
            self.assertEqual(before[seat], len(session.state.players[seat].zones['hand']))
            self.assertNotIn(drawn_ref, json.dumps(session.packet('pilot:' + seat, full=True)))
        session = self.replay(session, load=True)
        unchanged = authoritative_state_hash(session.state)
        response = {'action_id': 'choose', 'objects': [bearer.ref]}
        self.assertFalse(session.act('pilot:D', response).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        result = session.act('pilot:A', response)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        state = current_ring_state(session.engine, 'A')
        self.assertEqual(1, state.temptation_count)
        self.assertEqual(bearer.object_id, state.bearer.identity.object_id)
        self.assertEqual('B', session.state.cards[bearer.object_id].owner)
        self.assertIn('legendary', session.engine._type_parts(
            session.engine._effective_card_data(session.state.cards[bearer.object_id])['type_line'])[2])
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_birthday_escape_without_creatures_still_completes_one_temptation(self):
        self.assert_original_closed('Birthday Escape')
        session = self.session(701_054_102)
        source = self.add(session.engine, 'Birthday Escape', zone='hand', ref='native-ring-empty')
        self.prepare(session, mana={'U': 3, 'C': 3})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        state = current_ring_state(session.engine, 'A')
        self.assertEqual(1, state.temptation_count)
        self.assertIsNone(state.bearer)
        self.replay(session, load=True)

    def test_claim_the_precious_resolves_destruction_before_impossible_ring_choice(self):
        self.assert_original_closed('Claim the Precious')
        session = self.session(701_054_103)
        source = self.add(session.engine, 'Claim the Precious', zone='hand', ref='native-ring-claim')
        target = self.add(session.engine, 'Generic Bound Body', seat='B', ref='native-ring-destroyed')
        self.prepare(session, mana={'B': 4, 'C': 8})
        self.checkpoint(session)
        self.cast(session, 'A', source, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        state = current_ring_state(session.engine, 'A')
        self.assertEqual(1, state.temptation_count)
        self.assertIsNone(state.bearer)
        self.replay(session, load=True)

    def test_claim_all_targets_illegal_does_not_tempt(self):
        self.assert_original_closed('Claim the Precious')
        self.assert_original_closed('Unsummon')
        session = self.session(701_054_104)
        source = self.add(session.engine, 'Claim the Precious', zone='hand', ref='native-ring-fizzled')
        target = self.add(session.engine, 'Generic Bound Body', seat='B', ref='native-ring-escaped')
        response = self.add(session.engine, 'Unsummon', seat='B', zone='hand', ref='native-ring-response')
        self.prepare(session, mana={'B': 4, 'C': 8})
        session.state.players['B'].mana_pool.update({'U': 4})
        self.checkpoint(session)
        self.cast(session, 'A', source, [target.ref])
        self.cast(session, 'B', response, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertIsNone(current_ring_state(session.engine, 'A'))
        self.replay(session, load=True)

    def attack_and_complete_loot(self, session, bearer):
        self.until(session, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == 'combat.attackers')
        result = session.act('pilot:A', {'action_id': 'attack', 'attackers': {bearer.ref: 'B'}})
        self.assertTrue(result.ok, result.summary)
        before = len(session.state.players['A'].zones['hand'])
        decision = self.resolve(session)
        self.assertEqual('choice.apnap', decision.kind)
        self.assertEqual(['A'], decision.actors)
        self.assertEqual(before + 1, len(session.state.players['A'].zones['hand']))
        drawn_id = session.state.players['A'].zones['hand'][-1]
        drawn_ref = session.state.cards[drawn_id].ref
        for seat in 'BCD':
            packet = session.packet('pilot:' + seat, full=True)
            self.assertIsNone(packet['decision'])
            self.assertNotIn(drawn_ref, json.dumps(packet))
        session = self.replay(session, load=True)
        unchanged = authoritative_state_hash(session.state)
        response = {'action_id': 'choose', 'cards': [drawn_ref]}
        self.assertFalse(session.act('pilot:C', response).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        result = session.act('pilot:A', response)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        return session

    def test_native_attack_loot_and_combat_loss_keep_private_draw_and_all_opponents(self):
        session, source, bearer = self.birthday_fixture(701_054_105, count=3)
        self.checkpoint(session)
        self.resolve_birthday(session, source, bearer)
        self.assertEqual(4, current_ring_state(session.engine, 'A').temptation_count)
        life = {seat: session.state.players[seat].life for seat in 'ABCD'}
        session = self.attack_and_complete_loot(session, bearer)
        self.until(session, lambda current: current.state.players['C'].life == life['C'] - 3)
        self.assertEqual(life['A'], session.state.players['A'].life)
        self.assertEqual(life['B'] - 5, session.state.players['B'].life)
        self.assertEqual(life['C'] - 3, session.state.players['C'].life)
        self.assertEqual(life['D'] - 3, session.state.players['D'].life)
        self.replay(session, load=True)

    def test_native_blocker_delay_survives_pending_load_and_uses_current_controller(self):
        session, source, bearer = self.birthday_fixture(701_054_106, count=2)
        blocker = self.add(session.engine, 'Generic Bound Body', seat='C', ref='native-ring-blocker')
        session.engine.change_control(blocker.object_id, 'B', reason='pre-action borrowed blocker fixture')
        self.checkpoint(session)
        self.resolve_birthday(session, source, bearer)
        self.assertEqual(3, current_ring_state(session.engine, 'A').temptation_count)
        session = self.attack_and_complete_loot(session, bearer)
        self.until(session, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == 'combat.blockers'
                   and current.state.pending_decision.actors == ['B'])
        result = session.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: bearer.ref}})
        self.assertTrue(result.ok, result.summary)
        self.until(session, lambda current: bool(current.state.delayed_triggers))
        emblem = current_ring_state(session.engine, 'A').emblem
        waiting = session.state.delayed_triggers[-1]
        self.assertEqual(emblem.object_id, waiting.source_object_id)
        self.assertIsNone(waiting.expires_turn_sequence)
        session = self.replay(session, load=True)
        # Begin a separate owner-boundary record from this valid waiting state.
        # The preceding native cast, attack, block and delay record was verified.
        session.engine.change_control(blocker.object_id, 'D', reason='pending-delay controller boundary')
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.engine._grant_priority('A')
        session.engine.pump()
        self.checkpoint(session)
        self.until(session, lambda current: current.state.cards[blocker.object_id].zone == 'graveyard')
        self.assertIn(blocker.object_id, session.state.players['C'].zones['graveyard'])
        self.assertNotIn(blocker.object_id, session.state.players['D'].zones['battlefield'])
        self.replay(session, load=True)

    def test_call_observes_actual_rechoice_for_its_controller_and_optional_payment(self):
        self.assert_original_closed('Call of the Ring')
        session, birthday, bearer = self.birthday_fixture(701_054_107)
        call = self.add(session.engine, 'Call of the Ring', zone='hand', ref='native-ring-call')
        other_call = self.add(session.engine, 'Call of the Ring', seat='B', ref='native-ring-other-call')
        claim = self.add(session.engine, 'Claim the Precious', zone='hand', ref='native-ring-rechoice')
        target = self.add(session.engine, 'Generic Bound Body', seat='B', ref='native-ring-rechoice-target')
        self.prepare(session, mana={'U': 4, 'B': 4, 'C': 20})
        self.checkpoint(session)
        self.cast(session, 'A', call)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[call.object_id].zone)
        for source, targets, pay in ((birthday, (), True), (claim, (target.ref,), False)):
            self.cast(session, 'A', source, targets)
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            accepted = session.act('pilot:A', {'action_id': 'choose', 'objects': [bearer.ref]})
            self.assertTrue(accepted.ok, accepted.summary)
            decision = self.resolve(session)
            self.assertIsNotNone(decision)
            self.assertEqual(['A'], decision.actors)
            session = self.replay(session, load=True)
            life = {seat: session.state.players[seat].life for seat in 'ABCD'}
            hands = {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
            unchanged = authoritative_state_hash(session.state)
            response = {'action_id': 'choose', 'pay': pay}
            self.assertFalse(session.act('pilot:B', response).ok)
            self.assertEqual(unchanged, authoritative_state_hash(session.state))
            result = session.act('pilot:A', response)
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(session))
            for seat in 'ABCD':
                self.assertEqual(life[seat] - (2 if pay and seat == 'A' else 0), session.state.players[seat].life)
                self.assertEqual(hands[seat] + (1 if pay and seat == 'A' else 0),
                                 len(session.state.players[seat].zones['hand']))
            self.replay(session, load=True)
        self.assertEqual(2, current_ring_state(session.engine, 'A').temptation_count)
        self.assertEqual(bearer.object_id, current_ring_state(session.engine, 'A').bearer.identity.object_id)
        self.assertEqual('battlefield', session.state.cards[other_call.object_id].zone)

    def test_call_does_not_observe_an_impossible_choice(self):
        self.assert_original_closed('Call of the Ring')
        session = self.session(701_054_108)
        self.add(session.engine, 'Call of the Ring', ref='native-ring-no-choice-call')
        birthday = self.add(session.engine, 'Birthday Escape', zone='hand', ref='native-ring-no-choice-birthday')
        self.prepare(session, mana={'U': 3, 'C': 3})
        self.checkpoint(session)
        life = {seat: session.state.players[seat].life for seat in 'ABCD'}
        self.cast(session, 'A', birthday)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, current_ring_state(session.engine, 'A').temptation_count)
        self.assertIsNone(current_ring_state(session.engine, 'A').bearer)
        self.assertEqual(life, {seat: session.state.players[seat].life for seat in 'ABCD'})
        self.replay(session, load=True)

    def test_ring_power_restriction_agrees_in_offered_and_accepted_blocks(self):
        session, birthday, bearer = self.birthday_fixture(701_054_109)
        equal = self.add(session.engine, 'Generic Bound Body', seat='B', ref='native-ring-equal-blocker')
        larger = self.add(session.engine, 'Generic Bound Body', seat='B', ref='native-ring-larger-blocker')
        session.engine.apply_effect({'op': 'modify_stats_until_end_of_turn', 'card': larger.ref,
                                     'power': 1, 'toughness': 0}, actor='B')
        self.prepare(session, mana={'U': 4, 'C': 20})
        self.checkpoint(session)
        self.resolve_birthday(session, birthday, bearer)
        self.until(session, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == 'combat.attackers')
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {bearer.ref: 'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(session, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == 'combat.blockers'
                   and current.state.pending_decision.actors == ['B'])
        packet = session.packet('pilot:B', full=True)
        legal = packet['decision']['ctx']['legal_blocks']
        self.assertEqual([], legal.get(larger.ref, []))
        self.assertIn(bearer.ref, legal[equal.ref])
        unchanged = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': 'block', 'blocks': {larger.ref: bearer.ref}})
        self.assertFalse(rejected.ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {equal.ref: bearer.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIn(equal.object_id, session.state.combat.blockers[bearer.object_id])
        self.replay(session, load=True)
