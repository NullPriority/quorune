"""Independent CR400.7f contracts for attachment-source returns."""

from dataclasses import FrozenInstanceError, replace
import unittest

from quorune.attachment_source_return_model import (
    AttachmentSourceReturnSpec,
    compile_attachment_source_return_body,
    AttachmentDepartureSource, OPERATION, SOURCE_FIELD, validate_attachment_source_return,
)


class AttachmentSourceReturnModelTests(unittest.TestCase):
    def test_controller_graveyard_restriction_does_not_become_an_owner_search(self):
        spec = compile_attachment_source_return_body(
            'Return this card from your graveyard to the battlefield.')
        self.assertTrue(spec.allows_graveyard(graveyard_owner='A', trigger_controller='A'))
        self.assertFalse(spec.allows_graveyard(graveyard_owner='B', trigger_controller='A'))
        for player in ('', None, False):
            with self.assertRaises(ValueError):
                spec.allows_graveyard(graveyard_owner=player, trigger_controller='A')

    def test_unqualified_returns_keep_borrowed_auras_and_distinct_entry_instructions(self):
        delayed = compile_attachment_source_return_body(
            'Return this card to the battlefield at the beginning of the next end step.')
        transformed = compile_attachment_source_return_body(
            'Return this card to the battlefield transformed under your control.')
        self.assertTrue(delayed.allows_graveyard(graveyard_owner='B', trigger_controller='A'))
        self.assertTrue(transformed.allows_graveyard(graveyard_owner='B', trigger_controller='A'))
        self.assertEqual(('next_end_step', False), (delayed.timing, delayed.transformed))
        self.assertEqual(('immediate', True), (transformed.timing, transformed.transformed))

    def test_return_specification_is_immutable_and_rejects_unrepresented_combinations(self):
        spec = AttachmentSourceReturnSpec('controller', 'immediate')
        with self.assertRaises(FrozenInstanceError):
            spec.timing = 'next_end_step'
        for changes in ({'schema_version': True}, {'schema_version': 1.0},
                        {'schema_version': 0}, {'timing': 'next_end_step'},
                        {'transformed': 1}, {'transformed': True},
                        {'graveyard_scope': 'owner'}, {'graveyard_scope': []},
                        {'timing': []}, {'timing': 'cleanup'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(spec, **changes)

    def test_closed_codec_preserves_each_semantic_scope_and_refuses_extra_authority(self):
        for spec in (AttachmentSourceReturnSpec('controller', 'immediate'),
                     AttachmentSourceReturnSpec('any', 'next_end_step'),
                     AttachmentSourceReturnSpec('any', 'immediate', transformed=True)):
            self.assertEqual(spec, AttachmentSourceReturnSpec.from_dict(spec.to_dict()))
            for field in ('origin', 'source', 'controller', 'card_name'):
                with self.assertRaises(ValueError):
                    AttachmentSourceReturnSpec.from_dict({**spec.to_dict(), field: 'B'})
            with self.assertRaises(ValueError):
                AttachmentSourceReturnSpec.from_dict({k: v for k, v in spec.to_dict().items()
                                                      if k != 'schema_version'})

    def test_adjacent_optional_linked_or_unrepresented_return_grammar_remains_closed(self):
        for text in ('Return it from your graveyard to the battlefield.',
                     'You may return this card from your graveyard to the battlefield.',
                     'Return this card to the battlefield under its owner\'s control.',
                     'Return this card to the battlefield transformed at the next end step.',
                     'Return this card from your graveyard to the battlefield. Draw a card.',
                     'Return enchanted creature to the battlefield.', None):
            with self.subTest(text=text):
                self.assertIsNone(compile_attachment_source_return_body(text))


class AttachmentSourceReturnOwnerTests(unittest.TestCase):
    def link(self):
        return AttachmentDepartureSource('aura-id', 'Aura1', 'opaque-battlefield-incarnation', 4, 'B')

    def card(self, **changes):
        from types import SimpleNamespace
        return SimpleNamespace(**{'object_id': 'aura-id', 'ref': 'Aura1', 'owner': 'B',
            'zone': 'graveyard', 'zone_change_counter': 5, 'phased_out': False,
            'object_kind': 'card', **changes})

    def test_original_attachment_can_follow_exactly_one_zone_change_and_no_later_incarnation(self):
        link = self.link()
        self.assertTrue(link.matches_first_graveyard(self.card()))
        for change in ({'zone_change_counter': 4}, {'zone_change_counter': 6},
                       {'zone_change_counter': 7}, {'zone': 'exile'}, {'ref': 'Aura2'},
                       {'object_id': 'different'}, {'owner': 'A'}, {'phased_out': True},
                       {'object_kind': 'token'}):
            with self.subTest(change=change):
                self.assertFalse(link.matches_first_graveyard(self.card(**change)))
        self.assertFalse(link.matches_first_graveyard(None))
        self.assertEqual(link, AttachmentDepartureSource.from_dict(link.to_dict()))
        for change in ({'battlefield_counter': True}, {'battlefield_counter': -1},
                       {'schema_version': 1.0}, {'logical_object_id': ''}):
            with self.assertRaises(ValueError):
                replace(link, **change)

    def test_capture_and_binding_use_the_battlefield_occurrence_without_reacquiring_a_later_source(self):
        from types import SimpleNamespace
        from quorune.attachment_source_return import attachment_source_return_context, bind_attachment_source_return
        spec = AttachmentSourceReturnSpec('controller', 'immediate')
        source = self.card(zone='battlefield', zone_change_counter=4,
                           logical_object_id=self.link().logical_object_id)
        context = attachment_source_return_context(source, SimpleNamespace(effects=[spec.effect()]))
        context['source_logical_object_id'] = source.logical_object_id
        source.zone = 'graveyard'; source.zone_change_counter = 7
        item = SimpleNamespace(source_object_id=source.object_id, context=context)
        bound = bind_attachment_source_return(spec.effect(), item)
        self.assertEqual(4, bound['source']['battlefield_counter'])
        self.assertFalse(AttachmentDepartureSource.from_dict(bound['source']).matches_first_graveyard(source))
        self.assertEqual({}, attachment_source_return_context(source, SimpleNamespace(effects=[{'op': 'draw'}])))
        with self.assertRaises(ValueError):
            bind_attachment_source_return(spec.effect(), SimpleNamespace(source_object_id='other', context=context))

    def test_compiled_and_bound_instructions_cannot_trade_scope_or_transport_authority(self):
        from quorune.attachment_source_return import bind_attachment_source_return
        from types import SimpleNamespace
        spec = AttachmentSourceReturnSpec('any', 'next_end_step')
        self.assertEqual(spec, validate_attachment_source_return(spec.effect(), compiled=True))
        bound = {**spec.effect(), 'source': self.link().to_dict()}
        self.assertEqual(spec, validate_attachment_source_return(bound))
        for change in ({'schema_version': True}, {'phase': 'cleanup'}, {'source': 'Aura1'},
                       {'extra': True}, {'_replacement_selections': 'bad'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_attachment_source_return({**bound, **change})
        with self.assertRaises(ValueError):
            validate_attachment_source_return({**spec.effect(), 'phase': 'return'}, compiled=True)
        with self.assertRaises(ValueError):
            validate_attachment_source_return({**AttachmentSourceReturnSpec('any', 'immediate', True).effect(),
                'source': self.link().to_dict(), '_aura_target_ref': 'target'})
        with self.assertRaises(ValueError):
            bind_attachment_source_return(spec.effect(), SimpleNamespace(source_object_id='aura-id',
                context={'source_logical_object_id': self.link().logical_object_id}))

    def test_borrowed_aura_restriction_precedes_canonical_movement_and_new_incarnations_are_noops(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from quorune.attachment_source_return import apply_attachment_source_return
        card = self.card(); host = SimpleNamespace(active_seats=('A', 'B'),
            state=SimpleNamespace(cards={card.object_id: card}), move_card=Mock(return_value=card))
        effect = {**AttachmentSourceReturnSpec('controller', 'immediate').effect(), 'source': self.link().to_dict()}
        self.assertIsNone(apply_attachment_source_return(host, effect, actor='A', operation=OPERATION, reason='return'))
        host.move_card.assert_not_called()
        apply_attachment_source_return(host, effect, actor='B', operation=OPERATION, reason='return')
        self.assertEqual('B', host.move_card.call_args.kwargs['controller'])
        host.move_card.reset_mock(); card.zone_change_counter = 7
        apply_attachment_source_return(host, effect, actor='B', operation=OPERATION, reason='return')
        host.move_card.assert_not_called()

    def test_delayed_return_uses_the_existing_once_scheduler_with_sealed_source_and_no_movement(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from quorune.attachment_source_return import apply_attachment_source_return
        from quorune.delayed_object_model import OBJECT_TIMINGS
        host = SimpleNamespace(active_seats=('A', 'B'), move_card=Mock())
        effect = {**AttachmentSourceReturnSpec('any', 'next_end_step').effect(), 'source': self.link().to_dict()}
        with patch('quorune.trigger_processing.schedule_delayed_trigger', return_value=SimpleNamespace(ref='DT1')) as schedule:
            self.assertEqual('DT1', apply_attachment_source_return(host, effect, actor='A', operation=OPERATION, reason='return'))
        host.move_card.assert_not_called()
        values = schedule.call_args.kwargs
        self.assertEqual(OBJECT_TIMINGS['next_end_step'], values['condition']); self.assertIs(values['once'], True)
        self.assertEqual(('A', self.link().logical_object_id), (values['controller'], values['source_logical_object_id']))
        future, = values['stack_template']['context']['dynamic_effects']
        self.assertEqual(('return', self.link().to_dict()), (future['phase'], future['source']))

    def test_runtime_minima_are_derived_without_mechanic_annotations_and_reject_unrelated_sources(self):
        from types import SimpleNamespace
        from quorune.rules.attachment_source_return import attachment_source_return_runtime_dependencies, ATTACHMENT_DEATH
        spec = AttachmentSourceReturnSpec('any', 'immediate', True)
        values = dict(effects=[spec.effect()], target_schema=None, cost_schema=None,
            event='creature.dies', active_zone='battlefield', event_condition=ATTACHMENT_DEATH, coverage=())
        self.assertEqual(spec.capabilities, attachment_source_return_runtime_dependencies(SimpleNamespace(**values)))
        for change in ({'event': 'creature.dies.self'}, {'active_zone': 'graveyard'},
                       {'event_condition': None}, {'target_schema': {'kind': 'creature'}},
                       {'cost_schema': {'mana': '{1}'}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                attachment_source_return_runtime_dependencies(SimpleNamespace(**{**values, **change}))

    def test_fresh_pure_first_graveyard_counter_omission_mutant_is_killed(self):
        from unittest.mock import patch
        def omission(link, card):
            return card is not None and card.object_id == link.object_id and card.zone == 'graveyard'
        with patch.object(AttachmentDepartureSource, 'matches_first_graveyard', omission):
            result = unittest.TestResult()
            type(self)('test_original_attachment_can_follow_exactly_one_zone_change_and_no_later_incarnation').run(result)
        self.assertFalse(result.errors, result.errors); self.assertTrue(result.failures)

    def test_token_attachment_occurrence_is_retained_but_departed_token_cannot_return(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from quorune.attachment_source_return import (
            attachment_source_return_context, bind_attachment_source_return, apply_attachment_source_return,
        )
        spec = AttachmentSourceReturnSpec('controller', 'immediate')
        source = self.card(zone='battlefield', zone_change_counter=4, owner='A',
                           object_kind='token', logical_object_id=self.link().logical_object_id)
        captured = attachment_source_return_context(source, SimpleNamespace(effects=[spec.effect()]))
        item = SimpleNamespace(source_object_id=source.object_id, context={**captured,
            'source_logical_object_id': source.logical_object_id})
        source.zone = 'outside'; source.zone_change_counter = 5
        host = SimpleNamespace(active_seats=('A', 'B'), state=SimpleNamespace(cards={source.object_id: source}), move_card=Mock())
        result = apply_attachment_source_return(host, bind_attachment_source_return(spec.effect(), item),
            actor='A', operation=OPERATION, reason='Token Aura return')
        self.assertIsNone(result); host.move_card.assert_not_called()

    def test_single_faced_physical_copy_instructed_to_return_transformed_stays_in_graveyard(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from quorune.attachment_source_return import apply_attachment_source_return
        source = self.card()
        host = SimpleNamespace(active_seats=('A', 'B'), state=SimpleNamespace(cards={source.object_id: source}),
            move_card=Mock(), card_record=Mock(return_value=SimpleNamespace(layout='normal', faces=())))
        effect = {**AttachmentSourceReturnSpec('any', 'immediate', True).effect(), 'source': self.link().to_dict()}
        self.assertIsNone(apply_attachment_source_return(host, effect, actor='A', operation=OPERATION, reason='Copied return'))
        host.move_card.assert_not_called(); self.assertEqual('graveyard', source.zone)


from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients

NAMES = ('Screams from Within', 'Ghoulish Impetus', 'Skin Invasion')


class AttachmentSourceReturnFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name) / 'attachment-return.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/attachment-source-return-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Attachment source returns',
            [DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])


class AttachmentSourceReturnCompilerTests(AttachmentSourceReturnFixture, unittest.TestCase):
    def test_three_complete_originals_close_and_concrete_return_data_cannot_lose_its_owner(self):
        from quorune.oracle_ir import compile_oracle_card, register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        from quorune.attachment_source_return_model import CAPABILITY
        cards = [self.db.lookup(n, fuzzy=False) for n in NAMES]; semantics = SemanticRegistry()
        register_generated_programs(self.db, semantics, cards, trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
                trigger = next(p for p in semantics.programs_for_oracle(card.oracle_id)
                               if any(e.get('op') == OPERATION for e in p.effects))
                deps = tuple(c for c in trigger.capability_dependencies if c != CAPABILITY)
                changed = replace(trigger, coverage=(), handlers=(), capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(result['strict']); self.assertTrue(any(CAPABILITY in b for b in result['blockers']), result['blockers'])

    def test_verified_attachment_source_withdrawal_keeps_unrelated_original_consumers(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.attachment_source_return_model import CAPABILITY
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['Aura return owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            self.assertNotEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review').status)
        for name in ('Memnite', 'Boomerang', 'Steal Enchantment'):
            self.assertEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review').status)


class AttachmentSourceReturnNativeTests(AttachmentSourceReturnFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    def world(self, seed, name, *, kill_seat='A'):
        s = self.session(seed); aura = self.add(s.engine, name, zone='hand')
        carrier = self.add(s.engine, 'Grizzly Bears', seat='B', ref='aura-original-carrier')
        recipient = self.add(s.engine, 'Primal Huntbeast', seat='C', ref='aura-next-hexproof-carrier')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='aura-hidden-control')
        bolt = self.add(s.engine, 'Lightning Bolt', seat=kill_seat, zone='hand', ref='aura-kill-carrier')
        self.assert_closed(s, aura); self.assert_closed(s, recipient)
        return s, aura, carrier, recipient, private, bolt

    def cast_aura(self, s, aura, carrier):
        self.prepare(s, mana={'C': 15, 'B': 12, 'R': 8, 'G': 5, 'U': 5, 'W': 5})
        s.state.players['B'].mana_pool.update({'C': 3, 'U': 5})
        self.checkpoint(s); self.cast_for(s, 'A', aura, [carrier.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(carrier.object_id, s.state.cards[aura.object_id].attached_to)

    def kill_carrier(self, s, carrier, bolt, *, actor='A'):
        self.cast_for(s, actor, bolt, [s.state.cards[carrier.object_id].ref]); self.resolve_top(s)

    def enter_aura(self, s, recipient, *, controller='A'):
        decision = self.resolve(s); self.assertEqual('aura.entry', decision.kind)
        self.assertEqual([controller], decision.actors)
        s = self.replay(s, load=True)
        command = {'action_id': 'choose', 'aura_target': s.state.cards[recipient.object_id].ref}
        self.reject(s, 'D', command)
        result = s.act('pilot:' + controller, command); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); return s

    def next_end_step(self, s):
        self.until(s, lambda ss: ss.state.phase == 'ending' and ss.state.step == 'end_step')

    def test_actual_cast_originals_return_as_nontarget_aura_or_transformed_creature(self):
        for i, name in enumerate(NAMES):
            with self.subTest(card=name):
                s, aura, carrier, recipient, private, bolt = self.world(4007000 + i, name)
                self.cast_aura(s, aura, carrier); old = s.state.cards[aura.object_id].logical_object_id
                self.kill_carrier(s, carrier, bolt)
                if name == 'Skin Invasion':
                    self.assertIsNone(self.resolve(s)); self.assertEqual('Skin Shedder', s.state.cards[aura.object_id].active_face)
                else:
                    if name == 'Ghoulish Impetus':
                        self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
                        self.assertEqual(1, sum(t.active for t in s.state.delayed_triggers)); self.next_end_step(s)
                    s = self.enter_aura(s, recipient)
                    self.assertEqual(recipient.object_id, s.state.cards[aura.object_id].attached_to)
                self.assertEqual('A', s.state.cards[aura.object_id].controller)
                self.assertEqual('battlefield', s.state.cards[aura.object_id].zone)
                self.assertNotEqual(old, s.state.cards[aura.object_id].logical_object_id); self.finish(s, private)

    def test_actual_screams_without_legal_recipient_stays_in_graveyard(self):
        s, aura, carrier, recipient, private, bolt = self.world(4007010, 'Screams from Within')
        recipient.zone = 'outside'; s.state.players['C'].zones['battlefield'].remove(recipient.object_id)
        self.cast_aura(s, aura, carrier); self.kill_carrier(s, carrier, bolt)
        self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone); self.finish(s, private)

    def test_actual_borrowed_aura_uses_trigger_controller_and_the_printed_graveyard_restriction(self):
        for i, name in enumerate(NAMES):
            with self.subTest(card=name):
                s, aura, carrier, recipient, private, bolt = self.world(4007020 + i, name)
                theft = self.add(s.engine, 'Steal Enchantment', seat='B', zone='hand'); self.add(s.engine, 'Vedalken Orrery', seat='B')
                self.assert_closed(s, theft); self.cast_aura(s, aura, carrier)
                self.cast_for(s, 'B', theft, [aura.ref]); self.assertIsNone(self.resolve(s))
                self.assertEqual('B', s.state.cards[aura.object_id].controller); self.kill_carrier(s, carrier, bolt)
                if name == 'Screams from Within':
                    self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
                elif name == 'Ghoulish Impetus':
                    self.assertIsNone(self.resolve(s)); self.next_end_step(s); s = self.enter_aura(s, recipient, controller='B')
                    self.assertEqual('B', s.state.cards[aura.object_id].controller)
                else:
                    self.assertIsNone(self.resolve(s)); self.assertEqual('Skin Shedder', s.state.cards[aura.object_id].active_face)
                    self.assertEqual('B', s.state.cards[aura.object_id].controller)
                self.assertEqual('A', s.state.cards[aura.object_id].owner); self.finish(s, private)

    def test_actual_removed_source_or_countered_death_trigger_does_not_return(self):
        for i, response in enumerate(('Cremate', 'Stifle')):
            with self.subTest(response=response):
                s, aura, carrier, recipient, private, bolt = self.world(4007030 + i, 'Screams from Within')
                answer = self.add(s.engine, response, zone='hand'); self.cast_aura(s, aura, carrier); self.kill_carrier(s, carrier, bolt)
                target = aura.ref if response == 'Cremate' else next(t.ref for t in s.state.stack if t.source_object_id == aura.object_id)
                self.cast_for(s, 'A', answer, [target]); self.assertIsNone(self.resolve(s))
                self.assertEqual('exile' if response == 'Cremate' else 'graveyard', s.state.cards[aura.object_id].zone); self.finish(s, private)

    def test_actual_delayed_return_uses_next_end_step_once_and_can_be_countered(self):
        s, aura, carrier, recipient, private, bolt = self.world(4007040, 'Ghoulish Impetus')
        self.add(s.engine, 'Island', ref='aura-end-step-island')
        stifle = self.add(s.engine, 'Stifle', zone='hand'); self.cast_aura(s, aura, carrier); self.kill_carrier(s, carrier, bolt)
        self.assertIsNone(self.resolve(s)); s = self.replay(s, load=True); self.next_end_step(s)
        target = next(t.ref for t in s.state.stack if t.context.get('delayed_trigger_ref'))
        self.cast_for(s, 'A', stifle, [target]); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.until(s, lambda ss: ss.state.active_player == 'B' and ss.state.phase == 'beginning')
        self.next_end_step(s); self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone); self.finish(s, private)

    def test_actual_simultaneous_aura_and_creature_death_returns_the_original_transformed_card(self):
        s, aura, carrier, recipient, private, bolt = self.world(4007050, 'Skin Invasion')
        wipe = self.add(s.engine, 'Planar Cleansing', zone='hand'); self.cast_aura(s, aura, carrier)
        self.cast_for(s, 'A', wipe); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[aura.object_id].zone)
        self.assertEqual('Skin Shedder', s.state.cards[aura.object_id].active_face)
        self.assertEqual('graveyard', s.state.cards[carrier.object_id].zone); self.finish(s, private)

    def test_actual_return_fault_rolls_back_movement_attachment_and_retries(self):
        from unittest.mock import patch
        from quorune.engine import CommanderEngine
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s, aura, carrier, recipient, private, bolt = self.world(4007060, 'Screams from Within')
        self.cast_aura(s, aura, carrier); self.kill_carrier(s, carrier, bolt)
        self.assertEqual('aura.entry', self.resolve(s).kind); before = authoritative_state_hash(s.state)
        original = CommanderEngine.move_card; calls = []
        def fault(host, object_id, destination, **kwargs):
            result = original(host, object_id, destination, **kwargs)
            if object_id == aura.object_id and destination == 'battlefield':
                calls.append(True); raise GameRuleError('Attachment return rollback witness')
            return result
        command = {'action_id': 'choose', 'aura_target': recipient.ref}
        with patch.object(CommanderEngine, 'move_card', fault):
            result = s.act('pilot:A', command); self.assertFalse(result.ok); self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertTrue(calls); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); self.assertEqual(recipient.object_id, s.state.cards[aura.object_id].attached_to); self.finish(s, private)

    def test_actual_old_trigger_cannot_return_a_recast_aura_after_a_second_death(self):
        s, aura, carrier, recipient, private, bolt = self.world(4007070, 'Screams from Within')
        other = self.add(s.engine, 'Grizzly Bears', seat='C', ref='aura-second-carrier')
        regrowth = self.add(s.engine, 'Regrowth', zone='hand'); self.add(s.engine, 'Vedalken Orrery')
        second_bolt = self.add(s.engine, 'Lightning Bolt', zone='hand', ref='aura-second-bolt')
        stifle = self.add(s.engine, 'Stifle', seat='B', zone='hand'); self.cast_aura(s, aura, carrier)
        self.kill_carrier(s, carrier, bolt); self.cast_for(s, 'A', regrowth, [aura.ref]); self.resolve_top(s)
        self.cast_for(s, 'A', s.state.cards[aura.object_id], [other.ref]); self.resolve_top(s)
        self.kill_carrier(s, other, second_bolt)
        newest = next(t.ref for t in reversed(s.state.stack) if t.source_object_id == aura.object_id)
        self.cast_for(s, 'B', stifle, [newest]); self.resolve_top(s); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone); self.finish(s, private)

    def test_actual_single_faced_copy_of_skin_stays_in_graveyard_instead_of_returning_transformed(self):
        s, aura, carrier, recipient, private, bolt = self.world(4007080, 'Skin Invasion', kill_seat='C')
        self.add(s.engine, 'Mountain', seat='C', ref='physical-copy-removal-mountain')
        copier = self.add(s.engine, 'Clever Impersonator', zone='hand'); self.assert_closed(s, copier)
        self.cast_aura(s, aura, carrier); self.cast_for(s, 'A', copier)
        self.assertEqual('replacement.order', self.resolve(s).kind)
        option = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['options']
                      if aura.ref in row['id'] and not row['id'].startswith('decline:'))
        result = s.act('pilot:A', {'action_id': 'choose', 'replacement': option['id']}); self.assertTrue(result.ok, result.summary)
        s = self.enter_aura(s, recipient); self.assertEqual(recipient.object_id, s.state.cards[copier.object_id].attached_to)
        self.kill_carrier(s, recipient, bolt, actor='C'); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[copier.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[aura.object_id].zone); self.finish(s, private)

    def test_actual_copied_token_aura_queues_death_trigger_and_cannot_return(self):
        from quorune.model import TurnEntry
        s, aura, carrier, recipient, private, bolt = self.world(4007090, 'Skin Invasion', kill_seat='C')
        replication = self.add(s.engine, 'Extravagant Replication'); self.add(s.engine, 'Vedalken Orrery')
        self.add(s.engine, 'Mountain', seat='C', ref='token-aura-bolt-mountain'); self.assert_closed(s, replication)
        s.state.current_turn = TurnEntry('token-aura-initial-D', 'D'); s.state.last_normal_turn_player = 'D'
        self.prepare(s, actor='D', active='D'); s.state.players['A'].mana_pool.update({'R': 2}); self.checkpoint(s)
        self.cast_for(s, 'A', aura, [carrier.ref]); self.assertIsNone(self.resolve(s))
        self.until(s, lambda ss: ss.state.active_player == 'A' and ss.state.phase == 'beginning' and ss.state.step == 'upkeep')
        self.target(s, 'A', [aura.ref]); s = self.enter_aura(s, recipient)
        token = next(c for c in s.state.cards.values() if c.object_kind == 'token' and c.zone == 'battlefield'
                     and c.attached_to == recipient.object_id)
        self.kill_carrier(s, recipient, bolt, actor='C')
        self.assertTrue(any(t.source_object_id == token.object_id for t in s.state.stack))
        self.assertIsNone(self.resolve(s)); self.assertEqual('outside', s.state.cards[token.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[aura.object_id].zone); self.finish(s, private)

    def test_fresh_native_first_graveyard_counter_omission_mutant_is_killed(self):
        from unittest.mock import patch
        def omission(link, card):
            return card is not None and card.object_id == link.object_id and card.zone == 'graveyard'
        with patch.object(AttachmentDepartureSource, 'matches_first_graveyard', omission):
            result = unittest.TestResult()
            type(self)('test_actual_old_trigger_cannot_return_a_recast_aura_after_a_second_death').run(result)
        self.assertFalse(result.errors, result.errors)
        self.assertTrue(result.failures, 'Later Aura incarnation was accepted by the original death trigger')
