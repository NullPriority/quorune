"""Independent affected-player choices for public graveyard exile."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest

from quorune.targeted_graveyard_exile_model import GraveyardExileChoiceSpec as Spec
from quorune.compiler.targeted_graveyard_exile_templates import targeted_graveyard_exile_template as compile_choice
from quorune.rules.targeted_graveyard_exile import (
    DEPENDENCIES, graveyard_exile_player_target, targeted_graveyard_exile_node_capabilities,
    targeted_graveyard_exile_runtime_dependencies, is_closed_targeted_graveyard_exile_program,
)
from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery, SemanticChoiceContext
from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame, SemanticChoiceError
from quorune.semantic_choices.targeted_graveyard_exile import TargetedGraveyardExileChoiceHandler as Handler


class TargetedGraveyardExileContractTests(unittest.TestCase):
    def test_descriptor_round_trip_and_unknown_versions_roles_and_fields_fail_closed(self):
        spec = Spec(); self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        with self.assertRaises(FrozenInstanceError):spec.schema_version = 2
        for raw in (spec.to_dict() | {'schema_version': v} for v in (True, 0, 2, '1')):
            with self.assertRaises(ValueError):Spec.from_dict(raw)
        for extra in ({'kind': 'two_cards'}, {'count': 2}, {'predicate': {}}, {'optional': True}):
            with self.assertRaises(ValueError):Spec.from_dict(spec.to_dict() | extra)
        for change in ({'player': '$controller'}, {'target': '$target.0'}, {'then': []}, {'selection': {'schema_version': 1}}):
            with self.assertRaises(ValueError):Spec.from_effect(spec.effect() | change)

    def test_exact_player_and_opponent_grammar_retains_one_player_target_and_non_target_card_choice(self):
        for subject in ('player', 'opponent'):
            result = compile_choice('Target '+subject+' exiles a card from their graveyard.')
            self.assertIsNotNone(result); self.assertEqual((Spec().effect(),), result[1])
            self.assertEqual(graveyard_exile_player_target(opponent=subject == 'opponent'), result[2])
            self.assertEqual(DEPENDENCIES, targeted_graveyard_exile_node_capabilities(effects=result[1], target_schema=result[2]))
        for bad in ('Target player may exile a card from their graveyard.',
                'Target player exiles two cards from their graveyard.', 'Target player exiles a creature card from their graveyard.',
                'Target player exiles a card from your graveyard.', 'Exile the bottom card of target player\'s graveyard.',
                'Target player exiles a card from their graveyard. If it is a creature card, you gain 2 life.'):
            self.assertIsNone(compile_choice(bad))

    def program(self):
        return SimpleNamespace(effects=(Spec().effect(),), target_schema=graveyard_exile_player_target(),
            handlers=(), coverage=(), capability_dependencies=DEPENDENCIES)

    def test_runtime_infers_owners_without_coverage_and_requires_the_exact_direct_target_role(self):
        program = self.program(); self.assertEqual(DEPENDENCIES, targeted_graveyard_exile_runtime_dependencies(program))
        self.assertTrue(is_closed_targeted_graveyard_exile_program(program))
        for missing in DEPENDENCIES:
            changed = SimpleNamespace(**(vars(program) | {'capability_dependencies': tuple(x for x in DEPENDENCIES if x != missing)}))
            self.assertFalse(is_closed_targeted_graveyard_exile_program(changed))
        for change in ({'target_schema': None}, {'target_schema': {'zones': ['graveyard'], 'count': 1}},
                {'target_schema': graveyard_exile_player_target() | {'count': True}},
                {'effects': ({'op': 'offer_optional_effect', 'effects': program.effects},)},
                {'effects': (Spec().effect(player='$controller'),)}):
            with self.assertRaises(ValueError):targeted_graveyard_exile_runtime_dependencies(SimpleNamespace(**(vars(program) | change)))


class TargetedGraveyardExileChoiceTests(unittest.TestCase):
    def row(self, ref, *, owner='B', zone='graveyard', token=False, logical=None):
        return ObjectQueryResult(object_id=ref, ref=ref, printed_name='Public '+ref, owner=owner, controller=owner,
            zone=zone, token=token, logical_object_id=logical or ref+'@2')

    def query(self, rows=None):
        rows = tuple(rows if rows is not None else (self.row('one'), self.row('two'), self.row('foreign', owner='A'),
            self.row('hand', zone='hand'), self.row('token', token=True)))
        return SnapshotSemanticChoiceQuery(seat_order=('A', 'B', 'C'), active_order=('A', 'B', 'C'),
            object_rows=rows, materialized_choice_candidates=tuple(row.ref for row in rows
                if row.owner == 'B' and row.zone == 'graveyard' and not row.token))

    def context(self, query=None, actor='B'):
        return SemanticChoiceContext(actor=actor, stack_ref='stack', stack_controller='A', stack_label='Public graveyard exile',
            source_ref='source', card_ref=None, semantic_program_id='program', semantic_program_version=1,
            query=query or self.query())

    def prepared(self, query=None):
        return Handler().prepare(Spec().effect(player='B'), self.context(query))

    def continuation(self, effect):
        return SemanticChoiceContinuation(handler_id=Handler().handler_id, handler_version=1, stack_ref='stack',
            effect=effect, remaining=(), destination=None, note='Exile choice', semantic_frame=SemanticChoiceFrame(
                semantic_program_id='program', semantic_program_version=1, stack_object='stack', instruction_pointer=0, controller='A'))

    def test_canonical_public_materialization_uses_owner_and_excludes_tokens_copies_and_other_zones(self):
        from quorune.rules.targeted_graveyard_exile import targeted_graveyard_exile_candidates
        rows = (replace(self.row('creature'), controller='A', types=('creature',)), self.row('land'),
            self.row('instant'), self.row('foreign', owner='A'), self.row('hidden', zone='hand'),
            self.row('exiled', zone='exile'), self.row('token', token=True), self.row('spell-copy'))
        cards = {r.object_id: SimpleNamespace(is_card_object=not r.token and r.ref != 'spell-copy') for r in rows}
        state = SimpleNamespace(cards=cards, players=dict.fromkeys('ABCD'), active_seats=lambda:tuple('ABCD'))
        host = SimpleNamespace(state=state, public_zone_move_object_rows=lambda actor:rows,
            public_zone_move_active_seats=lambda:tuple('ABCD'), public_zone_move_apnap_order=lambda:tuple('CDAB'))
        self.assertEqual(('creature', 'instant', 'land'), targeted_graveyard_exile_candidates(host, 'B', Spec().effect(player='B')))
        continuation = {**Spec().effect(player='B'), '_choice_actor': 'B', '_stack_label': 'Exile choice',
            '_graveyard_candidates': ()}
        self.assertEqual(('creature', 'instant', 'land'), targeted_graveyard_exile_candidates(host, 'B', continuation))
        for extra in ({'_choice_actor': 'A'}, {'unowned': True}):
            with self.assertRaises(ValueError):targeted_graveyard_exile_candidates(host, 'B', continuation | extra)
        with self.assertRaises(ValueError):targeted_graveyard_exile_candidates(host, 'Z', Spec().effect(player='Z'))
        state.active_seats = lambda:tuple('ACD')
        self.assertEqual((), targeted_graveyard_exile_candidates(host, 'B', Spec().effect(player='B')))

    def test_target_owner_chooses_one_current_public_card_and_uses_canonical_owned_zone_intent(self):
        prepared = self.prepared(); choice = prepared.request.choice
        self.assertEqual(('one', 'two'), choice.legal_refs); self.assertEqual((1, 1, False), (choice.minimum, choice.maximum, choice.optional))
        completion = Handler().complete(self.continuation(prepared.continuation_effect), {'objects': ['two']}, self.query())
        intent, = completion.intents
        self.assertEqual(('B', ('two',), ('graveyard',), 'exile', True),
            (intent.actor, intent.object_refs, intent.expected_zones, intent.destination, intent.owned_only))
        accepted = Handler().complete(self.continuation(prepared.continuation_effect),
            {'action': 'choose', 'action_id': 'choose', 'objects': ['two'], 'choice_schema': {'field': 'objects'}}, self.query())
        self.assertEqual(completion.intents, accepted.intents)

    def test_empty_graveyard_auto_continues_but_unknown_or_wrong_actor_rejects(self):
        self.assertIsNotNone(self.prepared(self.query(())).auto_continue)
        departed = replace(self.query(), active_order=('A', 'C'))
        self.assertIsNotNone(self.prepared(departed).auto_continue)
        with self.assertRaises(SemanticChoiceError):Handler().prepare(Spec().effect(player='D'), self.context(actor='D'))
        with self.assertRaises(SemanticChoiceError):Handler().prepare(Spec().effect(player='A'), self.context())

    def test_forged_count_owner_zone_and_client_player_overrides_reject_without_intents(self):
        continuation = self.continuation(self.prepared().continuation_effect)
        for response in ({}, {'objects': []}, {'objects': ['one', 'two']}, {'objects': ['one', 'one']},
                {'objects': ['foreign']}, {'objects': ['hand']}, {'objects': ['token']},
                {'objects': ['one'], 'player': 'A'}, {'objects': 'one'}):
            with self.subTest(response=response), self.assertRaises(SemanticChoiceError):Handler().complete(continuation, response, self.query())

    def test_changed_candidate_incarnation_owner_membership_and_continuation_fail_closed(self):
        effect = self.prepared().continuation_effect; continuation = self.continuation(effect)
        for rows in ((self.row('one', logical='one@4'), self.row('two')),
                (self.row('one', owner='A'), self.row('two')),
                (self.row('one', zone='hand'), self.row('two')),
                (self.row('one'),), (self.row('one'), self.row('two'), self.row('new'))):
            with self.assertRaises(SemanticChoiceError):Handler().complete(continuation, {'objects': ['one']}, self.query(rows))
        for extra in ({'_choice_actor': 'A'}, {'_graveyard_candidates': []}, {'then': []}, {'_stack_label': ''}):
            with self.assertRaises(SemanticChoiceError):Handler().complete(self.continuation(dict(effect) | extra), {'objects': ['one']}, self.query())

    def test_fresh_source_controller_instead_of_target_owner_mutant_is_killed(self):
        import quorune.semantic_choices.targeted_graveyard_exile as owner
        self.test_target_owner_chooses_one_current_public_card_and_uses_canonical_owned_zone_intent()
        original = owner._candidates
        with patch.object(owner, '_candidates', lambda query, actor:
                original(replace(query, materialized_choice_candidates=('foreign',)), 'A')):
            with self.assertRaises(AssertionError):
                self.test_target_owner_chooses_one_current_public_card_and_uses_canonical_owned_zone_intent()


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers

ORIGINALS = ('Scrabbling Claws', 'Relic of Progenitus', 'Merrow Bonegnawer', 'Grave Birthing')


class TargetedGraveyardExileNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [c for c in base['cards'] if c['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json'], folder / 'chosen-graveyard.sqlite3')
        cls.db = CardDatabase(folder / 'chosen-graveyard.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Affected graveyard owner', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def test_complete_originals_and_material_siblings_bind_normally_and_require_choice_owners(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s, _ = self.start(735000)
        for name in ORIGINALS:
            card = self.add(s.engine, name, zone='hand'); self.assert_closed(s, card)
            record = self.db.lookup(name)
            self.assertEqual('exact', compile_oracle_card(record, capability_registry=self.registry,
                capability_profile='commander_review').status)
            program = next(p for p in s.engine.semantics.programs_for_oracle(card.oracle_id)
                if any(e.get('op') == Handler().operation for e in p.effects))
            for missing in DEPENDENCIES:
                deps = tuple(c for c in program.capability_dependencies if c != missing)
                changed = replace(program, coverage=(), capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'], (name, missing))
                self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])
        for name in ('Phyrexian Furnace',):
            record = self.db.lookup(name); card = self.add(s.engine, name, zone='hand')
            parent = compile_best_available_card_program(self.db, record, semantic_registry=s.engine.semantics,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertFalse(bind_card_program_runtime(parent, capability_registry=self.registry,
                profile='commander_review')['strict_capability_ready'], name)

    def activate_choice(self, s, source, target='B', seat='A'):
        self.priority(s, seat); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':') and 'target player exiles' in a['label'].casefold())
        command = {'action_id': action['id'], 'targets': [target], 'pay': 'auto'}
        self.reject(s, 'D' if seat != 'D' else 'C', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def choose_exile(self, s, ref, *, seat='B'):
        command = {'action_id': 'choose', 'objects': [ref]}
        self.reject(s, 'A' if seat != 'A' else 'D', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_actual_one_mana_claws_target_owner_chooses_under_ground_seal_with_private_pending_load_replay(self):
        s, private = self.start(735001); source = self.add(s.engine, 'Scrabbling Claws', zone='hand')
        first = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='affected-creature')
        second = self.add(s.engine, 'Plains', seat='B', zone='graveyard', ref='affected-land')
        foreign = self.add(s.engine, 'Memnite', zone='graveyard', ref='other-owner-graveyard')
        seal = self.add(s.engine, 'Ground Seal')
        for card in (source, first, second, foreign, seal):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2}); self.checkpoint(s); before = sum(s.state.players['A'].mana_pool.values())
        self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.assertEqual(before - 1, sum(s.state.players['A'].mana_pool.values()))
        self.activate_choice(s, source); self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.assertEqual(('B',), tuple(s.state.pending_decision.actors))
        objects = s.packet('pilot:B', full=True)['decision']['ctx']['objects']
        self.assertEqual({first.ref, second.ref}, {r['id'] for r in objects})
        for command in ({'action_id': 'choose', 'objects': []}, {'action_id': 'choose', 'objects': [first.ref, second.ref]},
                {'action_id': 'choose', 'objects': [foreign.ref]}, {'action_id': 'choose', 'objects': [private.ref]},
                {'action_id': 'choose', 'objects': [first.ref], 'player': 'A'}):self.reject(s, 'B', command)
        s = self.replay(s, load=True); self.choose_exile(s, first.ref); self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[first.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[second.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[foreign.object_id].zone)
        self.finish(s, private)


    def activate_other(self, s, source, text, *, targets=(), seat='A'):
        self.priority(s, seat); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':') and text.casefold() in a['label'].casefold())
        command = {'action_id': action['id'], 'pay': 'auto', 'targets': list(targets)}
        self.reject(s, 'D' if seat != 'D' else 'C', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_actual_claws_second_ability_sacrifices_source_then_exiles_target_card_and_draws_for_controller(self):
        s, private = self.start(735010); source = self.add(s.engine, 'Scrabbling Claws', zone='hand')
        target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='claws-material-target')
        other = self.add(s.engine, 'Memnite', zone='graveyard', ref='claws-material-other')
        for card in (source, target, other):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 3}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        hand = len(s.state.players['A'].zones['hand']); mana = sum(s.state.players['A'].mana_pool.values())
        self.activate_other(s, source, 'Exile target card', targets=(target.ref,))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.assertEqual(mana - 1, sum(s.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[other.object_id].zone)
        self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand'])); self.finish(s, private)

    def test_actual_relic_required_choice_then_second_ability_exiles_itself_and_all_current_graveyards_before_draw(self):
        s, private = self.start(735011); source = self.add(s.engine, 'Relic of Progenitus', zone='hand')
        chosen = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='relic-chosen')
        remaining = self.add(s.engine, 'Plains', seat='B', zone='graveyard', ref='relic-remaining')
        own = self.add(s.engine, 'Memnite', zone='graveyard', ref='relic-own')
        third = self.add(s.engine, 'Swamp', seat='C', zone='graveyard', ref='relic-third')
        for card in (source, chosen, remaining, own, third):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 3}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.activate_choice(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_exile(s, chosen.ref); self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[chosen.object_id].zone)
        self.assertTrue(s.state.cards[source.object_id].tapped); hand = len(s.state.players['A'].zones['hand'])
        self.activate_other(s, source, 'Exile all graveyards')
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        for card in (remaining, own, third):self.assertEqual('graveyard', s.state.cards[card.object_id].zone)
        self.assertIsNone(self.resolve(s))
        for card in (remaining, own, third):self.assertEqual('exile', s.state.cards[card.object_id].zone)
        self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand'])); self.finish(s, private)

    def test_actual_relic_second_ability_is_offered_when_every_graveyard_is_empty_and_still_draws(self):
        s, private = self.start(735012); source = self.add(s.engine, 'Relic of Progenitus', zone='hand'); self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.assertTrue(all(not p.zones['graveyard'] for p in s.state.players.values()))
        hand = len(s.state.players['A'].zones['hand']); self.activate_other(s, source, 'Exile all graveyards')
        self.assertEqual('exile', s.state.cards[source.object_id].zone); self.assertIsNone(self.resolve(s))
        self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand'])); self.finish(s, private)

    def test_actual_merrow_printed_black_cast_optional_untap_retains_target_owner_choice_and_empty_target_legality(self):
        for index, apply in enumerate((True, False)):
            with self.subTest(apply=apply):
                s, private = self.start(735020 + index); source = self.add(s.engine, 'Merrow Bonegnawer', zone='hand')
                fervor = self.add(s.engine, 'Fervor'); ritual = self.add(s.engine, 'Dark Ritual', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='merrow-affected')
                for card in (source, fervor, ritual, target):self.assert_closed(s, card)
                self.prepare(s, mana={'B': 3}); self.checkpoint(s); before = s.state.players['A'].mana_pool['B']
                self.cast(s, 'A', source); self.assertIsNone(self.resolve(s)); self.assertEqual(before - 1, s.state.players['A'].mana_pool['B'])
                self.activate_choice(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.choose_exile(s, target.ref); self.assertIsNone(self.resolve(s)); self.assertTrue(s.state.cards[source.object_id].tapped)
                mana = s.state.players['A'].mana_pool['B']; self.cast(s, 'A', ritual)
                self.assertEqual(1, self.trigger_count(s, source)); self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.assertEqual(('A',), tuple(s.state.pending_decision.actors))
                command = {'action_id': 'choose', 'choice': 'apply' if apply else 'decline'}
                self.reject(s, 'B', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(s)); self.assertEqual(not apply, s.state.cards[source.object_id].tapped)
                self.assertEqual(mana + 2, s.state.players['A'].mana_pool['B'])
                if apply:
                    self.assertFalse(s.state.players['C'].zones['graveyard'])
                    self.activate_choice(s, source, target='C'); self.assertIsNone(self.resolve(s))
                    self.assertTrue(s.state.cards[source.object_id].tapped)
                self.finish(s, private)

    def test_actual_grave_birthing_printed_cost_required_or_empty_choice_then_colorless_scion_and_private_draw(self):
        for index, empty in enumerate((False, True)):
            with self.subTest(empty=empty):
                s, private = self.start(735030 + index); source = self.add(s.engine, 'Grave Birthing', zone='hand')
                chosen = None if empty else self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='birthing-choice')
                self.assert_closed(s, source)
                if chosen is not None:self.assert_closed(s, chosen)
                self.prepare(s, mana={'C': 2, 'B': 1}); self.checkpoint(s)
                hand = len(s.state.players['A'].zones['hand']); mana = sum(s.state.players['A'].mana_pool.values())
                self.cast(s, 'A', source, ['B']); self.assertEqual(mana - 3, sum(s.state.players['A'].mana_pool.values()))
                pending = self.resolve(s)
                if not empty:
                    self.assertEqual('semantic.choice', pending.kind); self.assertEqual(('B',), tuple(pending.actors))
                    s = self.replay(s, load=True); self.choose_exile(s, chosen.ref); self.assertIsNone(self.resolve(s))
                    self.assertEqual('exile', s.state.cards[chosen.object_id].zone)
                else:self.assertIsNone(pending)
                self.assertEqual(hand, len(s.state.players['A'].zones['hand']))
                token, = (c for c in s.state.cards.values() if c.zone == 'battlefield' and c.is_token and c.controller == 'A')
                data = s.engine._effective_card_data(token)
                self.assertIn('Eldrazi', data['type_line']); self.assertIn('Scion', data['type_line']); self.assertEqual([], list(data['colors']))
                self.assertEqual((1, 1), tuple(s.engine._numeric_stat(token.object_id, field) for field in ('power', 'toughness')))
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                mana = s.state.players['A'].mana_pool.get('C', 0)
                self.activate_other(s, token, 'Add {C}')
                self.assertEqual(mana + 1, s.state.players['A'].mana_pool.get('C', 0))
                self.assertFalse(s.state.stack); self.assertFalse(any(c.zone == 'battlefield' and c.is_token for c in s.state.cards.values()))
                self.finish(s, private)


    def test_actual_source_removal_emptying_response_and_stifle_use_resolution_time_graveyard(self):
        for index, mode in enumerate(('source_removed', 'graveyard_emptied', 'countered')):
            with self.subTest(mode=mode):
                s, private = self.start(735040 + index); source = self.add(s.engine, 'Scrabbling Claws', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='response-affected')
                support = self.add(s.engine, {'source_removed': 'Disenchant', 'graveyard_emptied': "Tormod's Crypt", 'countered': 'Stifle'}[mode],
                    seat='B', zone='battlefield' if mode == 'graveyard_emptied' else 'hand')
                for card in (source, target, support):self.assert_closed(s, card)
                s.state.players['B'].mana_pool.update({'C': 1, 'W': 1, 'U': 1})
                self.prepare(s, mana={'C': 2}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
                self.activate_choice(s, source)
                if mode == 'graveyard_emptied':self.activate_other(s, support, 'graveyard', targets=('B',), seat='B')
                else:self.cast(s, 'B', support, [s.state.cards[source.object_id].ref if mode == 'source_removed' else s.state.stack[-1].ref])
                self.resolve_top(s)
                if mode == 'source_removed':
                    self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                    self.assertEqual('semantic.choice', self.resolve(s).kind); self.choose_exile(s, target.ref); self.assertIsNone(self.resolve(s))
                    self.assertEqual('exile', s.state.cards[target.object_id].zone)
                else:
                    self.assertIsNone(self.resolve(s)); self.assertTrue(s.state.cards[source.object_id].tapped)
                    self.assertEqual('exile' if mode == 'graveyard_emptied' else 'graveyard', s.state.cards[target.object_id].zone)
                self.finish(s, private)

    def test_actual_birthing_countered_or_target_departed_before_resolution_has_no_choice_token_or_draw(self):
        for index, countered in enumerate((True, False)):
            with self.subTest(countered=countered):
                s, private = self.start(735050 + index); source = self.add(s.engine, 'Grave Birthing', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='stopped-birthing-target')
                counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand') if countered else None
                for card in (source, target):self.assert_closed(s, card)
                if counter is not None:self.assert_closed(s, counter)
                s.state.players['B'].mana_pool['U'] = 2
                self.prepare(s, mana={'C': 2, 'B': 1}); self.checkpoint(s)
                hand = len(s.state.players['A'].zones['hand']); self.cast(s, 'A', source, ['B'])
                if countered:self.cast(s, 'B', counter, [s.state.stack[-1].ref])
                else:
                    self.priority(s, 'B')
                    result = s.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual(hand - 1, len(s.state.players['A'].zones['hand']))
                self.assertFalse(any(c.zone == 'battlefield' and c.is_token for c in s.state.cards.values()))
                if countered:self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
                self.finish(s, private)

    def test_actual_birthing_self_and_hexproof_targets_reject_but_empty_unprotected_opponent_remains_offered(self):
        s, private = self.start(735052); source = self.add(s.engine, 'Grave Birthing', zone='hand')
        leyline = self.add(s.engine, 'Leyline of Sanctity', seat='B')
        for card in (source, leyline):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2, 'B': 1}); self.checkpoint(s); self.priority(s, 'A')
        offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('cast:' + source.ref))
        for target in ('A', 'B'):self.reject(s, 'A', {'action_id': offered['id'], 'targets': [target], 'pay': 'auto'})
        hand = len(s.state.players['A'].zones['hand']); self.cast(s, 'A', source, ['C']); self.assertIsNone(self.resolve(s))
        self.assertEqual(hand, len(s.state.players['A'].zones['hand']))
        self.assertEqual(1, sum(c.zone == 'battlefield' and c.is_token and c.controller == 'A' for c in s.state.cards.values()))
        self.finish(s, private)

    def test_actual_post_exile_or_post_token_and_draw_fault_restores_full_pending_choice_then_loads_and_retries(self):
        import quorune.drawing.transaction as draw_owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        for index, after_draw in enumerate((False, True)):
            with self.subTest(after_draw=after_draw):
                s, private = self.start(735060 + index); source = self.add(s.engine, 'Grave Birthing', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='rollback-graveyard-choice')
                for card in (source, target):self.assert_closed(s, card)
                self.prepare(s, mana={'C': 2, 'B': 1}); self.checkpoint(s); self.cast(s, 'A', source, ['B'])
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                before = authoritative_state_hash(s.state); hand = tuple(s.state.players['A'].zones['hand'])
                library = tuple(s.state.players['A'].zones['library']); observed = []
                if after_draw:
                    owner, name = draw_owner, '_commit_ordinary_draw'; original = getattr(owner, name)
                    def fault(*args, **kwargs):
                        result = original(*args, **kwargs)
                        observed.append((s.state.cards[target.object_id].zone,
                            sum(c.zone == 'battlefield' and c.is_token for c in s.state.cards.values()),
                            len(s.state.players['A'].zones['hand'])))
                        raise GameRuleError('Injected after chosen exile, Scion and private draw')
                else:
                    owner, name = s.engine, 'move_objects_simultaneously_intent'; original = getattr(owner, name)
                    def fault(*args, **kwargs):
                        result = original(*args, **kwargs)
                        if s.state.cards[target.object_id].zone == 'exile':
                            observed.append(('exile', 0, len(hand)))
                            raise GameRuleError('Injected after chosen exile before later instructions')
                        return result
                with patch.object(owner, name, side_effect=fault):result = s.act('pilot:B', {'action_id': 'choose', 'objects': [target.ref]})
                self.assertFalse(result.ok); self.assertIn('Injected after chosen exile', result.summary)
                self.assertEqual([('exile', 1 if after_draw else 0, len(hand) + int(after_draw))], observed)
                self.assertEqual(before, authoritative_state_hash(s.state)); self.assertEqual(hand, tuple(s.state.players['A'].zones['hand']))
                self.assertEqual(library, tuple(s.state.players['A'].zones['library']))
                s = self.replay(s, load=True); self.choose_exile(s, target.ref); self.assertIsNone(self.resolve(s))
                self.assertEqual('exile', s.state.cards[target.object_id].zone); self.assertEqual(len(hand) + 1, len(s.state.players['A'].zones['hand']))
                self.assertEqual(1, sum(c.zone == 'battlefield' and c.is_token for c in s.state.cards.values())); self.finish(s, private)

    def test_fresh_native_source_controller_candidate_substitution_is_killed_after_complete_original_positive(self):
        import quorune.semantic_choices.targeted_graveyard_exile as owner
        self.test_actual_one_mana_claws_target_owner_chooses_under_ground_seal_with_private_pending_load_replay()
        original = owner._candidates; observed = []
        def wrong(query, actor):
            if actor == 'B':
                observed.append(actor); refs = tuple(row.ref for row in query.objects(zones=('graveyard',), owner='A'))
                return original(replace(query, materialized_choice_candidates=refs), 'A')
            return original(query, actor)
        with patch.object(owner, '_candidates', wrong):
            with self.assertRaises(AssertionError):self.test_actual_one_mana_claws_target_owner_chooses_under_ground_seal_with_private_pending_load_replay()
        self.assertTrue(observed)

    def test_actual_reverberate_copy_uses_new_controller_and_target_owner_before_original_chooses_again(self):
        s, private = self.start(735070); source = self.add(s.engine, 'Grave Birthing', zone='hand')
        copy = self.add(s.engine, 'Reverberate', seat='C', zone='hand')
        first = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='original-graveyard')
        second = self.add(s.engine, 'Plains', seat='D', zone='graveyard', ref='copy-graveyard')
        for card in (source, copy, first, second):self.assert_closed(s, card)
        s.state.players['C'].mana_pool['R'] = 2
        self.prepare(s, mana={'C': 2, 'B': 1}); self.checkpoint(s)
        hand_a = len(s.state.players['A'].zones['hand']); hand_c = len(s.state.players['C'].zones['hand'])
        self.cast(s, 'A', source, ['B']); original = s.state.stack[-1].ref
        self.cast(s, 'C', copy, [original]); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(('C',), tuple(s.state.pending_decision.actors))
        command = {'action_id': 'choose', 'targets': ['D']}; self.reject(s, 'A', command)
        result = s.act('pilot:C', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.assertEqual(('D',), tuple(s.state.pending_decision.actors))
        self.choose_exile(s, second.ref, seat='D')
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.assertEqual(('B',), tuple(s.state.pending_decision.actors))
        self.assertEqual('stack', s.state.cards[source.object_id].zone)
        self.assertEqual(hand_c, len(s.state.players['C'].zones['hand']))
        self.assertEqual(1, sum(c.zone == 'battlefield' and c.is_token and c.controller == 'C' for c in s.state.cards.values()))
        self.choose_exile(s, first.ref); self.assertIsNone(self.resolve(s))
        self.assertEqual(hand_a, len(s.state.players['A'].zones['hand']))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('exile', s.state.cards[first.object_id].zone); self.assertEqual('exile', s.state.cards[second.object_id].zone)
        for seat in ('A', 'C'):self.assertEqual(1, sum(c.zone == 'battlefield' and c.is_token and c.controller == seat for c in s.state.cards.values()))
        self.finish(s, private)

    def test_actual_chosen_commander_exiles_then_scion_and_draw_finish_before_owner_state_based_command_choice(self):
        import quorune.drawing.transaction as draw_owner
        s, private = self.start(735071); source = self.add(s.engine, 'Grave Birthing', zone='hand'); self.assert_closed(s, source)
        commander_id = s.state.players['B'].zones['command'][0]; commander = s.state.cards[commander_id]
        s.engine.move_card(commander_id, 'graveyard', reason='Prepared actual graveyard commander boundary')
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        self.assertTrue(s.engine._stabilize()); self.assertEqual('state.commander_zone', s.state.pending_decision.kind)
        kept = s.act('pilot:B', {'action_id': 'remain'}); self.assertTrue(kept.ok, kept.summary)
        self.prepare(s, mana={'C': 2, 'B': 1}); self.checkpoint(s); self.cast(s, 'A', source, ['B'])
        self.assertEqual('semantic.choice', self.resolve(s).kind); observed = []; original = draw_owner._commit_ordinary_draw
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append((s.state.cards[commander_id].zone, sum(c.zone == 'battlefield' and c.is_token for c in s.state.cards.values())))
            return result
        with patch.object(draw_owner, '_commit_ordinary_draw', side_effect=observe):self.choose_exile(s, commander.ref)
        self.assertEqual([('exile', 1)], observed); self.assertEqual('state.commander_zone', s.state.pending_decision.kind)
        self.assertEqual(('B',), tuple(s.state.pending_decision.actors)); s = self.replay(s, load=True)
        self.reject(s, 'A', {'action_id': 'command'}); result = s.act('pilot:B', {'action_id': 'command'}); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); self.assertEqual('command', s.state.cards[commander_id].zone)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone); self.finish(s, private)


    def test_genuinely_verified_six_owner_withdrawals_block_complete_originals_and_preserve_independent_controls(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        def binding(name, registry):
            record = self.db.lookup(name); semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (record,), capability_registry=registry,
                capability_profile='commander_review', promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True)
            parent = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                capability_registry=registry, capability_profile='commander_review')
            return bind_card_program_runtime(parent, capability_registry=registry, profile='commander_review')
        baseline = CapabilityRegistry(deepcopy(raw))
        baseline_index = build_index(registry_value=raw, declaration_source=doc, discovered_test_ids=known)
        baseline.mark_evidence_verified(validate_capability_evidence_index(baseline_index, registry=baseline))
        for name in ORIGINALS:self.assertTrue(binding(name, baseline)['strict_capability_ready'], name)
        for missing in (*DEPENDENCIES, 'resolution.effect_sequence.fixed_clauses', 'zone.draw.library_to_hand'):
            changed = deepcopy(raw); next(c for c in changed['capabilities'] if c['id'] == missing).update(
                status='blocked', blockers=['Affected graveyard choice owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertTrue(registry.evidence_fingerprint)
            withdrawn = registry.closure((missing,), profile='commander_review')
            self.assertFalse(withdrawn.trusted)
            self.assertIn('status:' + missing + ':blocked', withdrawn.blockers)
            self.assertFalse(any('unverified' in b for b in withdrawn.blockers))
            controls = [(name, name == 'Merrow Bonegnawer' and missing in
                {'resolution.effect_sequence.fixed_clauses', 'zone.draw.library_to_hand'}) for name in ORIGINALS]
            controls.extend((('Grizzly Bears', True), ('Forest', True)))
            if missing == DEPENDENCIES[0]:controls.append(('Grim Harvest', True))
            for name, expected in controls:
                bound = binding(name, registry)
                self.assertEqual(expected, bound['strict_capability_ready'], (missing, name, bound['blockers']))


if __name__ == '__main__':unittest.main()
