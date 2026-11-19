"""Pinned CR702.192a recurring first-resolution spell-copy contract."""
from dataclasses import FrozenInstanceError, replace
import unittest
from quorune.paradigm_model import RetainedSpellCopySnapshot


class RetainedSpellCopySnapshotTests(unittest.TestCase):
    def snapshot(self):
        return RetainedSpellCopySnapshot('fixture:lesson', 'front', {
            'name': 'Generic Lesson', 'mana_cost': '{2}{U}', 'type_line': 'Sorcery',
            'oracle_text': 'Target player draws two cards.',
            '_copiable_oracle_id': 'fixture:lesson', '_copiable_face_id': 'front',
            'ability_fragments': [{'kind': 'fixed', 'values': [1, 2]}]})

    def test_snapshot_is_independent_of_departed_physical_object_and_input_mutation(self):
        snapshot = self.snapshot(); payload = snapshot.to_dict()
        restored = RetainedSpellCopySnapshot.from_dict(payload)
        payload['characteristics']['name'] = 'Changed'
        payload['characteristics']['ability_fragments'][0]['values'].append(3)
        self.assertEqual(snapshot, restored); self.assertEqual('Generic Lesson', restored.characteristics['name'])
        self.assertEqual((1, 2), restored.characteristics['ability_fragments'][0]['values'])
        with self.assertRaises(FrozenInstanceError):restored.face_id = 'other'
        with self.assertRaises(TypeError):restored.characteristics['name'] = 'Changed'
        self.assertNotIn('object_id', restored.to_dict()); self.assertNotIn('zone_change_counter', restored.to_dict())

    def test_versions_program_face_and_characteristic_provenance_are_exact_and_closed(self):
        value = self.snapshot()
        for changes in ({'schema_version': True}, {'schema_version': 1.0}, {'schema_version': 2},
                {'oracle_id': 'other'}, {'face_id': 'other'}, {'oracle_id': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):replace(value, **changes)
        for changes in ({'name': ''}, {'mana_cost': None}, {'type_line': ''}, {'_copiable_oracle_id': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(value, characteristics={**dict(value.characteristics), **changes})
        with self.assertRaises(ValueError):RetainedSpellCopySnapshot.from_dict({**value.to_dict(), 'physical_source': 'O1'})
        with self.assertRaises(ValueError):RetainedSpellCopySnapshot.from_dict({})


class ParadigmRecurringOwnerTests(unittest.TestCase):
    def program(self, **changes):
        from types import SimpleNamespace
        from quorune.paradigm_model import PARADIGM_HANDLER
        from quorune.rules.paradigm import PARADIGM_CAPABILITIES, PARADIGM_TEMPLATE
        return SimpleNamespace(**{**dict(handlers=[{'handler_id': PARADIGM_HANDLER, 'schema_version': 1, 'event': 'resolve'}],
            coverage=['paradigm', 'spell-resolution-self-exile'],
            provenance={'template_id': PARADIGM_TEMPLATE + '+terminal-spell-self-exile-v1+fixed-body'},
            event='resolve', active_zone='stack', ability_id='spell:front', destination='exile',
            effects=[{'op': 'draw', 'player': '$controller', 'count': 2}], capability_dependencies=PARADIGM_CAPABILITIES), **changes})

    def host(self, *, first=True):
        from types import SimpleNamespace
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        ledger = SpellResolutionNameLedger()
        if not first:
            ledger, _ = ledger.begin(stack_ref='prior', controller='A', names=('Generic Lesson',))
            ledger = ledger.finish('prior')
        ledger, _ = ledger.begin(stack_ref='S1', controller='A', names=('Generic Lesson',))
        card = SimpleNamespace(zone='stack', oracle_id='fixture:lesson', annotations={})
        item = SimpleNamespace(ref='S1', kind='spell', semantic_key='fixture:lesson:spell:front',
            controller='A', card_object_id='physical', chosen_face=None)
        host = SimpleNamespace(state=SimpleNamespace(spell_resolution_name_ledger=ledger, cards={'physical': card}),
            semantics={item.semantic_key: self.program()}, semantic_program_is_current_trusted=lambda p: True)
        return host, item

    def test_descriptor_versions_event_and_fields_are_closed(self):
        from quorune.paradigm_model import PARADIGM_HANDLER
        from quorune.semantic_runtime.paradigm import ParadigmSpellDeclarationHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        handler = ParadigmSpellDeclarationHandler()
        valid = {'handler_id': PARADIGM_HANDLER, 'schema_version': 1, 'event': 'resolve'}
        self.assertEqual((), handler.lower(valid, None))
        for extra in ({'schema_version': True}, {'schema_version': 2}, {'event': 'cast'}, {'first': True}):
            with self.subTest(extra=extra), self.assertRaises(SemanticNodeError):handler.validate({**valid, **extra})

    def test_runtime_authority_cannot_drop_descriptor_marker_exile_or_lifetime_dependency(self):
        from quorune.rules.paradigm import paradigm_runtime_dependencies, require_paradigm_program
        from quorune.errors import GameRuleError
        host, _ = self.host()
        for changes in ({'handlers': []}, {'coverage': []}, {'provenance': {}}, {'destination': 'graveyard'},
                {'event': 'cast'}, {'active_zone': 'hand'}, {'effects': []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):paradigm_runtime_dependencies(self.program(**changes))
        program = self.program(capability_dependencies=())
        self.assertTrue(paradigm_runtime_dependencies(program))
        with self.assertRaises(GameRuleError):require_paradigm_program(host, program)
        host.semantic_program_is_current_trusted = lambda p: False
        with self.assertRaises(GameRuleError):require_paradigm_program(host, self.program())

    def test_first_resolution_schedules_recurring_controller_main_phase_without_a_physical_link(self):
        from unittest.mock import patch
        from quorune.rules.paradigm import schedule_paradigm_resolution
        from quorune.paradigm_model import PARADIGM_SNAPSHOT_FIELD
        host, item = self.host(); values = dict(RetainedSpellCopySnapshotTests().snapshot().characteristics)
        with patch('quorune.token_copy_faces.spell_copy_characteristics', return_value=values), \
                patch('quorune.trigger_processing.schedule_delayed_trigger') as schedule:
            schedule_paradigm_resolution(host, item)
        schedule.assert_called_once(); kw = schedule.call_args.kwargs
        self.assertFalse(kw['once']); self.assertEqual('A', kw['controller'])
        self.assertEqual('step.begin', kw['event_kind'])
        self.assertEqual({'phase': 'precombat_main', 'step': 'main', 'player': 'A'}, kw['condition'])
        self.assertNotIn('source_object_id', kw); self.assertNotIn('expires_turn_sequence', kw)
        snapshot = RetainedSpellCopySnapshot.from_dict(kw['stack_template']['context'][PARADIGM_SNAPSHOT_FIELD])
        host.state.cards.clear(); self.assertEqual('Generic Lesson', snapshot.characteristics['name'])

    def test_repeat_or_missing_history_never_creates_a_second_or_inferred_first_schedule(self):
        from unittest.mock import patch
        from quorune.rules.paradigm import schedule_paradigm_resolution
        from quorune.errors import GameRuleError
        host, item = self.host(first=False)
        with patch('quorune.trigger_processing.schedule_delayed_trigger') as schedule:
            schedule_paradigm_resolution(host, item); schedule.assert_not_called()
            host.state.spell_resolution_name_ledger = None
            with self.assertRaises(GameRuleError):schedule_paradigm_resolution(host, item)
            schedule.assert_not_called()

    def test_copy_creation_uses_retained_values_and_trigger_controller_after_original_is_gone(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from quorune.rules.paradigm import begin_paradigm_copy_cast
        from quorune.paradigm_model import PARADIGM_SNAPSHOT_FIELD, PARADIGM_TRIGGER_KEY
        snapshot = RetainedSpellCopySnapshotTests().snapshot(); copied = SimpleNamespace(object_id='copy', ref='O2')
        host = SimpleNamespace(semantics={'fixture:lesson:spell:front': self.program()},
            semantic_program_is_current_trusted=lambda p: True, _create_copy_object=Mock(return_value=copied),
            _one_shot_exile_cast_options=Mock(return_value=({'id': 'free'},)), _begin_one_shot_exile_cast_choice=Mock())
        item = SimpleNamespace(kind='triggered_ability', semantic_key=PARADIGM_TRIGGER_KEY, controller='B',
            context={PARADIGM_SNAPSHOT_FIELD: snapshot.to_dict()})
        begin_paradigm_copy_cast(host, item); args = host._create_copy_object.call_args.kwargs
        self.assertIsNone(args['source']); self.assertEqual('B', args['controller'])
        self.assertEqual('card_copy', args['object_kind']); self.assertEqual('exile', args['zone'])
        self.assertEqual(snapshot.to_dict()['characteristics'], args['characteristics'])
        host._begin_one_shot_exile_cast_choice.assert_called_once()


    def test_fresh_repeat_as_first_mutant_is_killed_by_the_independent_schedule_boundary(self):
        from unittest.mock import patch
        from quorune.spell_resolution_name_model import SpellResolutionNameFact
        result = unittest.TestResult()
        witness = type(self)('test_repeat_or_missing_history_never_creates_a_second_or_inferred_first_schedule')
        values = dict(RetainedSpellCopySnapshotTests().snapshot().characteristics)
        with patch.object(SpellResolutionNameFact, 'require_known_first', return_value=True), \
                patch('quorune.token_copy_faces.spell_copy_characteristics', return_value=values):
            witness.run(result)
        self.assertEqual([], result.errors); self.assertEqual(1, len(result.failures))
        self.assertIn('schedule_delayed_trigger', result.failures[0][1])


    def test_fresh_mutable_snapshot_mutant_is_killed_without_relying_on_an_exception(self):
        from unittest.mock import patch
        import quorune.paradigm_model as model
        witness = RetainedSpellCopySnapshotTests('test_snapshot_is_independent_of_departed_physical_object_and_input_mutation')
        result = unittest.TestResult()
        with patch.object(model, 'FrozenMap', side_effect=lambda value: dict(value)):
            witness.run(result)
        self.assertEqual([], result.errors); self.assertEqual(1, len(result.failures))

    def test_fresh_forgotten_prior_name_mutant_is_killed_by_the_second_resolution(self):
        from unittest.mock import patch
        from test_spell_resolution_names import SpellResolutionNameModelTests
        from quorune.spell_resolution_name_model import SpellResolutionNameHistory
        witness = SpellResolutionNameModelTests('test_known_empty_history_records_first_resolution_then_distinct_copy_resolution_is_not_first')
        result = unittest.TestResult()
        with patch.object(SpellResolutionNameHistory, 'has_same_name_resolution', return_value=False):
            witness.run(result)
        self.assertEqual([], result.errors); self.assertEqual(1, len(result.failures))



from inherited_composition_fixture import InheritedNativeFixture
import test_spell_resolution_names as names_fixture


class ParadigmFixture(InheritedNativeFixture):
    setUpClass = classmethod(names_fixture.SpellResolutionNameNativeTests.setUpClass.__func__)


class ParadigmCompilerTests(ParadigmFixture, unittest.TestCase):
    def test_all_five_originals_close_normally_with_the_source_spanned_keyword(self):
        from quorune.oracle_ir import compile_oracle_card
        names = ('Germination Practicum', 'Decorum Dissertation', 'Restoration Seminar',
            'Echocasting Symposium', 'Improvisation Capstone')
        for name in names:
            with self.subTest(name=name):
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
                self.assertIn('paradigm', node.mechanics)
                self.assertEqual(node.text, ir.faces[0].oracle_text[node.span.start:node.span.end])
                s = self.session(702192200 + names.index(name)); source = self.add(s.engine, name, zone='hand')
                self.assert_closed(s, source)

    def test_keyword_shape_does_not_hide_unknown_body_extra_sibling_or_duplicate_keyword(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        base = self.db.lookup('Decorum Dissertation')
        for text in ('Rewrite unrepresented history.\nParadigm', 'Target player draws two cards and loses 2 life.\nParadigm\nParadigm',
                'Target player draws two cards and loses 2 life.\nParadigm 2',
                'Target player draws two cards and loses 2 life.\nParadigm\nInvent an unknown sibling.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(replace(base, oracle_text=text),
                    capability_registry=self.registry, capability_profile='commander_review').status)


class ParadigmNativeTests(ParadigmFixture, unittest.TestCase):
    def next_recurrence(self, s):
        from quorune.paradigm_model import PARADIGM_TRIGGER_KEY
        for _ in range(600):
            if any(item.semantic_key == PARADIGM_TRIGGER_KEY for item in s.state.stack):return
            s.engine.pump(); decision = s.state.pending_decision
            self.assertIsNotNone(decision); principal = s.pending_principals()[0]
            if decision.kind == 'cleanup.discard':
                ctx = s.packet(principal, full=True)['decision']['ctx']
                command = {'action_id': 'discard', 'cards': [row['id'] for row in ctx['hand'][:ctx['count']]]}
            elif decision.kind == 'priority':command = {'action_id': 'pass'}
            elif decision.kind == 'combat.attackers':command = {'action_id': 'attack', 'attackers': {}}
            elif decision.kind == 'combat.blockers':command = {'action_id': 'block', 'blocks': {}}
            else:self.fail('Unexpected recurrence wait decision: ' + decision.kind)
            result = s.act(principal, command); self.assertTrue(result.ok, result.summary)
        self.fail('The recurring first main-phase trigger did not arrive')

    def test_actual_paid_dissertation_exiles_and_recurs_after_original_card_leaves_exile(self):
        from quorune.model import TurnEntry
        from quorune.paradigm_model import PARADIGM_TRIGGER_KEY
        s = self.session(702192210); source = self.add(s.engine, 'Decorum Dissertation', zone='hand')
        s.state.current_turn = TurnEntry('paradigm-original-turn-A', 'A'); s.state.last_normal_turn_player = 'A'
        self.assert_closed(s, source); self.prepare(s, mana={'C': 3, 'B': 2}); self.checkpoint(s)
        hand = len(s.state.players['B'].zones['hand']); self.cast_for(s, 'A', source, ['B'])
        self.assertIsNone(self.resolve(s)); self.assertEqual(hand + 2, len(s.state.players['B'].zones['hand']))
        self.assertEqual(38, s.state.players['B'].life); self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.assertEqual(1, len(s.state.delayed_triggers)); self.assertFalse(s.state.delayed_triggers[0].once)
        self.replay(s, load=True)
        # The original card's later movement cannot be recurrence authority.
        s.engine.move_card(source.object_id, 'graveyard', reason='Paradigm original departure witness')
        self.checkpoint(s)
        self.next_recurrence(s)
        self.assertEqual('A', s.state.active_player); self.assertEqual('precombat_main', s.state.phase)
        decision = self.resolve(s); self.assertIsNotNone(decision); self.assertEqual('selection.exile_cast', decision.kind)
        s = self.replay(s, load=True)
        before = len(s.state.players['C'].zones['hand'])
        result = s.act('pilot:A', {'action_id': 'cast', 'cast': {'targets': ['C']}})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual(before + 2, len(s.state.players['C'].zones['hand'])); self.assertEqual(38, s.state.players['C'].life)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(1, len(s.state.delayed_triggers)); self.replay(s, load=True)


    def test_legacy_missing_name_history_rejects_paradigm_before_cached_trust_and_preserves_plain_spells(self):
        s = self.session(702192230); spell = self.add(s.engine, 'Decorum Dissertation', zone='hand')
        plain = self.add(s.engine, 'Lightning Bolt', zone='hand')
        program = next(p for p in s.engine.semantics.programs_for_oracle(spell.oracle_id) if p.event == 'resolve')
        ordinary = next(p for p in s.engine.semantics.programs_for_oracle(plain.oracle_id) if p.event == 'resolve')
        self.assertTrue(s.engine.semantic_program_is_current_trusted(program))
        self.assertTrue(s.engine.semantic_program_is_current_trusted(ordinary))
        known = s.state.spell_resolution_name_ledger; s.state.spell_resolution_name_ledger = None
        self.assertFalse(s.engine.semantic_program_is_current_trusted(program))
        self.assertTrue(s.engine.semantic_program_is_current_trusted(ordinary))
        self.assertNotIn('spell_resolution_name_ledger', s.state.to_dict())
        s.state.spell_resolution_name_ledger = known
        self.assertTrue(s.engine.semantic_program_is_current_trusted(program))



class TargetPlayerCopyRecipientTests(unittest.TestCase):
    def template(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        return token_copy_recipe_template("Target player creates a token that's a copy of target creature you control.", source_name='Generic Copy Lesson')

    def test_explicit_two_role_schema_and_raw_ownership_do_not_depend_on_coverage(self):
        from types import SimpleNamespace
        from quorune.token_copy_recipient_model import COPY_RECIPIENT_FIELD, COPY_RECIPIENT_CAPABILITY
        from quorune.rules.token_copy_recipients import target_copy_recipient_runtime_dependencies
        template = self.template(); self.assertIsNotNone(template)
        self.assertEqual(['recipient', 'copy_source'], [g['id'] for g in template.target_schema['groups']])
        self.assertEqual('$target.0', template.effect['controller'])
        self.assertEqual(1, template.effect[COPY_RECIPIENT_FIELD]['copy_index'])
        program = SimpleNamespace(effects=[template.effect], target_schema=template.target_schema, coverage=[])
        self.assertIn(COPY_RECIPIENT_CAPABILITY, target_copy_recipient_runtime_dependencies(program))
        program.target_schema = {'count': 2, 'categories': ['permanent']}
        with self.assertRaises(ValueError):target_copy_recipient_runtime_dependencies(program)

    def test_version_slots_and_unknown_recipient_variants_fail_closed(self):
        from quorune.token_copy_recipient_model import TokenCopyRecipientBinding
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        value = TokenCopyRecipientBinding(); self.assertEqual(value, TokenCopyRecipientBinding.from_dict(value.to_dict()))
        for changes in ({'schema_version': True}, {'schema_version': 2}, {'recipient_index': 1}, {'copy_index': 0}, {'copy_index': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):TokenCopyRecipientBinding.from_dict({**value.to_dict(), **changes})
        with self.assertRaises(ValueError):TokenCopyRecipientBinding.from_dict({**value.to_dict(), 'controller': 'A'})
        for text in ("Target opponent creates a token that's a copy of target creature you control.",
                "Target player creates two tokens that are copies of target creature you control.",
                "Target player creates a token that's a copy of target creature an opponent controls.",
                "Target player creates a token that's a copy of target creature you control. Draw a card."):
            self.assertIsNone(token_copy_recipe_template(text, source_name='Generic Copy Lesson'), text)

    def test_partial_legality_never_substitutes_caster_or_the_other_target(self):
        from types import SimpleNamespace
        from quorune.rules.token_copy_recipients import resolved_copy_recipient
        effect = {**self.template().effect, 'controller': 'B'}
        self.assertEqual('B', resolved_copy_recipient(effect, SimpleNamespace(targets=['B', 'CreatureA'])))
        for targets in ([None, 'CreatureA'], ['B', None]):
            self.assertIsNone(resolved_copy_recipient(effect, SimpleNamespace(targets=targets)))
        with self.assertRaises(ValueError):resolved_copy_recipient({**effect, 'controller': 'A'}, SimpleNamespace(targets=['B', 'CreatureA']))
        with self.assertRaises(ValueError):resolved_copy_recipient(effect, SimpleNamespace(targets=['CreatureA']))

    def test_copy_authority_stays_with_caster_while_the_target_player_creates_the_token(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.token_copy_runtime import apply_copy_recipe
        item = SimpleNamespace(ref='S1', controller='A', targets=['B', 'CreatureA'], context={})
        host = SimpleNamespace(state=SimpleNamespace(stack=[item]))
        effect = {**self.template().effect, 'controller': 'B', '_runtime_source': {'stack_ref': 'S1'}}
        reference = {'ref': 'CreatureA', 'zone': 'battlefield', 'snapshot': {'characteristics': {
            'name': 'Generic Creature', 'mana_cost': '{G}', 'type_line': 'Creature - Elf', 'colors': ['G'],
            'power': '1', 'toughness': '1', 'keywords': [], 'ability_fragments': []}, 'annotations': {}}}
        with patch('quorune.token_copy_runtime.copy_reference_snapshot', return_value=reference) as read, \
                patch('quorune.token_creation.create_tokens', return_value=[]) as create:
            self.assertEqual([], apply_copy_recipe(host, effect, actor='A', reason='Independent recipient witness'))
        self.assertEqual(1, read.call_args.kwargs['target_index'])
        self.assertEqual('B', create.call_args.args[1]); self.assertEqual('CreatureA', create.call_args.kwargs['copy_of'])


    def test_fresh_caster_recipient_mutant_is_killed_by_the_creation_boundary(self):
        from unittest.mock import patch
        import quorune.rules.token_copy_recipients as owner
        witness = type(self)('test_copy_authority_stays_with_caster_while_the_target_player_creates_the_token')
        result = unittest.TestResult()
        with patch.object(owner, 'resolved_copy_recipient', return_value='A'):
            witness.run(result)
        self.assertEqual([], result.errors); self.assertEqual(1, len(result.failures))



class TargetPlayerCopyNativeTests(ParadigmFixture, unittest.TestCase):
    def test_actual_echocasting_gives_the_target_player_doubled_hexproof_copies_and_replays(self):
        import json
        from quorune.record import authoritative_state_hash
        s = self.session(702192220); spell = self.add(s.engine, 'Echocasting Symposium', zone='hand')
        creature = self.add(s.engine, 'Gladecover Scout'); doubler = self.add(s.engine, 'Parallel Lives', seat='B')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='recipient-copy-private-C')
        for source in (spell, creature, doubler):self.assert_closed(s, source)
        self.prepare(s, mana={'C': 4, 'U': 2}); self.checkpoint(s)
        self.priority(s, 'A'); action = self.action(s, 'A', spell)
        command = {'action_id': action['id'], 'pay': 'auto', 'targets': ['B', creature.ref]}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B', command).ok); self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(2, len(tokens)); self.assertEqual({'B'}, {c.controller for c in tokens})
        self.assertEqual({'B'}, {c.owner for c in tokens}); self.assertEqual('A', s.state.cards[creature.object_id].controller)
        for token in tokens:
            self.assertEqual('Gladecover Scout', s.engine._effective_card_data(token)['name'])
            self.assertIn('hexproof', s.engine._combat_keywords(token))
            self.assertEqual((1, 1), tuple(s.engine._numeric_stat(token.object_id, stat) for stat in ('power','toughness')))
        self.assertEqual('exile', s.state.cards[spell.object_id].zone)
        for seat in 'ABD':self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + seat, full=True)))
        self.replay(s, load=True)

    def test_actual_echocasting_keeps_the_player_slot_when_the_copy_target_bounces(self):
        s = self.session(702192221); spell = self.add(s.engine, 'Echocasting Symposium', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears'); response = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B'); self.assert_closed(s, spell)
        self.prepare(s, mana={'C': 4, 'U': 2}); self.checkpoint(s)
        self.cast_for(s, 'A', spell, ['B', creature.ref]); self.cast_for(s, 'B', response, [creature.ref])
        self.assertIsNone(self.resolve(s)); self.assertEqual('hand', s.state.cards[creature.object_id].zone)
        self.assertFalse(any(c.is_token and c.zone == 'battlefield' for c in s.state.cards.values()))
        self.assertEqual('exile', s.state.cards[spell.object_id].zone); self.replay(s, load=True)

    def test_fresh_native_caster_creator_mutant_is_killed_after_the_positive_paid_echo_witness(self):
        from unittest.mock import patch
        import quorune.rules.token_copy_recipients as owner
        witness = type(self)('test_actual_echocasting_gives_the_target_player_doubled_hexproof_copies_and_replays')
        result = unittest.TestResult()
        with patch.object(owner,'resolved_copy_recipient',return_value='A'):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))



class ManaValueExileProgressTests(unittest.TestCase):
    @staticmethod
    def identity(index):
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity
        return LibrarySelectionObjectIdentity('card-' + str(index), 'card-' + str(index) + '@1', 'O' + str(index))

    def test_zero_values_do_not_advance_total_and_exact_threshold_stops_on_its_first_card(self):
        from quorune.mana_value_exile_model import ManaValueExileProgress
        initial = ManaValueExileProgress(4); progress = initial
        for index, value in enumerate((0, 1, 2)):
            progress = progress.append(self.identity(index), value); self.assertFalse(progress.complete)
        final = progress.append(self.identity(3), 1)
        self.assertEqual(4, final.total); self.assertTrue(final.complete)
        self.assertEqual(['O0', 'O1', 'O2', 'O3'], [c.identity.ref for c in final.cards])
        self.assertEqual(0, initial.total); self.assertEqual((), initial.cards)
        with self.assertRaises(ValueError):final.append(self.identity(4), 0)

    def test_overshoot_stops_and_partial_or_empty_library_progress_remains_truthful(self):
        from quorune.mana_value_exile_model import ManaValueExileProgress
        empty = ManaValueExileProgress(4); self.assertFalse(empty.complete); self.assertEqual(0, empty.total)
        partial = empty.append(self.identity(0), 2); self.assertFalse(partial.complete)
        high = partial.append(self.identity(1), 7); self.assertEqual(9, high.total); self.assertTrue(high.complete)
        with self.assertRaises(ValueError):high.append(self.identity(2), 1)

    def test_codec_rejects_repeated_cards_invented_totals_late_entries_and_bad_versions(self):
        from dataclasses import replace
        from quorune.mana_value_exile_model import ManaValueExileProgress, ExiledCardManaValue
        value = ManaValueExileProgress(4).append(self.identity(0), 0).append(self.identity(1), 4)
        self.assertEqual(value, ManaValueExileProgress.from_dict(value.to_dict()))
        for changes in ({'schema_version': True}, {'schema_version': 2}, {'minimum_total': 0}, {'minimum_total': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):replace(value, **changes)
        with self.assertRaises(ValueError):ManaValueExileProgress.from_dict({**value.to_dict(), 'total': 0})
        with self.assertRaises(ValueError):ManaValueExileProgress(4, (ExiledCardManaValue(self.identity(0), 4), ExiledCardManaValue(self.identity(1), 1)))
        with self.assertRaises(ValueError):ManaValueExileProgress(4, (ExiledCardManaValue(self.identity(0), 1), ExiledCardManaValue(self.identity(0), 1)))
        for amount in (-1, True, 1.5, '2'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):ExiledCardManaValue(self.identity(2), amount)

    def test_fresh_strictly_greater_stopping_mutant_is_killed_at_the_exact_boundary(self):
        from unittest.mock import patch
        from quorune.mana_value_exile_model import ManaValueExileProgress
        witness = type(self)('test_zero_values_do_not_advance_total_and_exact_threshold_stops_on_its_first_card')
        result = unittest.TestResult()
        with patch.object(ManaValueExileProgress, 'complete', new=property(lambda p: p.total > p.minimum_total)):
            witness.run(result)
        self.assertEqual([], result.errors); self.assertEqual(1, len(result.failures))



class TotalManaExileOwnerTests(unittest.TestCase):
    @staticmethod
    def instruction():
        from quorune.fixed_top_exile import FIXED_TOP_EXILE_OPERATION
        return {'op': FIXED_TOP_EXILE_OPERATION, 'schema_version': 4, 'player': 'A',
            'scope': 'controller_total_mana_value', 'minimum_total': 4}

    def context(self, values):
        from types import SimpleNamespace
        rows = {f'O{i}': SimpleNamespace(ref=f'O{i}', object_id=f'card-{i}', logical_object_id=f'card-{i}@0',
            owner='A', token=False, mana_value=float(value), zone='library') for i,value in enumerate(values)}
        class Query:
            active_seats = ('A','B','C','D')
            def __init__(self):self.library = list(rows);self.reads = [];self.options = ()
            def library_refs(self, actor, *, top_first):
                if actor != 'A' or top_first is not True:raise AssertionError('Wrong library reader')
                return tuple(self.library)
            def object(self, ref, *, zones):
                self.reads.append(ref);row = rows.get(ref)
                return row if row is not None and row.zone in zones else None
            def authorized_cast_options(self):return self.options
        query = Query()
        return SimpleNamespace(actor='A', stack_controller='A', stack_ref='S1', query=query), rows

    def prepare(self, effect, context):
        from quorune.semantic_choices.fixed_top_exile import FixedTopExileHandler
        return FixedTopExileHandler(self.instruction()['op'], 'choice.fixed-top-exile.prepare.v1').prepare(effect, context)

    def move(self, context, rows, prep, *, destination='exile'):
        self.assertEqual(1, len(prep.preparation_intents));intent = prep.preparation_intents[0]
        self.assertEqual(context.query.library[0], intent.object_ref);self.assertEqual(0, intent.expected_zone_change_counter)
        ref = context.query.library.pop(0);rows[ref].zone = destination;rows[ref].logical_object_id = rows[ref].object_id + '@1'
        return prep.auto_continue.prepend_effects[0]

    def test_scan_uses_actual_values_including_zero_and_stops_before_reading_the_next_card(self):
        from quorune.fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION
        context, rows = self.context((0,2,2,9));effect = self.instruction()
        for _ in range(3):
            prep = self.prepare(effect, context);self.assertIsNone(prep.request)
            effect = self.move(context, rows, prep)
        final = self.prepare(effect, context);self.assertFalse(final.preparation_intents)
        offer, = final.auto_continue.prepend_effects;self.assertEqual(FIXED_TOP_EXILE_OFFER_OPERATION, offer['op'])
        self.assertEqual(['O0','O1','O2'], [r['identity']['ref'] for r in offer['results']])
        self.assertEqual(['O3'], context.query.library);self.assertNotIn('O3', context.query.reads)

    def test_replaced_exile_is_not_counted_and_empty_or_short_library_still_finishes(self):
        context, rows = self.context((9,4,8));effect = self.instruction()
        effect = self.move(context, rows, self.prepare(effect,context), destination='graveyard')
        effect = self.move(context, rows, self.prepare(effect,context))
        final = self.prepare(effect,context);offer, = final.auto_continue.prepend_effects
        self.assertEqual(['O1'], [r['identity']['ref'] for r in offer['results']]);self.assertEqual(['O2'], context.query.library)
        for values in ((),(0,1,1)):
            context, rows = self.context(values);effect = self.instruction()
            for _ in values:effect = self.move(context,rows,self.prepare(effect,context))
            final = self.prepare(effect,context);self.assertFalse(final.preparation_intents)
            self.assertEqual(len(values), len(final.auto_continue.prepend_effects[0]['results']))

    def test_wrong_incarnation_stalled_move_and_changed_controller_reject_before_more_exile(self):
        from quorune.semantic_choices.model import SemanticChoiceError
        for fault in ('incarnation','stalled','controller'):
            context, rows = self.context((1,4));prep = self.prepare(self.instruction(),context)
            if fault == 'stalled':effect = prep.auto_continue.prepend_effects[0]
            else:
                effect = self.move(context,rows,prep)
                if fault == 'incarnation':rows['O0'].logical_object_id = 'card-0@2'
                else:effect = {**dict(effect), 'player': 'B'}
            before = tuple(context.query.library)
            with self.subTest(fault=fault), self.assertRaises(SemanticChoiceError):self.prepare(effect,context)
            self.assertEqual(before, tuple(context.query.library))

    def test_old_versions_cannot_be_reinterpreted_as_the_new_instruction_or_progress(self):
        from quorune.fixed_top_exile import validate_fixed_top_exile, FIXED_TOP_EXILE_OPERATION
        initial = self.instruction();self.assertEqual(initial['scope'], validate_fixed_top_exile(initial,resolved=True))
        for version in (True,1,2,3,5,6):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_fixed_top_exile({**initial, 'schema_version':version},resolved=True)
        old = {'op':FIXED_TOP_EXILE_OPERATION,'schema_version':1,'player':'A','scope':'each_library','supplier':None,'decline':None}
        self.assertEqual('each_library',validate_fixed_top_exile(old,resolved=True))
        with self.assertRaises(ValueError):validate_fixed_top_exile({**old,'minimum_total':4},resolved=True)
        context, rows = self.context((4,));step = self.move(context,rows,self.prepare(initial,context))
        self.assertEqual(initial['scope'],validate_fixed_top_exile(step,resolved=True))
        with self.assertRaises(ValueError):validate_fixed_top_exile(step,resolved=False)
        with self.assertRaises(ValueError):validate_fixed_top_exile({**dict(step),'progress':{'schema_version':1,'minimum_total':3,'cards':[]}},resolved=True)

    def test_cast_subset_reuses_free_cast_intent_and_remaining_cards_stay_available(self):
        from types import SimpleNamespace
        from quorune.fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION
        from quorune.semantic_choices.fixed_top_exile import FixedTopExileHandler
        context, rows = self.context((2,2));effect = self.instruction()
        for _ in range(2):effect = self.move(context,rows,self.prepare(effect,context))
        offer = self.prepare(effect,context).auto_continue.prepend_effects[0]
        context.query.options = ({'id':'free-0','card':'O0'}, {'id':'free-1','card':'O1'})
        handler = FixedTopExileHandler(FIXED_TOP_EXILE_OFFER_OPERATION,'choice.fixed-top-exile.cast.v1')
        prep = handler.prepare(offer,context);continuation = SimpleNamespace(effect=prep.continuation_effect,stack_ref='S1')
        result = handler.complete(continuation,{'choice':'cast','cast':{'cost_option':'free-0'}},context.query)
        self.assertEqual('free', result.intents[0].progress.fixed_top_payment)
        self.assertEqual(['O1'],[r['identity']['ref'] for r in result.prepend_effects[0]['results']])
        declined = handler.complete(continuation,{'choice':'decline'},context.query)
        self.assertFalse(declined.intents);self.assertFalse(declined.prepend_effects)
        self.assertTrue(all(row.zone == 'exile' for row in rows.values()))


    def test_fresh_count_instead_of_mana_value_mutant_is_killed_before_the_next_card_is_exiled(self):
        from unittest.mock import patch
        import quorune.semantic_choices.mana_value_exile as owner
        original = owner._append_actual_exile
        def count_one(progress, pending, query, actor):
            row = query.object(pending.ref, zones=('exile',))
            old = row.mana_value;row.mana_value = 1
            try:return original(progress, pending, query, actor)
            finally:row.mana_value = old
        result = unittest.TestResult()
        witness = type(self)('test_scan_uses_actual_values_including_zero_and_stops_before_reading_the_next_card')
        with patch.object(owner, '_append_actual_exile', side_effect=count_one):witness.run(result)
        self.assertEqual([], result.errors);self.assertEqual(1, len(result.failures))


    def test_compiler_and_raw_dependencies_require_exact_total_value_spell_scope(self):
        from types import SimpleNamespace
        from quorune.compiler.discover_templates import discover_effect_template
        from quorune.rules.discover_capability_shapes import discover_node_capabilities
        from quorune.discover_model import discover_payload_dependencies
        text = ('Exile cards from the top of your library until you exile cards with total mana value 4 or greater. '
            'You may cast any number of spells from among them without paying their mana costs.')
        compiled = discover_effect_template(text, source_name='Generic Lesson');self.assertIsNotNone(compiled)
        _template, effects, targets, mechanics = compiled
        self.assertEqual(4, effects[0]['schema_version']);self.assertEqual(4, effects[0]['minimum_total'])
        cap = 'library.exile_until_total_mana_value.cast_subset'
        self.assertIn(cap, discover_node_capabilities(effects=effects,target_schema=targets,mechanic_ids=set(mechanics)))
        self.assertIn(cap, discover_payload_dependencies(effects[0]))
        for bad in (text.replace('mana value 4','mana value X'), text.replace('mana value 4','mana value 0'),
                text.replace('your library',"target player's library"), text.replace('may cast','may play'),
                text + ' Invent an unrepresented sibling.'):
            with self.subTest(bad=bad):self.assertIsNone(discover_effect_template(bad,source_name='Generic Lesson'))
        self.assertEqual((), discover_node_capabilities(effects=effects,target_schema={'count':1,'categories':['player']},mechanic_ids=set(mechanics)))



class TotalManaExileNativeTests(ParadigmFixture, unittest.TestCase):
    def library_top(self, s, names):
        cards = [self.add(s.engine, name, zone='library', ref='total-mana-top-' + str(i)) for i,name in enumerate(names)]
        ids = {c.object_id for c in cards};library = s.state.players['A'].zones['library']
        library[:] = [oid for oid in library if oid not in ids] + [c.object_id for c in reversed(cards)]
        return cards

    def cast_from_exile(self, s, ref, *, face=None, targets=()):
        decision = s.state.pending_decision;self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        ctx = s.packet('pilot:A', full=True)['decision']['ctx']
        options = ctx.get('cast_options') or ctx.get('context', {}).get('cast_options') or ()
        selected = next(option for option in options if option['card'] == ref and (face is None or option.get('face') == face))
        result = s.act('pilot:A', {'action_id': 'choose', 'choice': 'cast',
            'cast': {'cost_option': selected['id'], 'targets': list(targets)}})
        self.assertTrue(result.ok, result.summary)

    def test_actual_capstone_stops_at_zero_two_two_and_casts_a_subset_without_mana(self):
        import json
        from quorune.record import authoritative_state_hash
        s = self.session(702192300);source = self.add(s.engine, 'Improvisation Capstone', zone='hand')
        cards = self.library_top(s, ('Wastes','Grizzly Bears','Grizzly Bears','Lightning Bolt'))
        private = self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='capstone-private-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':5,'R':2});self.checkpoint(s)
        self.cast_for(s,'A',source);decision = self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        for card in cards[:3]:self.assertEqual('exile',s.state.cards[card.object_id].zone)
        self.assertEqual('library',s.state.cards[cards[3].object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B',{'action_id':'choose','choice':'decline'}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s = self.replay(s,load=True);self.cast_from_exile(s,cards[1].ref)
        self.assertIsNotNone(self.resolve(s));self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        result = s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[cards[1].object_id].zone)
        self.assertEqual('exile',s.state.cards[cards[2].object_id].zone)
        self.assertEqual('exile',s.state.cards[cards[0].object_id].zone)
        self.assertEqual('library',s.state.cards[cards[3].object_id].zone)
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(1,len(s.state.delayed_triggers));self.replay(s,load=True)

    def test_actual_split_card_counts_its_exile_value_and_its_chosen_face_casts_freely(self):
        s = self.session(702192301);source = self.add(s.engine,'Improvisation Capstone',zone='hand')
        creature = self.add(s.engine,'Grizzly Bears',seat='B');cards = self.library_top(s,('Far // Away','Lightning Bolt'))
        self.assert_closed(s,source);self.assert_closed(s,cards[0]);self.prepare(s,mana={'C':5,'R':2});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[cards[0].object_id].zone)
        self.assertEqual('library',s.state.cards[cards[1].object_id].zone)
        s = self.replay(s,load=True);self.cast_from_exile(s,cards[0].ref,face='Far',targets=[creature.ref])
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[creature.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[cards[0].object_id].zone)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_capstone_can_cast_both_selected_spells_before_the_parent_finishes(self):
        s = self.session(702192302);source = self.add(s.engine,'Improvisation Capstone',zone='hand')
        cards = self.library_top(s,('Grizzly Bears','Grizzly Bears','Lightning Bolt'))
        self.prepare(s,mana={'C':5,'R':2});self.checkpoint(s);self.cast_for(s,'A',source)
        self.assertIsNotNone(self.resolve(s));self.cast_from_exile(s,cards[1].ref)
        self.assertIsNotNone(self.resolve(s));self.cast_from_exile(s,cards[0].ref)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(['battlefield','battlefield'],[s.state.cards[c.object_id].zone for c in cards[:2]])
        self.assertEqual('library',s.state.cards[cards[2].object_id].zone)
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.replay(s,load=True)

    def test_actual_empty_library_finishes_without_cast_choice_and_does_not_create_a_draw_loss(self):
        s = self.session(702192303);source = self.add(s.engine,'Improvisation Capstone',zone='hand')
        for oid in tuple(s.state.players['A'].zones['library']):
            s.engine.move_card(oid,'exile',reason='Empty-library fixture preparation',log=False)
        self.prepare(s,mana={'C':5,'R':2});self.checkpoint(s);self.cast_for(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.game_over)
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(1,len(s.state.delayed_triggers));self.replay(s,load=True)

    def test_fresh_native_card_count_mutant_is_killed_by_the_actual_zero_two_two_prefix(self):
        from unittest.mock import patch
        import quorune.semantic_choices.mana_value_exile as owner
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity
        original = owner._append_actual_exile
        def count_one(progress,pending,query,actor):
            actual = original(progress,pending,query,actor)
            if len(actual.cards)==len(progress.cards):return actual
            identity=actual.cards[-1].identity
            return progress.append(LibrarySelectionObjectIdentity(identity.object_id,identity.logical_object_id,identity.ref),1)
        witness = type(self)('test_actual_capstone_stops_at_zero_two_two_and_casts_a_subset_without_mana')
        result = unittest.TestResult()
        with patch.object(owner,'_append_actual_exile',side_effect=count_one):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))

    def test_actual_second_exile_fault_restores_the_entire_prefix_and_retries(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.zone_transitions import ZoneTransitionOwner
        s = self.session(702192304);source = self.add(s.engine,'Improvisation Capstone',zone='hand')
        cards = self.library_top(s,('Grizzly Bears','Grizzly Bears','Lightning Bolt'))
        self.prepare(s,mana={'C':5,'R':2});self.checkpoint(s);self.cast_for(s,'A',source)
        for _ in range(8):
            if len(s.state.priority_passes)==len(s.engine.active_seats)-1:break
            result = s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(len(s.engine.active_seats)-1,len(s.state.priority_passes))
        before = authoritative_state_hash(s.state);principal=s.pending_principals()[0]
        original=ZoneTransitionOwner.move_card;calls=[]
        def fault(owner,object_id,destination,**kwargs):
            result=original(owner,object_id,destination,**kwargs)
            if destination=='exile' and object_id in {c.object_id for c in cards}:
                calls.append(object_id)
                if len(calls)==2:raise GameRuleError('Injected second exile fault')
            return result
        with patch.object(ZoneTransitionOwner,'move_card',fault):
            result=s.act(principal,{'action_id':'pass'});self.assertFalse(result.ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual(2,len(calls))
        self.assertEqual(['library','library','library'],[s.state.cards[c.object_id].zone for c in cards])
        result=s.act(principal,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)



class ParadigmOriginalNativeTests(ParadigmFixture, unittest.TestCase):
    def test_actual_paid_germination_is_nontargeted_and_keeps_only_controlled_creatures(self):
        s = self.session(702192400);source = self.add(s.engine,'Germination Practicum',zone='hand')
        first = self.add(s.engine,'Grizzly Bears');hexproof = self.add(s.engine,'Gladecover Scout')
        foreign = self.add(s.engine,'Grizzly Bears',seat='B',ref='germination-foreign')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
        for card in (first,hexproof):self.assertEqual(2,s.state.cards[card.object_id].counters.get('+1/+1'))
        self.assertEqual(0,s.state.cards[foreign.object_id].counters.get('+1/+1',0))
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(1,len(s.state.delayed_triggers));self.replay(s,load=True)

    def test_actual_paid_restoration_returns_only_its_original_nonland_permanent(self):
        s = self.session(702192401);source = self.add(s.engine,'Restoration Seminar',zone='hand')
        target = self.add(s.engine,'Grizzly Bears',zone='graveyard')
        land = self.add(s.engine,'Wastes',zone='graveyard',ref='restoration-unavailable-land')
        self.assert_closed(s,source);self.prepare(s,mana={'C':5,'W':2});self.checkpoint(s)
        self.priority(s,'A');row = self.action(s,'A',source)
        legal = row['target_schema'].get('legal_refs',[])
        self.assertIn(target.ref,legal);self.assertNotIn(land.ref,legal)
        self.cast_for(s,'A',source,[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[land.object_id].zone)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_countered_or_all_targets_illegal_paradigm_never_records_a_resolution(self):
        for case in ('counter','targets'):
            with self.subTest(case=case):
                s = self.session(702192410+(case=='targets'));source = self.add(s.engine,'Restoration Seminar',zone='hand')
                target = self.add(s.engine,'Grizzly Bears',zone='graveyard')
                reply = self.add(s.engine,'Counterspell',seat='B',zone='hand') if case=='counter' else self.add(s.engine,"Tormod's Crypt",seat='B')
                for i in range(2):self.add(s.engine,'Island',seat='B',ref='restoration-response-island-'+str(i))
                self.assert_closed(s,source);self.assert_closed(s,reply)
                self.prepare(s,mana={'C':5,'W':2});self.checkpoint(s);self.cast_for(s,'A',source,[target.ref])
                if case=='counter':self.cast_for(s,'B',reply,[s.state.stack[-1].ref])
                else:
                    self.priority(s,'B')
                    row = next(r for r in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+reply.ref+':'))
                    result = s.act('pilot:B',{'action_id':row['id'],'targets':['A'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertFalse(s.state.delayed_triggers)
                self.assertFalse(s.state.spell_resolution_name_ledger.history.has_same_name_resolution('A',('Restoration Seminar',)))
                self.assertFalse(s.state.spell_resolution_name_ledger.pending);self.replay(s,load=True)


class ParadigmWithdrawalTests(ParadigmFixture, unittest.TestCase):
    def test_genuinely_verified_owners_block_only_their_complete_originals_and_preserve_plain_spells(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        family = ('Germination Practicum','Decorum Dissertation','Restoration Seminar','Echocasting Symposium','Improvisation Capstone')
        for cap in ('casting.lifecycle.paradigm','history.spell_resolution.names','copy.card.retained_snapshot',
                    'token.creation.target_player_copy','library.exile_until_total_mana_value.cast_subset'):
            raw = deepcopy(original)
            next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['Independently withdrawn owner'])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            expected = ('Echocasting Symposium',) if cap=='token.creation.target_player_copy' else ('Improvisation Capstone',) if cap.startswith('library.exile_until_total') else family
            for name in family:
                status = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status
                with self.subTest(cap=cap,name=name):
                    if name in expected:self.assertNotEqual('exact',status)
                    else:self.assertEqual('exact',status)
            for name in ('Lightning Bolt','Grizzly Bears'):
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)

    def test_runtime_binding_rejects_undeclared_owners_from_complete_original_programs(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name,cap in (('Decorum Dissertation','history.spell_resolution.names'),
                ('Echocasting Symposium','token.creation.target_player_copy'),
                ('Improvisation Capstone','library.exile_until_total_mana_value.cast_subset')):
            s = self.session(702192600+len(name));source = self.add(s.engine,name,zone='hand')
            program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve')
            dependencies = tuple(v for v in program.capability_dependencies if v!=cap)
            changed = replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            binding = bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])



class ParadigmRecurrenceNativeTests(ParadigmFixture, unittest.TestCase):
    next_recurrence = ParadigmNativeTests.next_recurrence

    def test_actual_uncast_copy_is_first_and_declining_an_occurrence_preserves_later_recurrence(self):
        s = self.session(702192500);source = self.add(s.engine,'Germination Practicum',zone='hand')
        copy = self.add(s.engine,'Twincast',zone='hand');creature = self.add(s.engine,'Grizzly Bears')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'G':2,'U':2});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'U':2});self.cast_for(s,'A',copy,[s.state.stack[-1].ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(4,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertEqual(1,len(s.state.delayed_triggers))
        self.next_recurrence(s);self.assertIsNotNone(self.resolve(s))
        self.assertEqual('selection.exile_cast',s.state.pending_decision.kind)
        result = s.act('pilot:A',{'action_id':'decline','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(4,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertTrue(s.state.delayed_triggers[0].active);self.replay(s,load=True)
        self.checkpoint(s);self.next_recurrence(s);self.assertIsNotNone(self.resolve(s))
        result = s.act('pilot:A',{'action_id':'cast','cast':{}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(6,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertEqual(1,len(s.state.delayed_triggers));self.replay(s,load=True)

    def test_actual_countered_recurrence_does_not_consume_the_lifetime_trigger(self):
        s = self.session(702192501);source = self.add(s.engine,'Germination Practicum',zone='hand')
        stifle = self.add(s.engine,'Stifle',seat='B',zone='hand');self.add(s.engine,'Island',seat='B')
        creature = self.add(s.engine,'Grizzly Bears');self.assert_closed(s,source);self.assert_closed(s,stifle)
        self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s);self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
        self.next_recurrence(s);trigger = next(t for t in s.state.stack if t.semantic_key=='builtin:paradigm')
        self.cast_for(s,'B',stifle,[trigger.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertTrue(s.state.delayed_triggers[0].active);self.replay(s,load=True);self.checkpoint(s)
        self.next_recurrence(s);self.assertIsNotNone(self.resolve(s))
        result = s.act('pilot:A',{'action_id':'cast','cast':{}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(4,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertEqual(1,len(s.state.delayed_triggers));self.replay(s,load=True)

    def test_actual_exile_cast_prohibition_prevents_the_copy_and_removal_restores_a_later_occurrence(self):
        s = self.session(702192502);source = self.add(s.engine,'Germination Practicum',zone='hand')
        magistrate = self.add(s.engine,'Drannith Magistrate',seat='B');response = self.add(s.engine,'Unsummon',seat='C',zone='hand')
        self.add(s.engine,'Island',seat='C');creature = self.add(s.engine,'Grizzly Bears')
        for card in (source,magistrate,response):self.assert_closed(s,card)
        self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s);self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
        self.next_recurrence(s);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[creature.object_id].counters.get('+1/+1'));self.assertTrue(s.state.delayed_triggers[0].active)
        self.cast_for(s,'C',response,[magistrate.ref]);self.assertIsNone(self.resolve(s));self.checkpoint(s)
        self.next_recurrence(s);self.assertIsNotNone(self.resolve(s))
        result = s.act('pilot:A',{'action_id':'cast','cast':{}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(4,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.replay(s,load=True)

    def test_actual_first_resolution_completion_fault_restores_body_and_schedule_before_retry(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.spell_resolution_names as owner
        s = self.session(702192503);source = self.add(s.engine,'Germination Practicum',zone='hand')
        creature = self.add(s.engine,'Grizzly Bears');self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s);self.cast_for(s,'A',source)
        for _ in range(8):
            if len(s.state.priority_passes)==len(s.engine.active_seats)-1:break
            result = s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(len(s.engine.active_seats)-1,len(s.state.priority_passes))
        before = authoritative_state_hash(s.state);principal = s.pending_principals()[0];original = owner.finish_spell_resolution_names;calls = []
        def fault(host,item):
            original(host,item);calls.append(item.ref);raise GameRuleError('Injected Paradigm completion fault')
        with patch.object(owner,'finish_spell_resolution_names',side_effect=fault):
            result = s.act(principal,{'action_id':'pass'});self.assertFalse(result.ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual(1,len(calls));self.assertFalse(s.state.delayed_triggers)
        self.assertEqual(0,s.state.cards[creature.object_id].counters.get('+1/+1',0))
        result = s.act(principal,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,s.state.cards[creature.object_id].counters.get('+1/+1'));self.assertEqual(1,len(s.state.delayed_triggers))
        self.replay(s,load=True)

    def test_actual_free_copy_still_pays_sphere_tax_and_an_unpayable_occurrence_preserves_the_trigger(self):
        s = self.session(702192504);source = self.add(s.engine,'Germination Practicum',zone='hand')
        creature = self.add(s.engine,'Grizzly Bears');tax = self.add(s.engine,'Sphere of Resistance',seat='B')
        self.assert_closed(s,source);self.assert_closed(s,tax)
        self.prepare(s,mana={'C':4,'G':2});self.checkpoint(s);self.cast_for(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.next_recurrence(s);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertTrue(s.state.delayed_triggers[0].active)
        mana = self.add(s.engine,'Wastes',ref='paradigm-tax-payer');self.checkpoint(s)
        self.next_recurrence(s);self.assertIsNotNone(self.resolve(s))
        self.assertEqual('selection.exile_cast',s.state.pending_decision.kind)
        result = s.act('pilot:A',{'action_id':'cast','cast':{}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertTrue(s.state.cards[mana.object_id].tapped)
        self.assertEqual(4,s.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertEqual(1,len(s.state.delayed_triggers));self.replay(s,load=True)


class OneShotCopyCastLegalityTests(unittest.TestCase):
    def test_shared_offer_reads_the_canonical_static_rule_for_the_selected_exiled_face(self):
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from quorune.selection.exile_cast import OneShotExileCastChoiceOwnerMixin
        card = SimpleNamespace(zone='exile')
        record = SimpleNamespace(oracle_id='fixture:copy',type_line='Sorcery',mana_cost='{2}{G}',faces=())
        program = SimpleNamespace(target_schema=None)
        host = SimpleNamespace(card_record=lambda c:record,_cast_faces=lambda r,required_face:(None,),
            semantics={'fixture:copy:spell:front':program},semantic_program_is_current_trusted=lambda p:True,
            _cast_cost_options=Mock(return_value=({'id':'free','requirements':{}},)))
        with patch('quorune.selection.exile_cast.prospective_cast_card',return_value=card), \
                patch('quorune.rules.casting.proposal._static_cast_rule_state',return_value=('prohibited_origin',())) as gate:
            result = OneShotExileCastChoiceOwnerMixin._one_shot_exile_cast_options(host,actor='A',card=card,maximum_mana_value=None)
        self.assertEqual((),result);host._cast_cost_options.assert_not_called()
        self.assertEqual((host,'A',card),gate.call_args.args)
        self.assertEqual({'face':None,'face_name':None,'face_down':False},gate.call_args.kwargs)
        with patch('quorune.selection.exile_cast.prospective_cast_card',return_value=card), \
                patch('quorune.rules.casting.proposal._static_cast_rule_state',return_value=(None,())):
            allowed = OneShotExileCastChoiceOwnerMixin._one_shot_exile_cast_options(host,actor='A',card=card,maximum_mana_value=None)
        self.assertEqual(1,len(allowed));self.assertEqual('free',allowed[0]['id'])
