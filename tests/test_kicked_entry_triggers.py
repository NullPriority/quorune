from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class KickedEntryCompilerTests(unittest.TestCase):
    def test_kicked_entry_condition_and_independent_targets_keep_source_spans(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for body in ('destroy target creature with flying.', 'create a 1/1 white Soldier creature token.', 'tap up to two target creatures.', 'you gain 10 life.'):
            text = 'When this creature enters, if it was kicked, ' + body
            with self.subTest(body=body):
                ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
                node = ir.faces[0].nodes[0]
                self.assertEqual('fixed-kicked-entry-trigger-v1', node.template_id)
                self.assertEqual('permanent.enter.self', node.event)
                self.assertEqual({'field': 'cast_option', 'op': 'eq', 'value': 'kicked'}, node.event_condition)
                self.assertEqual(text, ir.faces[0].oracle_text[node.span.start:node.span.end])
                self.assertIn('trigger.entry.fixed_kicked_result', node.capability_dependencies)
                self.assertEqual(registry.closure(('trigger.entry.fixed_kicked_result',), profile='commander_review').trusted, node.exact)

    def test_kicked_entry_pronouns_refer_to_the_same_paid_source(self):
        from test_fixed_optional_mana_payment_triggers import payment_record

        registry = load_default_capability_registry()
        for subject in ("it", "he", "she"):
            with self.subTest(subject=subject):
                text = f"When this creature enters, if {subject} was kicked, create a 1/1 white Soldier creature token."
                ir = compile_oracle_card(payment_record(text), capability_registry=registry,
                                         capability_profile="commander_review")
                self.assertEqual("exact", ir.status)
                node, = ir.faces[0].nodes
                self.assertEqual({"field": "cast_option", "op": "eq", "value": "kicked"}, node.event_condition)

    def test_kicked_entry_unknown_payment_results_and_siblings_remain_residual(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for text in (
            'When this creature enters, if it was kicked twice, draw a card.',
            'When another creature enters, if it was kicked, draw a card.',
            'When this creature enters, if it was kicked, perform an unrepresented ritual.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(payment_record(text), capability_registry=registry)
                self.assertNotEqual('exact', ir.status)
        text = 'Kicker {G}\nWhen this creature enters, if it was kicked, draw a card.\nWhenever a player sneezes, draw a card.'
        ir = compile_oracle_card(payment_record(text), capability_registry=registry)
        self.assertNotEqual('exact', ir.status)

    def test_kicked_entry_condition_and_dependency_mutants_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        record = payment_record('When this creature enters, if it was kicked, draw a card.')
        for dependency in ('trigger.entry.fixed_kicked_result', 'casting.kicker.fixed_mana', 'trigger.event.normalized_zone_change', 'trigger.placement.apnap'):
            blocked = deepcopy(raw); row = next(r for r in blocked['capabilities'] if r['id'] == dependency)
            row.update(status='blocked', blockers=['Independent kicked entry dependency mutation'])
            with self.subTest(dependency=dependency):
                self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(blocked)).status)
        from quorune.compiler import kicked_entry_trigger_nodes as owner
        with mock.patch.object(owner, 'fixed_kicked_entry_trigger_node', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=load_default_capability_registry()).status)


class KickedEntryActionTests(unittest.TestCase):
    # CR 702.33e/f, 603.4 and 400.7: paid kicker is an entry cast fact;
    # returning the card creates a new object without that payment history.
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'kicked-entry.sqlite3'
        copy_fixture = json.loads(
            (ROOT / 'tests/fixtures/fixed-qualified-stack-copy-cards.json').read_text(encoding='utf-8')
        )
        copy_card, = [row for row in copy_fixture['cards'] if row['name'] == 'Lithoform Engine']
        copy_fixture['cards'] = [copy_card]
        copy_fixture['rulings'] = [row for row in copy_fixture.get('rulings', ())
                                    if row['oracle_id'] == copy_card['oracle_id']]
        copy_path = path.with_name('copy-authority-fixture.json')
        copy_path.write_text(json.dumps(copy_fixture), encoding='utf-8')
        witness = path.with_name('variable-kicker-witness.json')
        witness.write_text(json.dumps({'schema_version': 1, 'cards': [{
            'oracle_id': 'fixture:positive-generic-kicker', 'name': 'Positive Kicker Witness',
            'mana_cost': '{1}{U}', 'cmc': 2.0, 'type_line': 'Creature — Elemental',
            'power': '2', 'toughness': '2', 'colors': ['U'], 'color_identity': ['U'],
            'keywords': ['Kicker'], 'layout': 'normal', 'legalities': {'commander': 'legal'},
            'oracle_text': "Kicker {X}. X can't be 0. (You may pay an additional {X} as you cast this spell.)\n"
                'When this creature enters, if it was kicked, draw a card.',
        }]}), encoding='utf-8')
        artifacts = {'schema_version': 1, 'cards': [], 'rulings': []}
        for source, names in (
            ('reconfigure-cards.json', {'Bonesplitter'}),
            ('announced-artifact-copy-cards.json', {'Disenchant', 'Sol Ring'}),
        ):
            support = json.loads((ROOT / 'tests/fixtures' / source).read_text(encoding='utf-8'))
            selected = [row for row in support['cards'] if row['name'] in names]
            assert {row['name'] for row in selected} == names
            artifacts['cards'].extend(selected)
            oracle_ids = {row['oracle_id'] for row in selected}
            artifacts['rulings'].extend(row for row in support['rulings'] if row['oracle_id'] in oracle_ids)
        artifact_path = path.with_name('artifact-entry-support.json')
        artifact_path.write_text(json.dumps(artifacts), encoding='utf-8')
        build_fixture_database([
            copy_path, witness, artifact_path,
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json',
            ROOT / 'tests/fixtures/kicked-entry-triggers.json',
        ], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Kicked entry deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in 'ABCD'}, first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session); engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        records = tuple(record for record in self.db.iter_cards() if compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review').status == 'exact')
        register_generated_programs(self.db, engine.semantics, records, trust_level='trusted', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='hand', seat='A'):
        record = self.db.lookup(name)
        card = CardInstance(object_id='kicked:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=seat, controller=seat, zone=zone, zone_timestamp=engine._next_zone_timestamp(), known_to=[seat] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card; engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self, session, source, mana):
        engine = session.engine; engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('A'); engine.pump()
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs); self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        return next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state); session.commands.clear(); session.decisions.clear()

    def advance_to(self, session, predicate):
        for _ in range(24):
            if predicate(session): return
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Kicked entry did not reach its expected boundary')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kicked-entry-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_actual_kicked_and_unkicked_druid_casts_pay_exact_cost_and_replay(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                session = self.session(7023301 + int(kicked)); engine = session.engine
                source = self.add(engine, 'Krosan Druid', ref='DRUID')
                action = self.ready(session, source, {'G': 2, 'C': 6})
                self.assertEqual({'normal', 'kicked'}, {option['id'] for option in action['cost_options']})
                self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked' if kicked else 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(0 if kicked else 1, engine.state.players['A'].mana_pool['G'])
                self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
                self.assertEqual(1 if kicked else 0, len(session.state.stack))
                if kicked:
                    self.assertEqual('kicked', session.state.stack[-1].context['cast_option'])
                    self.advance_to(session, lambda s: not s.state.stack)
                self.assertEqual(50 if kicked else 40, session.state.players['A'].life)
                self.replay(session)

    def test_positive_generic_kicker_preserves_paid_x_and_private_draw(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                session = self.session(7023391 + int(kicked))
                engine = session.engine
                source = self.add(engine, 'Positive Kicker Witness', ref='VAR-KICKER')
                drawn = self.add(engine, 'Air Elemental', ref='VAR-HIDDEN', zone='library')
                action = self.ready(session, source, {'U': 1, 'C': 4})
                options = {row['id']: row for row in action['cost_options']}
                self.assertEqual({'normal', 'kicked'}, set(options))
                self.assertNotIn('x', options['normal'].get('choice_schema', {}))
                self.assertEqual({'type': 'integer', 'minimum': 1, 'maximum': 3, 'default': 1},
                                 options['kicked']['choice_schema']['x'])
                self.checkpoint(session)
                response = {'action_id': action['id'], 'cost_option': 'normal', 'pay': 'auto'}
                if kicked:
                    response.update(cost_option='kicked', x=2)
                accepted = session.act('pilot:A', response)
                self.assertTrue(accepted.ok, accepted.summary)
                spell, = session.state.stack
                self.assertEqual(2 if kicked else None, spell.x_value)
                self.assertEqual(kicked, spell.context['kicked'])
                self.assertEqual(1 if kicked else 3, session.state.players['A'].mana_pool['C'])
                self.assertEqual(0, session.state.players['A'].mana_pool['U'])
                self.advance_to(session, lambda game: game.state.cards[source.object_id].zone == 'battlefield')
                self.assertEqual(int(kicked), len(session.state.stack))
                if kicked:
                    self.advance_to(session, lambda game: not game.state.stack)
                self.assertEqual('hand' if kicked else 'library', session.state.cards[drawn.object_id].zone)
                for seat in 'BCD':
                    self.assertNotIn(drawn.printed_name, json.dumps(session.packet(f'pilot:{seat}', full=True)))
                self.replay(session)

    def test_positive_generic_kicker_rejects_invalid_unselected_and_unpayable_x(self):
        session = self.session(7023393)
        source = self.add(session.engine, 'Positive Kicker Witness', ref='VAR-REJECT')
        action = self.ready(session, source, {'U': 1, 'C': 4})
        base = {'action_id': action['id'], 'pay': 'auto'}
        self.checkpoint(session)
        expected = authoritative_state_hash(session.state)
        for choices in (
            {'cost_option': 'kicked'}, {'cost_option': 'kicked', 'x': 0},
            {'cost_option': 'kicked', 'x': -1}, {'cost_option': 'kicked', 'x': 4},
            {'cost_option': 'kicked', 'x': True}, {'cost_option': 'kicked', 'x': '2'},
            {'cost_option': 'kicked', 'x': 1.5}, {'cost_option': 'normal', 'x': 2}, {'x': 2},
            {'cost_option': 'normal', 'x': False}, {'cost_option': 'normal', 'x': '0'},
            {'cost_option': 'kicked', 'x': 2, 'declared_cost': {'GENERIC': 1, 'U': 1}},
        ):
            with self.subTest(choices=choices):
                rejected = session.act('pilot:A', {**base, **choices})
                self.assertFalse(rejected.ok, rejected.summary)
                self.assertEqual(expected, authoritative_state_hash(session.state))
                self.assertFalse(session.commands)

    def test_positive_generic_kicker_requires_exact_shape_and_runtime_authority(self):
        from quorune.kicker import (
            KickerError, FixedManaKickerSpec, POSITIVE_GENERIC_KICKER_CAPABILITY,
            positive_generic_kicker_runtime_dependencies,
        )
        from quorune.rules.capabilities import CapabilityRegistry

        record = self.db.lookup('Positive Kicker Witness')
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status)
        for invalid in (
            replace(record, mana_cost='{X}{U}'), replace(record, mana_cost='{W/U}'),
            replace(record, oracle_text=record.oracle_text.replace(". X can't be 0.", "")),
            replace(record, oracle_text=record.oracle_text.replace('Kicker {X}', 'Kicker {X}{U}')),
            replace(record, oracle_text=record.oracle_text + '\nKicker {2}'),
        ):
            with self.subTest(cost=invalid.mana_cost, text=invalid.oracle_text):
                compiled = compile_oracle_card(invalid, capability_registry=self.registry, capability_profile='commander_review')
                self.assertNotEqual('exact', compiled.status)
        node = next(node for node in ir.faces[0].nodes if node.event == 'cast.cost')
        spec = dict(node.handlers[0]['kicker'])
        self.assertTrue(FixedManaKickerSpec.from_dict(spec).has_positive_generic_x)
        with self.assertRaises(KickerError):
            FixedManaKickerSpec.from_dict({**spec, 'oracle_line': 'Kicker {X}.'})
        game = self.session(7023394)
        program = next(program for program in game.engine.semantics.programs_for_oracle(record.oracle_id)
                       if program.event == 'cast.cost')
        self.assertEqual((POSITIVE_GENERIC_KICKER_CAPABILITY,), positive_generic_kicker_runtime_dependencies(program))
        with self.assertRaises(KickerError):
            positive_generic_kicker_runtime_dependencies(replace(program, active_zone='graveyard'))

        registry = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(row for row in registry['capabilities'] if row['id'] == 'casting.kicker.fixed_mana')
        row.update(status='blocked', blockers=['Kicker prerequisite omitted'])
        invalid = compile_oracle_card(record, capability_registry=CapabilityRegistry(registry), capability_profile='commander_review')
        self.assertNotEqual('exact', invalid.status)

    def test_mana_kicker_runtime_binding_requires_the_validated_price_owner(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.kicker import KICKER_CAPABILITY_ID, POSITIVE_GENERIC_KICKER_CAPABILITY

        profile = 'commander_review'
        game = self.session(7023399)
        for name, owner, wrong_owner, wrong_version in (
            ('Krosan Druid', KICKER_CAPABILITY_ID, POSITIVE_GENERIC_KICKER_CAPABILITY, 2),
            ('Thieving Skydiver', POSITIVE_GENERIC_KICKER_CAPABILITY, KICKER_CAPABILITY_ID, 1),
        ):
            with self.subTest(card=name):
                record = self.db.lookup(name)
                program, = (ability for ability in game.engine.semantics.programs_for_oracle(record.oracle_id)
                            if ability.event == 'cast.cost')
                binding = bind_semantic_program_runtime(
                    program, capability_registry=self.registry, profile=profile,
                )
                self.assertTrue(binding['strict'], binding['blockers'])
                self.assertEqual([owner], binding['required_registered_capabilities'])
                self.assertIn(KICKER_CAPABILITY_ID, binding['capability_closure']['reachable'])

                crossed = replace(
                    program,
                    capability_dependencies=[wrong_owner],
                    capability_closure=self.registry.closure([wrong_owner], profile=profile).to_dict(),
                )
                binding = bind_semantic_program_runtime(
                    crossed, capability_registry=self.registry, profile=profile,
                )
                self.assertFalse(binding['strict'])
                self.assertIn(f'capability:undeclared_runtime_dependency:{owner}', binding['blockers'])

                descriptor, = program.handlers
                malformed = replace(program)
                malformed.handlers = [{
                    **descriptor, 'kicker': {**descriptor['kicker'], 'schema_version': wrong_version},
                }]
                binding = bind_semantic_program_runtime(
                    malformed, capability_registry=self.registry, profile=profile,
                )
                self.assertFalse(binding['strict'])
                self.assertTrue(any(
                    issue.startswith(f"runtime_handler:invalid:{descriptor['handler_id']}:")
                    for issue in binding['blockers']
                ), binding['blockers'])

    def _invoke_kicked_artifact_entry(self, session, source, *, x=1):
        action = self.ready(session, source, {'U': 1, 'C': 5, 'W': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {
            'action_id': action['id'], 'cost_option': 'kicked', 'x': x, 'pay': 'auto',
        })
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(x, session.state.stack[-1].x_value)
        self.advance_to(session, lambda game: game.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        return session.packet('pilot:A', full=True)['decision']['ctx']['target_schema']

    def _cast_in_response(self, session, seat, spell, target):
        self.advance_to(session, lambda game: game.state.priority_player == seat)
        actions = session.packet(f'pilot:{seat}', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'] == f'cast:{spell.ref}')
        result = session.act(f'pilot:{seat}', {
            'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto',
        })
        self.assertTrue(result.ok, result.summary)

    def _ready_equipped_artifact(self, engine):
        from quorune.attachments import attach_objects

        equipment = self.add(engine, 'Bonesplitter', ref='ENTRY-EQUIP', zone='battlefield', seat='B')
        carrier = self.add(engine, 'Air Elemental', ref='ENTRY-CARRIER', zone='battlefield', seat='B')
        attach_objects(engine.state.cards, equipment, carrier,
                       source_timestamp=engine._next_zone_timestamp(), players=engine.state.players)
        return equipment, carrier

    def test_skydiver_kicked_limit_transfers_and_reattaches_exact_equipment(self):
        session = self.session(7023395)
        engine = session.engine
        source = self.add(engine, 'Thieving Skydiver', ref='SKYDIVER')
        equipment, carrier = self._ready_equipped_artifact(engine)
        own = self.add(engine, 'Sol Ring', ref='OWN-RING', zone='battlefield')
        expensive = self.add(engine, 'Skyclave Relic', ref='ENTRY-EXPENSIVE', zone='battlefield', seat='C')
        blink = self.add(engine, 'Cloudshift', ref='ENTRY-BLINK')
        schema = self._invoke_kicked_artifact_entry(session, source)
        self.assertEqual({equipment.ref, own.ref}, set(schema['legal_refs']))
        self.assertEqual(1, schema['mana_value_max'])
        for seat in 'BCD':
            self.assertIsNone(session.packet(f'pilot:{seat}', full=True)['decision'])
        expected = authoritative_state_hash(session.state)
        command_count = len(session.commands)
        for principal, target in (('pilot:B', equipment), ('pilot:A', expensive), ('pilot:A', carrier)):
            rejected = session.act(principal, {'action_id': 'choose', 'targets': [target.ref]})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(expected, authoritative_state_hash(session.state))
            self.assertEqual(command_count, len(session.commands))
        with tempfile.TemporaryDirectory() as directory:
            record_path = Path(directory) / 'pending-skydiver'
            session.save(record_path)
            session = CommanderSession.load(self.db, record_path)
        selected = session.act('pilot:A', {'action_id': 'choose', 'targets': [equipment.ref]})
        self.assertTrue(selected.ok, selected.summary)
        self.advance_to(session, lambda game: not game.state.stack)
        state = session.state
        self.assertEqual('A', state.cards[equipment.object_id].controller)
        self.assertEqual('B', state.cards[equipment.object_id].owner)
        self.assertEqual(source.object_id, state.cards[equipment.object_id].attached_to)
        self.assertNotIn(equipment.object_id, state.cards[carrier.object_id].attachments)
        self.assertEqual('4', session.engine._effective_card_data(state.cards[source.object_id])['power'])

        original = state.cards[source.object_id].logical_object_id
        self._cast_in_response(session, 'A', blink, state.cards[source.object_id])
        self.advance_to(session, lambda game: not game.state.stack)
        self.assertNotEqual(original, state.cards[source.object_id].logical_object_id)
        self.assertEqual('A', state.cards[equipment.object_id].controller)
        self.assertIsNone(state.cards[equipment.object_id].attached_to)
        self.replay(session)

    def test_skydiver_original_entry_survives_blink_without_moving_equipment(self):
        session = self.session(7023396)
        engine = session.engine
        source = self.add(engine, 'Thieving Skydiver', ref='SKYDIVER')
        equipment, carrier = self._ready_equipped_artifact(engine)
        blink = self.add(engine, 'Cloudshift', ref='ENTRY-BLINK')
        self._invoke_kicked_artifact_entry(session, source)
        selected = session.act('pilot:A', {'action_id': 'choose', 'targets': [equipment.ref]})
        self.assertTrue(selected.ok, selected.summary)
        original = source.logical_object_id
        self._cast_in_response(session, 'A', blink, source)
        self.advance_to(session, lambda game: game.state.cards[source.object_id].logical_object_id != original)
        self.assertEqual(1, len(session.state.stack))
        self.advance_to(session, lambda game: not game.state.stack)
        self.assertEqual(('A', carrier.object_id), (equipment.controller, equipment.attached_to))
        self.replay(session)

    def test_skydiver_revalidates_original_target_and_skips_non_equipment(self):
        for choice in ('removed', 'plain', 'declined', 'no_legal_target'):
            with self.subTest(choice=choice):
                session = self.session(7023397)
                engine = session.engine
                source = self.add(engine, 'Thieving Skydiver', ref='SKYDIVER')
                target = self.add(
                    engine, 'Skyclave Relic' if choice == 'no_legal_target' else 'Sol Ring',
                    ref='ENTRY-ARTIFACT', zone='battlefield', seat='B',
                )
                removal = self.add(engine, 'Disenchant', ref='ENTRY-REMOVAL', seat='B')
                session.state.players['B'].mana_pool.update({'W': 1, 'C': 1})
                if choice in {'removed', 'plain'}:
                    self._invoke_kicked_artifact_entry(session, source)
                    selected = session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
                    self.assertTrue(selected.ok, selected.summary)
                    if choice == 'removed':
                        self._cast_in_response(session, 'B', removal, target)
                else:
                    action = self.ready(session, source, {'U': 1, 'C': 5})
                    self.checkpoint(session)
                    cost = {'cost_option': 'normal'} if choice == 'declined' else {'cost_option': 'kicked', 'x': 1}
                    accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', **cost})
                    self.assertTrue(accepted.ok, accepted.summary)
                self.advance_to(session, lambda game: not game.state.stack)
                self.assertIsNone(target.attached_to)
                self.assertEqual('graveyard' if choice == 'removed' else 'battlefield', target.zone)
                self.assertEqual('A' if choice == 'plain' else 'B', target.controller)
                self.assertNotIn('attachment.attach', {event.code for event in session.state.events})
                if choice == 'no_legal_target':
                    self.assertIn('stack.trigger.removed', {event.code for event in session.state.events})
                self.replay(session)

    def test_skydiver_paid_spell_copy_uses_the_same_x_on_its_own_entry(self):
        from quorune.targets import target_plan

        session = self.session(7023399)
        session.state.stack_resolution_authority_version = 2
        engine = session.engine
        source = self.add(engine, 'Thieving Skydiver', ref='COPY-SKYDIVER')
        copier = self.add(engine, 'Lithoform Engine', ref='ENTRY-COPIER', zone='battlefield')
        first = self.add(engine, 'Sol Ring', ref='COPY-FIRST', zone='battlefield', seat='B')
        second = self.add(engine, 'Sol Ring', ref='COPY-SECOND', zone='battlefield', seat='C')
        expensive = self.add(engine, 'Skyclave Relic', ref='COPY-EXPENSIVE', zone='battlefield', seat='D')
        action = self.ready(session, source, {'U': 1, 'C': 10})
        self.checkpoint(session)
        cast = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'x': 2, 'pay': 'auto'})
        self.assertTrue(cast.ok, cast.summary)
        spell, = session.state.stack
        group, = target_plan({'zones': ['stack'], 'categories': ['spell'], 'count': 1}).groups
        original_row, = (row for row in engine._target_candidate_rows('A', group) if row['ref'] == spell.ref)
        self.assertEqual(2, original_row['mana_value'])
        self.advance_to(session, lambda game: game.state.priority_player == 'A')
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        copy_action, = (
            row for row in actions if row['id'].startswith(f'activate:{copier.ref}:')
            and spell.ref in row.get('target_schema', {}).get('legal_refs', ())
        )
        copied = session.act('pilot:A', {'action_id': copy_action['id'], 'targets': [spell.ref], 'pay': 'auto'})
        self.assertTrue(copied.ok, copied.summary)
        self.advance_to(session, lambda game: game.state.stack[-1].kind == 'spell_copy')
        self.assertEqual(2, session.state.stack[-1].x_value)
        for target in (first, second):
            self.advance_to(session, lambda game: game.state.pending_decision.kind == 'semantic.target')
            packet = session.packet('pilot:A', full=True)['decision']['ctx']['target_schema']
            self.assertEqual(2, packet['mana_value_max'])
            self.assertNotIn(expensive.ref, packet['legal_refs'])
            accepted = session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
            self.assertTrue(accepted.ok, accepted.summary)
        self.advance_to(session, lambda game: not game.state.stack)
        self.assertEqual(('A', 'A', 'D'), (first.controller, second.controller, expensive.controller))
        token, = (
            card for card in session.state.cards.values()
            if card.zone == 'battlefield' and card.is_token and card.oracle_id == source.oracle_id
        )
        self.assertEqual('A', token.controller)
        self.replay(session)

    def test_skydiver_rejects_mutated_price_result_and_runtime_scope(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.rules.kicked_artifact_entry import (
            KICKED_ARTIFACT_ENTRY_CAPABILITY, KICKED_ARTIFACT_ENTRY_LIMIT, project_kicked_artifact_entry_program,
        )
        from quorune.rules.capabilities import CapabilityRegistry

        record = self.db.lookup('Thieving Skydiver')
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status)
        trigger, = [node for node in ir.faces[0].nodes if node.event == 'permanent.enter.self']
        self.assertIn(KICKED_ARTIFACT_ENTRY_CAPABILITY, trigger.capability_dependencies)
        self.assertIn(KICKED_ARTIFACT_ENTRY_LIMIT, trigger.target_schema)
        for text in (
            record.oracle_text.replace("Kicker {X}. X can't be 0.", 'Kicker {3}'),
            record.oracle_text.replace('mana value X or less', 'mana value X or more'),
            record.oracle_text.replace('attach it to this creature', 'attach it to another creature'),
        ):
            with self.subTest(text=text):
                pending = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                              capability_profile='commander_review')
                self.assertNotEqual('exact', pending.status)
        game = self.session(7023398)
        complete = compile_best_available_card_program(
            self.db, record, semantic_registry=game.engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review',
        )
        self.assertTrue(complete.trust_closure['strict_capability_ready'], complete.trust_closure['blockers'])
        binding = bind_card_program_runtime(complete, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        program, = [program for program in game.engine.semantics.programs_for_oracle(record.oracle_id)
                    if program.event == 'permanent.enter.self']
        with self.assertRaises(ValueError):
            project_kicked_artifact_entry_program(replace(program, event_condition=None))
        for dependency in ('casting.kicker.positive_generic_x', 'continuous.control.fixed_resolution',
                           'attachment.action.targeted_equipment', 'quantity_expression.source_entry_x'):
            with self.subTest(dependency=dependency):
                raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                row = next(row for row in raw['capabilities'] if row['id'] == dependency)
                row.update(status='blocked', blockers=['Required Skydiver behavior omitted'])
                pending = compile_oracle_card(record, capability_registry=CapabilityRegistry(raw),
                                              capability_profile='commander_review')
                self.assertNotEqual('exact', pending.status)

    def test_paid_permanent_spell_copy_entry_preserves_the_recorded_version(self):
        for version, end_life in ((1, 50), (2, 60)):
            with self.subTest(version=version):
                session = self.session(7023320 + version)
                session.state.stack_resolution_authority_version = version
                source = self.add(session.engine, 'Krosan Druid', ref='PAID-SOURCE')
                copier = self.add(session.engine, 'Lithoform Engine', ref='SPELL-COPIER', zone='battlefield')
                action = self.ready(session, source, {'G': 2, 'C': 12})
                self.checkpoint(session)
                cast = session.act('pilot:A', {
                    'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto',
                })
                self.assertTrue(cast.ok, cast.summary)
                original, = [item for item in session.state.stack if item.card_object_id == source.object_id]
                self.advance_to(session, lambda game: 'pilot:A' in game.pending_principals())
                actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                copy_action, = [
                    row for row in actions
                    if row['id'].startswith(f'activate:{copier.ref}:')
                    and original.ref in row.get('target_schema', {}).get('legal_refs', ())
                ]
                accepted = session.act('pilot:A', {
                    'action_id': copy_action['id'], 'targets': [original.ref], 'pay': 'auto',
                })
                self.assertTrue(accepted.ok, accepted.summary)
                self.advance_to(session, lambda game: not game.state.stack)
                self.assertEqual(end_life, session.state.players['A'].life)
                self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
                copy_token, = [card for card in session.state.cards.values()
                               if card.zone == 'battlefield' and card.is_token]
                self.assertEqual(source.printed_name, copy_token.printed_name)
                self.replay(session)

    def test_actual_kicked_verix_creates_karox_with_its_printed_characteristics(self):
        session = self.session(7023308)
        engine = session.engine
        source = self.add(engine, 'Verix Bladewing', ref='VERIX')
        action = self.ready(session, source, {'R': 2, 'C': 5})
        self.assertEqual({'normal', 'kicked'}, {option['id'] for option in action['cost_options']})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack))
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))
        self.advance_to(session, lambda s: not s.state.stack)
        token, = (card for card in session.state.cards.values() if card.is_token)
        self.assertEqual(('Karox Bladewing', 'A', 'A', 'battlefield'),
                         (token.printed_name, token.owner, token.controller, token.zone))
        current = engine._effective_card_data(token)
        self.assertEqual(('Legendary Creature — Dragon', '4', '4'),
                         (current['type_line'], current['power'], current['toughness']))
        self.assertEqual({'R'}, set(current['colors']))
        self.assertEqual({'flying'}, engine._combat_keywords(token))
        self.replay(session)

    def test_actual_kicked_entry_trigger_survives_blink_and_new_entry_has_no_kicker(self):
        session = self.session(7023303); engine = session.engine
        source = self.add(engine, 'Krosan Druid', ref='DRUID')
        spell = self.add(engine, 'Cloudshift', ref='BLINK')
        action = self.ready(session, source, {'G': 2, 'C': 8, 'W': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack)); old_identity = source.logical_object_id
        self.advance_to(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'priority' and s.state.priority_player == 'A')
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:BLINK')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].logical_object_id != old_identity)
        self.assertEqual(1, len(session.state.stack), 'A returned new object must not trigger another kicked ability')
        self.advance_to(session, lambda s: not s.state.stack)
        self.assertEqual(50, session.state.players['A'].life); self.replay(session)

    def test_actual_kicked_private_library_choice_rolls_back_and_loads_pending(self):
        session = self.session(7023304); engine = session.engine
        source = self.add(engine, 'Vineshaper Prodigy', ref='PRODIGY')
        action = self.ready(session, source, {'G': 1, 'U': 1, 'C': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind != 'priority')
        packet = session.packet('pilot:A', full=True)['decision']
        for seat in 'BCD': self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
        before = authoritative_state_hash(session.state)
        bad = session.act('pilot:B', {'action_id': 'choose'})
        self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-kicked-library'; session.save(path)
            session = CommanderSession.load(self.db, path)
        packet = session.packet('pilot:A', full=True)['decision']
        ctx = packet['ctx']
        self.assertEqual(3, len(ctx['objects']))
        refs = [card['id'] for card in ctx['objects']]
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': {'hand': [refs[0]], 'bottom': refs[1:]}})
        self.assertTrue(result.ok, result.summary)
        self.replay(session)

    def test_kicked_targets_are_chosen_only_on_paid_entry_and_illegal_choices_roll_back(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                session = self.session(7023305 + int(kicked)); engine = session.engine
                source = self.add(engine, 'Oran-Rief Recluse', ref='RECLUSE')
                flying = self.add(engine, 'Air Elemental', ref='FLYING', zone='battlefield', seat='B')
                ground = self.add(engine, 'Llanowar Elves', ref='GROUND', zone='battlefield', seat='C')
                action = self.ready(session, source, {'G': 2, 'C': 4})
                self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked' if kicked else 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
                if not kicked:
                    self.assertFalse(session.state.stack)
                    self.assertEqual('priority', session.state.pending_decision.kind)
                else:
                    self.assertEqual('semantic.target', session.state.pending_decision.kind)
                    before = authoritative_state_hash(session.state)
                    for principal, ref in (('pilot:B', flying.ref), ('pilot:A', ground.ref)):
                        bad = session.act(principal, {'action_id': 'choose', 'targets': [ref]})
                        self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(session.state))
                    result = session.act('pilot:A', {'action_id': 'choose', 'targets': [flying.ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.advance_to(session, lambda s: not s.state.stack)
                    self.assertEqual('graveyard', session.state.cards[flying.object_id].zone)
                self.assertEqual('battlefield', session.state.cards[ground.object_id].zone)
                self.replay(session)

    def test_kicked_optional_targets_allow_zero_without_hiding_legal_targets(self):
        session = self.session(7023307); engine = session.engine
        source = self.add(engine, 'Kitesail Cleric', ref='CLERIC')
        target = self.add(engine, 'Air Elemental', ref='LEGAL', zone='battlefield', seat='B')
        action = self.ready(session, source, {'W': 2, 'C': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
        self.assertIn(target.ref, str(session.packet('pilot:A', full=True)['decision']))
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': []})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: not s.state.stack)
        self.assertFalse(session.state.cards[target.object_id].tapped); self.replay(session)

    def test_kicked_condition_omission_mutant_is_killed_by_actual_unkicked_cast(self):
        from dataclasses import replace
        from quorune.compiler import kicked_entry_trigger_nodes as owner
        original = owner.fixed_kicked_entry_trigger_node
        def omit(**kwargs):
            node = original(**kwargs)
            return replace(node, event_condition=None) if node else None
        with mock.patch.object(owner, 'fixed_kicked_entry_trigger_node', omit):
            with self.assertRaises(AssertionError):
                session = self.session(7023309); source = self.add(session.engine, 'Krosan Druid', ref='UNPAID')
                action = self.ready(session, source, {'G': 1, 'C': 2})
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
                self.assertFalse(session.state.stack, 'An unpaid entry must not trigger the kicked result')

    def test_actual_kicked_relic_copies_do_not_inherit_kicker_and_create_no_loop(self):
        session = self.session(7023308); engine = session.engine
        source = self.add(engine, 'Skyclave Relic', ref='RELIC')
        action = self.ready(session, source, {'C': 6})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack))
        self.advance_to(session, lambda s: not s.state.stack)
        copies = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(2, len(copies))
        self.assertTrue(all(card.tapped for card in copies))
        self.assertTrue(all(card.annotations.get('kicker_paid') is not True for card in copies))
        self.replay(session)

    def test_actual_kicked_warhorse_creates_one_token_after_paid_entry(self):
        session = self.session(7023310); engine = session.engine
        source = self.add(engine, 'Phyrexian Warhorse', ref='WARHORSE')
        action = self.ready(session, source, {'B': 1, 'W': 1, 'C': 4})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack))
        self.advance_to(session, lambda s: not s.state.stack)
        soldiers = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(1, len(soldiers)); self.assertEqual('Soldier Token', soldiers[0].printed_name)
        self.replay(session)
