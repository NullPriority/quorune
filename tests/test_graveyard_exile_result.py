"""Pinned Shovel ruling and CR608.2c/h: a particular chosen card's type."""
from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

from quorune.graveyard_exile_result_model import GraveyardExileLifeResultSpec as Spec
from quorune.compiler.graveyard_exile_result_templates import parse_graveyard_exile_life_result as parse
from quorune.target_forms import TargetCharacteristicForm
from quorune.targeted_graveyard_exile_model import GraveyardExileChoiceSpec


class GraveyardExileLifeResultContractTests(unittest.TestCase):
    def test_fixed_creature_card_result_is_immutable_and_its_exact_codec_rejects_unowned_domains(self):
        spec = Spec(2); self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        with self.assertRaises(FrozenInstanceError):spec.amount = 3
        for amount in (True, False, 0, -1, 2.0, '2', None):
            with self.assertRaises(ValueError):Spec(amount)
        for version in (True, False, 0, 2, '1'):
            with self.assertRaises(ValueError):Spec(2, schema_version=version)
        for change in ({'card_type': 'artifact'}, {'card_type': ['creature']}, {'optional': True},
                {'controller': 'A'}, {'gain_on_success': True}, {'life_gain': 'X'}, {'schema_version': 2}):
            with self.assertRaises(ValueError):Spec.from_dict(spec.to_dict() | change)
        for missing in spec.to_dict():
            value = spec.to_dict(); del value[missing]
            with self.assertRaises(ValueError):Spec.from_dict(value)

    def test_selected_creature_card_condition_uses_existing_characteristic_form_without_a_zone_or_controller_substitution(self):
        condition = Spec(2).card_form
        self.assertIsInstance(condition, TargetCharacteristicForm)
        self.assertEqual(TargetCharacteristicForm(types_all=('creature',)), condition)
        for types, subtypes, supers, expected in (
                ({'creature'}, {'human'}, set(), True),
                ({'artifact', 'creature'}, {'golem'}, {'legendary'}, True),
                ({'artifact'}, {'creature'}, set(), False),
                ({'sorcery'}, set(), set(), False),
                ({'land'}, {'forest'}, {'basic'}, False)):
            self.assertEqual(expected, condition.matches(types=types, subtypes=subtypes, supertypes=supers))

    def test_only_complete_fixed_creature_card_life_riders_match_and_ordinary_exile_stays_separate(self):
        for subject, opponent in (('player', False), ('opponent', True)):
            for article in ('a', 'one'):
                text = f"Target {subject} exiles {article} card from their graveyard. If it's a creature card, you gain 2 life."
                self.assertEqual((opponent, Spec(2)), parse(text))
        self.assertEqual((False, Spec(12)), parse("Target player exiles a card from their graveyard. If it's a creature card, you gain 12 life"))
        for text in (
                'Target player exiles a card from their graveyard.',
                "Target player may exile a card from their graveyard. If it's a creature card, you gain 2 life.",
                "Target player exiles two cards from their graveyard. If it's a creature card, you gain 2 life.",
                "Target player exiles a card from their graveyard. If it's an artifact card, you gain 2 life.",
                "Target player exiles a card from their graveyard. If it's a creature card, they gain 2 life.",
                "Target player exiles a card from their graveyard. If it's a creature card, you gain X life.",
                "Target player exiles a card from their graveyard. If it's a creature card, you gain 0 life.",
                "Target player exiles a card from their graveyard. If it's a creature card, you gain 2 life. Draw a card.",
                "Target player exiles a card from their graveyard. If they do, you gain 2 life."):
            self.assertIsNone(parse(text), text)

    def test_existing_choice_version_one_codec_does_not_accept_a_new_result_under_its_old_encoding(self):
        old = GraveyardExileChoiceSpec(); self.assertEqual({'kind': 'one_owned_graveyard_card', 'schema_version': 1}, old.to_dict())
        self.assertEqual(old, GraveyardExileChoiceSpec.from_dict(old.to_dict()))
        with self.assertRaises(ValueError):GraveyardExileChoiceSpec.from_dict(old.to_dict() | {'result': Spec(2).to_dict()})
        with self.assertRaises(ValueError):Spec.from_dict(old.to_dict())

    def test_fresh_artifact_condition_substitution_is_killed_after_the_positive_creature_card_predicate(self):
        self.test_selected_creature_card_condition_uses_existing_characteristic_form_without_a_zone_or_controller_substitution()
        wrong = property(lambda spec: TargetCharacteristicForm(types_all=('artifact',)))
        with patch.object(Spec, 'card_form', wrong):
            with self.assertRaises(AssertionError):
                self.test_selected_creature_card_condition_uses_existing_characteristic_form_without_a_zone_or_controller_substitution()


class GraveyardExileLifeResultCommitTests(unittest.TestCase):
    def intent(self, card, **changes):
        from quorune.semantic_runtime.graveyard_exile_result_intent import GraveyardExileLifeResultIntent
        values = dict(actor='B', object_refs=(card.ref,), expected_zones=('graveyard',), destination='exile',
            reason='Selected-card result', owned_only=True, selected_identity=card.logical_object_id, result=Spec(2))
        return GraveyardExileLifeResultIntent(**(values | changes))

    def fixture(self, *, types=('creature',)):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.object_query import ObjectQueryResult
        card = CardInstance('selected', 'selected-ref', 'selected-oracle', 'Selected card', 'B', 'B', 'graveyard')
        host = SimpleNamespace(state=SimpleNamespace(cards={card.object_id: card}), types=types, observations=[])
        host._require_seat = lambda actor, **kwargs: self.assertEqual('B', actor)
        host._resolve_object = lambda actor, ref, **kwargs: card
        def public(current):
            host.observations.append(('query', current.zone))
            return ObjectQueryResult(current.object_id, current.ref, current.printed_name, current.owner,
                current.controller, current.zone, types=tuple(host.types), logical_object_id=current.logical_object_id)
        host._public_object_query_result = public
        return host, card

    def test_typed_movement_result_intent_has_a_closed_incarnation_and_replacement_identity(self):
        from dataclasses import replace
        from quorune.semantic_runtime.graveyard_exile_result_intent import validate_graveyard_exile_life_result_identity as decode
        host, card = self.fixture(); intent = self.intent(card)
        self.assertEqual(intent.identity(), decode(json.loads(json.dumps(intent.identity()))))
        for changes in ({'selected_identity': ''}, {'result': None}, {'result': Spec(2).to_dict()},
                {'owned_only': False}, {'controlled_only': True}, {'destination': 'hand'},
                {'expected_zones': ('hand',)}, {'object_refs': ()}, {'battlefield_tapped': True}):
            with self.assertRaises(ValueError):replace(intent, **changes)
        for changes in ({'extra': True}, {'actor': ''}, {'selected_identity': True},
                {'result': Spec(2).to_dict() | {'life_gain': True}}):
            with self.assertRaises(ValueError):decode(intent.identity() | changes)

    def test_canonical_movement_precedes_current_public_type_check_and_hidden_destinations_use_last_known_facts(self):
        from quorune.rules.graveyard_exile_results import commit_graveyard_exile_life_result as commit
        for before, after, destination, face_down, expected in (
                (('creature',), ('artifact',), 'exile', False, 0),
                (('artifact',), ('artifact', 'creature'), 'exile', False, 2),
                (('creature',), ('artifact',), 'hand', False, 2),
                (('artifact',), ('creature',), 'hand', False, 0),
                (('creature',), ('artifact',), 'exile', True, 2),
                (('creature',), ('land',), 'battlefield', False, 0),
                (('creature',), ('creature',), 'graveyard', False, 2),
                (('artifact',), ('artifact',), 'graveyard', False, 0)):
            with self.subTest(before=before, after=after, destination=destination, face_down=face_down):
                host, card = self.fixture(types=before); intent = self.intent(card)
                def move(current_host, base):
                    self.assertIs(host, current_host); self.assertNotIsInstance(base, type(intent))
                    self.assertTrue(base.owned_only); self.assertEqual(('graveyard',), base.expected_zones)
                    host.observations.append(('move', card.zone)); card.zone = destination
                    card.face_down = face_down; card.zone_change_counter += 1; host.types = after
                    return (card.ref,)
                with patch('quorune.semantic_choices.simultaneous_movement.commit_simultaneous_movement', side_effect=move):
                    result = commit(host, intent)
                self.assertEqual([('query', 'graveyard'), ('move', 'graveyard')], host.observations[:2])
                if expected:
                    self.assertEqual([{'op': 'life', 'player': '$controller', 'delta': expected}], [dict(e) for e in result.effects])
                else:self.assertEqual((), result.effects)
                if destination == 'hand' or face_down:self.assertEqual(2, len(host.observations))

    def test_changed_incarnation_and_noncard_are_rejected_before_any_canonical_move(self):
        from quorune.errors import GameRuleError
        from quorune.rules.graveyard_exile_results import commit_graveyard_exile_life_result as commit
        host, card = self.fixture(); intent = self.intent(card); card.zone_change_counter += 1
        with patch('quorune.semantic_choices.simultaneous_movement.commit_simultaneous_movement') as movement:
            with self.assertRaises(GameRuleError):commit(host, intent)
            movement.assert_not_called()
        host, card = self.fixture(); intent = self.intent(card); card.is_token = True; card.object_kind = 'token'
        with patch('quorune.semantic_choices.simultaneous_movement.commit_simultaneous_movement') as movement:
            with self.assertRaises(GameRuleError):commit(host, intent)
            movement.assert_not_called()


class GraveyardExileLifeResultIntegrationTests(unittest.TestCase):
    def prepared(self):
        from test_targeted_graveyard_exile import TargetedGraveyardExileChoiceTests as Old
        from quorune.semantic_choices.targeted_graveyard_exile import TargetedGraveyardExileChoiceHandler as Handler
        old = Old('test_target_owner_chooses_one_current_public_card_and_uses_canonical_owned_zone_intent')
        spec = GraveyardExileChoiceSpec(schema_version=2, result=Spec(2))
        prepared = Handler().prepare(spec.effect(player='B'), old.context())
        continuation = old.continuation(prepared.continuation_effect)
        return old, Handler(), prepared, continuation

    def test_additive_choice_two_requires_its_typed_result_and_keeps_one_encoding_byte_compatible(self):
        old = GraveyardExileChoiceSpec(); before = json.dumps(old.to_dict(), sort_keys=True)
        new = GraveyardExileChoiceSpec(schema_version=2, result=Spec(2))
        self.assertEqual(new, GraveyardExileChoiceSpec.from_dict(json.loads(json.dumps(new.to_dict()))))
        self.assertEqual(new, GraveyardExileChoiceSpec.from_effect(new.effect()))
        self.assertEqual(before, json.dumps(old.to_dict(), sort_keys=True)); self.assertNotIn('result', old.to_dict())
        for changes in ({'schema_version': 1}, {'schema_version': 3}, {'schema_version': True}, {'result': None},
                {'result': Spec(2).to_dict() | {'card_type': 'artifact'}}, {'optional': True}):
            with self.assertRaises(ValueError):GraveyardExileChoiceSpec.from_dict(new.to_dict() | changes)
        for changes in ({'schema_version': 2}, {'result': Spec(2)}, {'schema_version': 2, 'result': Spec(2).to_dict()}):
            from dataclasses import replace
            with self.assertRaises(ValueError):replace(old, **changes)

    def test_exact_full_template_and_raw_program_derive_fine_and_life_owners_without_coverage(self):
        from types import SimpleNamespace
        from quorune.compiler.targeted_graveyard_exile_templates import targeted_graveyard_exile_template
        from quorune.graveyard_exile_result_model import GRAVEYARD_EXILE_LIFE_RESULT_CAPABILITY as cap
        from quorune.rules.targeted_graveyard_exile import (DEPENDENCIES, targeted_graveyard_exile_node_capabilities,
            targeted_graveyard_exile_payload_dependencies, targeted_graveyard_exile_runtime_dependencies)
        compiled = targeted_graveyard_exile_template("Target player exiles a card from their graveyard. If it's a creature card, you gain 2 life.")
        self.assertEqual('target-player-graveyard-exile-choice-result-v2', compiled[0])
        self.assertEqual(GraveyardExileChoiceSpec(schema_version=2, result=Spec(2)), GraveyardExileChoiceSpec.from_effect(compiled[1][0]))
        expected = (*DEPENDENCIES, cap, 'life.change.effect')
        self.assertEqual(expected, targeted_graveyard_exile_node_capabilities(effects=compiled[1], target_schema=compiled[2]))
        self.assertEqual(expected, targeted_graveyard_exile_payload_dependencies(compiled[1][0]))
        self.assertEqual(expected, targeted_graveyard_exile_runtime_dependencies(SimpleNamespace(
            effects=compiled[1], handlers=(), target_schema=compiled[2], coverage=(), capability_dependencies=())))
        self.assertEqual(DEPENDENCIES, targeted_graveyard_exile_payload_dependencies(GraveyardExileChoiceSpec().effect()))

    def test_actual_typed_choice_handler_keeps_target_owner_and_emits_only_one_sealed_result_intent(self):
        from quorune.semantic_runtime.graveyard_exile_result_intent import GraveyardExileLifeResultIntent
        old, handler, prepared, continuation = self.prepared()
        self.assertEqual(('one', 'two'), prepared.request.choice.legal_refs)
        completion = handler.complete(continuation, {'objects': ['two']}, old.query())
        intent, = completion.intents; self.assertIsInstance(intent, GraveyardExileLifeResultIntent)
        self.assertEqual(('B', ('two',), 'two@2', Spec(2)), (intent.actor, intent.object_refs, intent.selected_identity, intent.result))
        self.assertEqual((), completion.prepend_effects)
        empty = handler.prepare(GraveyardExileChoiceSpec(schema_version=2, result=Spec(2)).effect(player='B'), old.context(old.query(())))
        self.assertIsNotNone(empty.auto_continue); self.assertEqual((), empty.auto_continue.prepend_effects)

    def test_replacement_and_remaining_choice_codecs_preserve_selected_incarnation_and_nested_result(self):
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity, with_replacement_selections
        from quorune.semantic_choices.completed_choice import encode_remaining_completion, decode_remaining_completion
        old, handler, prepared, continuation = self.prepared()
        completion = handler.complete(continuation, {'objects': ['one']}, old.query()); intent, = completion.intents
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual('graveyard_exile_life_result', kind); self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))
        self.assertEqual(identity, with_replacement_selections(intent, ()).identity())
        value = encode_remaining_completion(continuation, 'B', {'objects': ['one']}, completion, 0)
        loaded = decode_remaining_completion(json.loads(json.dumps(value)))
        self.assertEqual('B', loaded[1]); self.assertEqual(completion, loaded[3])
        self.assertEqual(continuation.to_dict(), loaded[0].to_dict())

    def test_only_exact_final_selected_card_parent_can_prepend_its_committed_life_followup(self):
        from dataclasses import replace
        from quorune.rules.committed_quantity_followups import CommittedQuantityFollowup
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.counter_coordination import _with_committed_quantity_followup
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
        old, handler, prepared, continuation = self.prepared()
        completion = handler.complete(continuation, {'objects': ['one']}, old.query()); intent, = completion.intents
        result = CommittedQuantityFollowup((FrozenMap({'op': 'life', 'player': '$controller', 'delta': 2}),))
        changed = _with_committed_quantity_followup(completion, intent, 0, result)
        self.assertEqual(result.effects, changed.prepend_effects)
        self.assertEqual(completion, _with_committed_quantity_followup(completion, intent, 0, ('one',)))
        base = MoveObjectsSimultaneouslyIntent(actor='B', object_refs=('one',), expected_zones=('graveyard',), destination='exile', reason='Old movement', owned_only=True)
        with self.assertRaises(SemanticChoiceError):_with_committed_quantity_followup(completion, base, 0, result)
        with self.assertRaises(SemanticChoiceError):_with_committed_quantity_followup(replace(completion, intents=(intent, intent)), intent, 0, result)


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_targeted_graveyard_exile as graveyard_native


class GraveyardExileLifeResultNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = graveyard_native.TargetedGraveyardExileNativeTests.start
    reject = graveyard_native.TargetedGraveyardExileNativeTests.reject
    finish = graveyard_native.TargetedGraveyardExileNativeTests.finish
    activate_choice = graveyard_native.TargetedGraveyardExileNativeTests.activate_choice
    choose_exile = graveyard_native.TargetedGraveyardExileNativeTests.choose_exile

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [c for c in base['cards'] if c['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-result-cards.json'], folder / 'selected-result.sqlite3')
        cls.db = CardDatabase(folder / 'selected-result.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Selected graveyard card result', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def test_complete_shovel_original_and_material_costs_admit_normally_and_actual_result_minima_cannot_be_removed(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.graveyard_exile_result_model import GRAVEYARD_EXILE_LIFE_RESULT_CAPABILITY as cap
        s, _ = self.start(737000); source = self.add(s.engine, 'Graveyard Shovel', zone='hand')
        self.assert_closed(s, source)
        self.assertEqual('exact', compile_oracle_card(self.db.lookup('Graveyard Shovel'), capability_registry=self.registry,
            capability_profile='commander_review').status)
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        for missing in (cap, 'life.change.effect', 'choice.affected_player.graveyard_exile'):
            dependencies = tuple(c for c in program.capability_dependencies if c != missing)
            raw = replace(program, coverage=(), capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(raw, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict']); self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])
        record = self.db.lookup('Phyrexian Furnace'); parent = compile_best_available_card_program(self.db, record,
            semantic_registry=s.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(parent, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def test_actual_printed_two_cast_and_two_tap_choose_only_the_target_owners_current_card_then_apply_its_type_result(self):
        for index, choice in enumerate(('creature', 'noncreature', 'empty')):
            with self.subTest(choice=choice):
                s, private = self.start(737010 + index); source = self.add(s.engine, 'Graveyard Shovel', zone='hand')
                if choice != 'empty':
                    creature = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                    land = self.add(s.engine, 'Forest', seat='B', zone='graveyard')
                    for card in (creature, land):self.assert_closed(s, card)
                self.assert_closed(s, source); self.prepare(s, mana={'C': 4}); self.checkpoint(s)
                before = sum(s.state.players['A'].mana_pool.values()); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
                self.assertEqual(before - 2, sum(s.state.players['A'].mana_pool.values()))
                self.activate_choice(s, source); self.assertTrue(s.state.cards[source.object_id].tapped)
                self.assertEqual(before - 4, sum(s.state.players['A'].mana_pool.values())); pending = self.resolve(s)
                if choice == 'empty':
                    self.assertIsNone(pending)
                else:
                    self.assertIsNotNone(pending); self.assertEqual(('B',), tuple(pending.actors))
                    selected = creature if choice == 'creature' else land
                    other = land if choice == 'creature' else creature
                    s = self.replay(s, load=True); self.choose_exile(s, s.state.cards[selected.object_id].ref)
                    self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[selected.object_id].zone)
                    self.assertEqual('graveyard', s.state.cards[other.object_id].zone)
                self.assertEqual((42 if choice == 'creature' else 40, 40, 40, 40),
                    tuple(s.state.players[seat].life for seat in 'ABCD'))
                self.finish(s, private)

    def test_actual_result_life_uses_the_resolving_controller_and_existing_controller_relative_gain_replacement(self):
        for index, replacement_controller in enumerate(('A', 'C')):
            with self.subTest(replacement_controller=replacement_controller):
                s, private = self.start(737020 + index); source = self.add(s.engine, 'Graveyard Shovel', zone='hand')
                creature = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                replacement = self.add(s.engine, 'Rhox Faithmender', seat=replacement_controller)
                for card in (source, creature, replacement):self.assert_closed(s, card)
                self.prepare(s, mana={'C': 4}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
                self.activate_choice(s, source); self.assertIsNotNone(self.resolve(s)); self.choose_exile(s, creature.ref)
                self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[creature.object_id].zone)
                self.assertEqual((44 if replacement_controller == 'A' else 42, 40, 40, 40),
                    tuple(s.state.players[seat].life for seat in 'ABCD'))
                self.finish(s, private)


    def test_actual_source_removal_resets_source_control_but_the_original_controller_gets_the_ground_seal_choice_result(self):
        from quorune.control_effects import change_control
        s, private = self.start(737030); source = self.add(s.engine, 'Graveyard Shovel')
        change_control(s.engine, source.object_id, 'C', reason='Prepared borrowed artifact source')
        creature = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
        seal = self.add(s.engine, 'Ground Seal', seat='D'); removal = self.add(s.engine, 'Disenchant', zone='hand')
        for card in (source, creature, seal, removal):self.assert_closed(s, card)
        s.state.players['C'].mana_pool.update({'C': 2}); self.prepare(s, mana={'W': 2}); self.checkpoint(s)
        self.activate_choice(s, source, seat='C'); self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(s.state.players['C'].mana_pool.values()))
        self.cast(s, 'A', removal, [source.ref]); self.resolve_top(s)
        self.assertEqual(('graveyard', 'A', 'A'), (s.state.cards[source.object_id].zone,
            s.state.cards[source.object_id].owner, s.state.cards[source.object_id].controller))
        self.assertEqual('graveyard', s.state.cards[creature.object_id].zone)
        self.assertIsNotNone(self.resolve(s)); s = self.replay(s, load=True); self.choose_exile(s, creature.ref)
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[creature.object_id].zone)
        self.assertEqual((40, 40, 42, 40), tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.finish(s, private)

    def test_actual_chosen_commander_exile_and_result_life_finish_before_its_owner_state_based_command_choice(self):
        s, private = self.start(737031); source = self.add(s.engine, 'Graveyard Shovel', zone='hand')
        commander_id = s.state.players['B'].zones['command'][0]; commander = s.state.cards[commander_id]
        s.engine.move_card(commander_id, 'graveyard', reason='Prepared current selected-card commander boundary')
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        self.assertTrue(s.engine._stabilize()); self.assertEqual('state.commander_zone', s.state.pending_decision.kind)
        kept = s.act('pilot:B', {'action_id': 'remain'}); self.assertTrue(kept.ok, kept.summary)
        self.assert_closed(s, source); self.prepare(s, mana={'C': 4}); self.checkpoint(s)
        self.cast(s, 'A', source); self.assertIsNone(self.resolve(s)); self.activate_choice(s, source)
        self.assertIsNotNone(self.resolve(s)); self.choose_exile(s, commander.ref)
        self.assertEqual('exile', s.state.cards[commander_id].zone); self.assertEqual(42, s.state.players['A'].life)
        self.assertEqual('state.commander_zone', s.state.pending_decision.kind)
        self.assertEqual(('B',), tuple(s.state.pending_decision.actors)); s = self.replay(s, load=True)
        self.reject(s, 'A', {'action_id': 'command'}); result = s.act('pilot:B', {'action_id': 'command'})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual('command', s.state.cards[commander_id].zone); self.assertEqual(42, s.state.players['A'].life)
        self.finish(s, private)

    def test_actual_post_life_commit_fault_restores_full_exile_life_choice_and_private_state_then_loads_and_retries(self):
        import quorune.effect_runtime.life_effects as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s, private = self.start(737032); source = self.add(s.engine, 'Graveyard Shovel', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
        for card in (source, creature):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 4}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.activate_choice(s, source); self.assertIsNotNone(self.resolve(s))
        before = authoritative_state_hash(s.state); original = owner._commit; observed = []
        def fault(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append((s.state.cards[creature.object_id].zone, s.state.players['A'].life))
            raise GameRuleError('Injected after selected-card exile and committed result life')
        command = {'action_id': 'choose', 'objects': [creature.ref]}
        with patch.object(owner, '_commit', side_effect=fault):result = s.act('pilot:B', command)
        self.assertFalse(result.ok); self.assertIn('Injected after selected-card', result.summary)
        self.assertEqual([('exile', 42)], observed); self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertEqual('graveyard', s.state.cards[creature.object_id].zone); self.assertEqual(40, s.state.players['A'].life)
        self.assertTrue(s.state.cards[source.object_id].tapped); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True); self.reject(s, 'A', command); self.choose_exile(s, s.state.cards[creature.object_id].ref)
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[creature.object_id].zone)
        self.assertEqual(42, s.state.players['A'].life); self.finish(s, private)

    def noncreature_result_journey(self, seed):
        s, private = self.start(seed); source = self.add(s.engine, 'Graveyard Shovel', zone='hand')
        land = self.add(s.engine, 'Forest', seat='B', zone='graveyard')
        for card in (source, land):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 4}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.activate_choice(s, source); self.assertIsNotNone(self.resolve(s)); self.choose_exile(s, land.ref)
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[land.object_id].zone)
        self.assertEqual(40, s.state.players['A'].life); self.finish(s, private)

    def test_fresh_native_land_for_creature_condition_substitution_is_killed_after_a_positive_real_action_journey(self):
        self.noncreature_result_journey(737040)
        wrong = property(lambda spec: TargetCharacteristicForm(types_all=('land',)))
        with patch.object(Spec, 'card_form', wrong):
            with self.assertRaises(AssertionError):self.noncreature_result_journey(737041)

    def test_genuinely_verified_result_life_and_choice_owner_withdrawals_block_only_dependent_complete_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.graveyard_exile_result_model import GRAVEYARD_EXILE_LIFE_RESULT_CAPABILITY as cap
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        def ready(name, registry):
            card = self.db.lookup(name); sem = SemanticRegistry()
            register_generated_programs(self.db, sem, (card,), capability_registry=registry, capability_profile='commander_review',
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            parent = compile_best_available_card_program(self.db, card, semantic_registry=sem,
                capability_registry=registry, capability_profile='commander_review')
            return bind_card_program_runtime(parent, capability_registry=registry, profile='commander_review')['strict_capability_ready']
        baseline = CapabilityRegistry(deepcopy(raw)); index = build_index(registry_value=raw, declaration_source=doc, discovered_test_ids=known)
        baseline.mark_evidence_verified(validate_capability_evidence_index(index, registry=baseline))
        self.assertTrue(ready('Graveyard Shovel', baseline))
        for missing in (cap, 'life.change.effect', 'choice.affected_player.graveyard_exile'):
            changed = deepcopy(raw); next(row for row in changed['capabilities'] if row['id'] == missing).update(
                status='blocked', blockers=['Selected-card result or its canonical parent withdrawn'])
            registry = CapabilityRegistry(changed); current = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(current, registry=registry))
            status = registry.closure((missing,), profile='commander_review'); self.assertFalse(status.trusted)
            self.assertIn('status:' + missing + ':blocked', status.blockers); self.assertFalse(any('unverified' in b for b in status.blockers))
            self.assertFalse(ready('Graveyard Shovel', registry))
            self.assertEqual(missing != 'choice.affected_player.graveyard_exile', ready('Scrabbling Claws', registry))
            for name in ('Cremate', 'Grizzly Bears', 'Forest'):self.assertTrue(ready(name, registry), (missing, name))


if __name__ == '__main__':unittest.main()
