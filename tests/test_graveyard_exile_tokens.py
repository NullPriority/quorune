"""Independent contracts for fixed creature tokens following graveyard exile."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_targeted_graveyard_exile as previous

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.compiler.graveyard_exile_token_templates import graveyard_exile_token_template
from quorune.graveyard_exile_token_model import (
    ExiledCreatureCardPredicate,
    GRAVEYARD_EXILE_TOKEN_OPERATION,
    GraveyardExileTokenSpec,
)


SOLDIER = (
    'Exile target card from a graveyard. If it was a creature card, '
    'create a 1/1 white Human Soldier creature token.'
)
PEST_SHELL = (
    'Exile target card from a graveyard. If a creature card is exiled this way, '
    'create a 1/1 black and green Pest creature token.'
)


class GraveyardExileTokenContractTests(unittest.TestCase):
    def test_fixed_token_grammar_retains_one_target_and_the_two_creature_fact_policies(self):
        for clause, policy, name, subtype, colors in (
            (SOLDIER, ExiledCreatureCardPredicate.CREATURE_CARD, 'Human Soldier Token', 'Human Soldier', ('W',)),
            (PEST_SHELL, ExiledCreatureCardPredicate.COMMITTED_CREATURE_CARD, 'Pest Token', 'Pest', ('B', 'G')),
        ):
            with self.subTest(clause=clause):
                _, (effect,), target, mechanics = graveyard_exile_token_template(clause)
                self.assertEqual(GRAVEYARD_EXILE_TOKEN_OPERATION, effect['op'])
                self.assertEqual('$target.0', effect['card'])
                self.assertEqual(1, target['count'])
                self.assertEqual(['graveyard'], target['zones'])
                self.assertNotIn('target_controller', target)
                self.assertIn('cr-111-tokens', mechanics)
                spec = GraveyardExileTokenSpec.from_dict(effect['result'])
                self.assertIs(policy, spec.predicate)
                self.assertEqual('$controller', spec.token_effect['controller'])
                self.assertEqual(1, spec.token_effect['quantity'])
                self.assertEqual(name, spec.token_effect['name'])
                characteristics = spec.token_effect['characteristics']
                self.assertEqual('Token Creature — ' + subtype, characteristics['type_line'])
                self.assertEqual(colors, characteristics['colors'])
                self.assertEqual(('1', '1'), (characteristics['power'], characteristics['toughness']))

    def test_fixed_token_leaf_keeps_unsupported_counts_conditions_and_siblings_out(self):
        for clause in (
            SOLDIER.replace('target card', 'up to one target card'),
            SOLDIER.replace('Exile target', 'You may exile target'),
            SOLDIER.replace('a graveyard', 'your graveyard'),
            SOLDIER.replace('creature card,', 'artifact card,'),
            SOLDIER.replace('a 1/1', 'two 1/1').replace('token.', 'tokens.'),
            SOLDIER.replace('a 1/1 white Human Soldier creature token', 'a Food token'),
            SOLDIER.replace('a 1/1 white Human Soldier creature token', 'a token that is a copy of this creature'),
            SOLDIER + ' Otherwise, you gain 3 life.',
            PEST_SHELL[:-1] + ' with "When this token dies, you win the game."',
        ):
            with self.subTest(clause=clause):
                self.assertIsNone(graveyard_exile_token_template(clause))

    def test_fixed_token_codec_seals_the_result_and_rejects_foreign_or_open_children(self):
        _, (instruction,), _, _ = graveyard_exile_token_template(SOLDIER)
        value = instruction['result']
        spec = GraveyardExileTokenSpec.from_dict(json.loads(json.dumps(value)))
        self.assertEqual(value, spec.to_dict())
        with self.assertRaises(TypeError):
            spec.token_effect['characteristics']['colors'][0] = 'R'
        for fields in (
            {'controller': '$target.0'}, {'quantity': True}, {'quantity': 2}, {'tapped': True},
            {'op': 'life'}, {'characteristics': {'type_line': 'Token Creature — Elf', 'power': '*', 'toughness': '1'}},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                GraveyardExileTokenSpec.from_dict({**value, 'token_effect': {**value['token_effect'], **fields}})
        for fields in ({'schema_version': True}, {'predicate': 'unknown'}, {'otherwise': 'draw'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                GraveyardExileTokenSpec.from_dict({**value, **fields})

    def test_uncommitted_exile_uses_last_known_classifier_only_for_the_unqualified_fact(self):
        _, (soldier,), _, _ = graveyard_exile_token_template(SOLDIER)
        _, (pest,), _, _ = graveyard_exile_token_template(PEST_SHELL)
        ordinary = GraveyardExileTokenSpec.from_dict(soldier['result'])
        arrival = GraveyardExileTokenSpec.from_dict(pest['result'])
        self.assertTrue(ordinary.should_create(exile_committed=False, creature_card=True))
        self.assertFalse(ordinary.should_create(exile_committed=False, creature_card=False))
        self.assertFalse(arrival.should_create(exile_committed=False, creature_card=None))
        self.assertTrue(arrival.should_create(exile_committed=True, creature_card=True))
        self.assertFalse(arrival.should_create(exile_committed=True, creature_card=False))
        with self.assertRaises(ValueError):
            ordinary.should_create(exile_committed=False, creature_card=None)

    def test_raw_and_composed_node_require_the_same_public_target_and_canonical_token_owners(self):
        from types import SimpleNamespace

        from quorune.graveyard_exile_token_model import GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES
        from quorune.rules.graveyard_exile_tokens import (
            graveyard_exile_token_node_capabilities,
            graveyard_exile_token_payload_dependencies,
            graveyard_exile_token_runtime_dependencies,
        )

        _, effects, target, _ = graveyard_exile_token_template(SOLDIER)
        expected = {*GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES, 'token.creation.fixed_definition'}
        node = SimpleNamespace(effects=effects, handlers=(), target_schema=target)
        self.assertEqual(expected, set(graveyard_exile_token_payload_dependencies(effects[0])))
        self.assertEqual(expected, set(graveyard_exile_token_node_capabilities(effects=effects, target_schema=target)))
        self.assertEqual(expected, set(graveyard_exile_token_runtime_dependencies(node)))
        for targets in ({**target, 'count': True}, {**target, 'zones': ['hand']}, None):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                graveyard_exile_token_runtime_dependencies(SimpleNamespace(effects=effects, handlers=(), target_schema=targets))
        with self.assertRaises(ValueError):
            graveyard_exile_token_runtime_dependencies(SimpleNamespace(effects=(), handlers=effects, target_schema=target))

    def test_typed_intent_uses_the_receipt_before_creating_and_never_queries_a_hidden_destination(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from quorune.object_query import ObjectQueryResult
        from quorune.rules.graveyard_exile_tokens import commit_graveyard_exile_token
        from quorune.rules.single_object_zone_transition import (
            SingleObjectDestination, SingleObjectOrigin, SingleObjectZoneTransitionResult,
        )
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext
        from quorune.semantic_runtime.graveyard_exile_token_handlers import GraveyardExileTokenHandler

        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Fixed token',
            seats=('A', 'B'), active_seats=('A', 'B'), apnap_order=('A', 'B'))
        for clause, destination, arrival_types, expected in (
            (SOLDIER, 'hand', ('land',), 'Human Soldier Token'),
            (PEST_SHELL, 'hand', ('creature',), None),
            (PEST_SHELL, 'exile', ('artifact', 'creature'), 'Pest Token'),
            (SOLDIER, 'exile', ('land',), None),
        ):
            with self.subTest(clause=clause, destination=destination, types=arrival_types):
                _, (effect,), _, _ = graveyard_exile_token_template(clause)
                plan = GraveyardExileTokenHandler().lower({**effect, 'card': 'target-ref'}, context)
                intent, = plan.intents
                target = SimpleNamespace(object_id='target-id', ref='target-ref', logical_object_id='target@1',
                    zone='graveyard', types=('creature',), face_down=False, is_card_object=True)

                def public_row(card):
                    self.assertIn(card.zone, ('graveyard', 'exile'))
                    return ObjectQueryResult(object_id=card.object_id, ref=card.ref, printed_name='Witness',
                        owner='B', controller='B', zone=card.zone, types=card.types, logical_object_id=card.logical_object_id)

                def move(host, ref, *, actor, **kwargs):
                    self.assertEqual(('A', target.ref), (actor, ref))
                    target.zone, target.logical_object_id, target.types = destination, 'target@2', arrival_types
                    return SingleObjectZoneTransitionResult(target.object_id, ref, 'B', 'B', SingleObjectDestination.EXILE,
                        destination, target.logical_object_id, SingleObjectOrigin.GRAVEYARD)

                host = SimpleNamespace(state=SimpleNamespace(cards={target.object_id: target}), _require_seat=Mock(),
                    _resolve_object=Mock(return_value=target), _public_object_query_result=Mock(side_effect=public_row))
                with patch('quorune.public_zone_moves.exile_public_graveyard_card_transition', side_effect=move):
                    result = commit_graveyard_exile_token(host, intent)
                names = [effect['name'] for effect in result.effects]
                self.assertEqual([expected] if expected is not None else [], names)
                if destination == 'hand' and clause == PEST_SHELL:
                    host._public_object_query_result.assert_not_called()

    def test_composed_instruction_resolves_only_its_target_before_committing_the_token(self):
        from types import SimpleNamespace

        from quorune.compiler.effect_template_composition import reviewed_effect_template_composition
        from quorune.replacement_decisions import _quantity_followup_plan
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext
        from quorune.semantic_runtime.graveyard_exile_token_handlers import GraveyardExileTokenHandler
        from quorune.semantic_runtime.values import resolve_semantic_value

        def no_fallback(text):
            self.fail(f'An owned token instruction reached the fallback compiler ({text})')

        result = reviewed_effect_template_composition(SOLDIER, source_name='Generic source',
            compile_atomic=no_fallback, compile_fixed=no_fallback)
        self.assertEqual(graveyard_exile_token_template(SOLDIER), result)
        _, effects, target, mechanics = result
        self.assertIn('card.exile.public_graveyard_fixed_creature_token', capability_dependencies_for_node(
            effects=effects, target_schema=target, mechanic_ids=mechanics))
        item = SimpleNamespace(controller='A', targets=['announced-target'], context={})
        resolved = resolve_semantic_value(SimpleNamespace(), effects[0], item)
        self.assertEqual('announced-target', resolved['card'])
        self.assertEqual('$controller', resolved['result']['token_effect']['controller'])
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Fixed token',
            seats=('A', 'B'), active_seats=('A', 'B'), apnap_order=('A', 'B'))
        plan = GraveyardExileTokenHandler().lower(resolved, context)
        self.assertTrue(_quantity_followup_plan(plan))

    def test_committed_gate_mutation_is_killed_by_the_declined_movement_witness(self):
        from unittest.mock import PropertyMock, patch

        _, (instruction,), _, _ = graveyard_exile_token_template(PEST_SHELL)
        recipe = GraveyardExileTokenSpec.from_dict(instruction['result'])

        def witness():
            self.assertFalse(recipe.should_create(exile_committed=False, creature_card=True))

        witness()
        with patch.object(GraveyardExileTokenSpec, 'requires_committed_exile', new_callable=PropertyMock, return_value=False):
            with self.assertRaisesRegex(AssertionError, 'True is not false'):
                witness()

    def test_activation_gate_declares_the_token_result_and_validates_its_exact_target(self):
        from quorune.compiler.activated_mana_nodes import _activated_effect_dependency_gate
        from quorune.graveyard_exile_token_model import GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES, GRAVEYARD_EXILE_TOKEN_CAPABILITY

        _, effects, target, mechanics = graveyard_exile_token_template(SOLDIER)
        options = dict(effects=effects, mechanics=mechanics, trusted_mechanics=frozenset(),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        gate = _activated_effect_dependency_gate(target_schema=target, **options)
        self.assertEqual({*GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES, 'token.creation.fixed_definition'}, set(gate.capabilities))
        changed = _activated_effect_dependency_gate(target_schema={**target, 'owner_relation': 'self'}, **options)
        self.assertNotIn(GRAVEYARD_EXILE_TOKEN_CAPABILITY, changed.capabilities)

    def test_canonical_quoted_pest_fragment_uses_the_explicit_followup_token_closure(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.committed_quantity_followups import fixed_followup_capabilities
        from quorune.rules.graveyard_exile_tokens import graveyard_exile_token_payload_dependencies

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'pest-definition.sqlite3'
            build_fixture_database(ROOT / 'tests/fixtures/graveyard-exile-token-cards.json', path)
            with CardDatabase(path) as db:
                creator = db.lookup('Professor of Zoomancy')
                ir = compile_oracle_card(creator, capability_registry=load_default_capability_registry(),
                    capability_profile='commander_review')
        self.assertEqual('exact', ir.status)
        token, = (effect for face in ir.faces for node in face.nodes for effect in node.effects if effect['op'] == 'create_token')
        self.assertEqual((), fixed_followup_capabilities(token))
        recipe = GraveyardExileTokenSpec(ExiledCreatureCardPredicate.COMMITTED_CREATURE_CARD, token)
        self.assertEqual(token, recipe.to_dict()['token_effect'])
        self.assertIn('token.creation.typed_ability_definition', graveyard_exile_token_payload_dependencies(recipe.effect()))

    def test_activation_promotion_requires_a_single_target_instruction_and_all_result_dependencies(self):
        from dataclasses import replace
        from types import SimpleNamespace

        from quorune.abilities import parse_activated_abilities
        from quorune.replacement.immutable import FrozenMap
        from quorune.rules.graveyard_exile_tokens import (
            graveyard_exile_token_runtime_dependencies, is_closed_graveyard_exile_token_activation,
        )
        from quorune.semantic_runtime.activated_abilities import activated_ability_catalog_descriptor

        _, effects, target, _ = graveyard_exile_token_template(SOLDIER)
        ability, = parse_activated_abilities(card_name='Source', oracle_text='{2}{W}{B}: ' + SOLDIER)
        key = 'pinned-source:ability:ab1'
        ability = replace(ability, builtin_semantic_key=key, target_schema=FrozenMap(target))
        handler = activated_ability_catalog_descriptor(ability)
        fields = {'key': key, 'event': 'activate', 'effects': effects, 'handlers': (handler,), 'target_schema': target}
        required = graveyard_exile_token_runtime_dependencies(SimpleNamespace(**fields))
        fields['capability_dependencies'] = required
        self.assertTrue(is_closed_graveyard_exile_token_activation(SimpleNamespace(**fields)))
        for changes in ({'event': 'resolve'}, {'handlers': ()}, {'handlers': ({'op': 'life'},)},
                {'key': 'other-source:ability:ab1'}, {'effects': (*effects, {'op': 'life'})},
                {'target_schema': {**target, 'count': True}}, {'capability_dependencies': required[:-1]}):
            with self.subTest(changes=changes):
                self.assertFalse(is_closed_graveyard_exile_token_activation(SimpleNamespace(**{**fields, **changes})))

class GraveyardExileTokenNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = previous.TargetedGraveyardExileNativeTests.start
    reject = previous.TargetedGraveyardExileNativeTests.reject
    finish = previous.TargetedGraveyardExileNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        db_path = folder / 'graveyard-exile-tokens.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-token-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-type-outcome-cards.json',
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Graveyard exile tokens', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def activation(self, session, source):
        self.priority(session, 'A')
        current = session.state.cards[source.object_id]
        prefix = f'activate:{current.ref}:'
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        return next((action for action in actions if action['id'].startswith(prefix)), None)

    def activate_for(self, session, source, target):
        offered = self.activation(session, source)
        self.assertIsNotNone(offered)
        command = {'action_id': offered['id'], 'targets': [target.ref], 'pay': 'auto'}
        self.reject(session, 'D', command)
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)

    @staticmethod
    def tokens(session, name):
        return [card for card in session.state.cards.values()
            if card.zone == 'battlefield' and not card.is_card_object and card.printed_name == name]

    def test_actual_feral_appetite_creates_only_for_creature_and_pest_attacks_then_dies(self):
        session, private = self.start(740110)
        source = self.add(session.engine, 'Feral Appetite', zone='hand')
        land = self.add(session.engine, 'Forest', seat='D', zone='graveyard')
        artifact_creature = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        fervor = self.add(session.engine, 'Fervor')
        answer = self.add(session.engine, 'Shock', seat='B', zone='hand')
        mountain = self.add(session.engine, 'Mountain', seat='B')
        for card in (source, land, artifact_creature, fervor, answer, mountain):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 4, 'G': 3})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, sum(session.state.players['A'].mana_pool.values()))
        self.activate_for(session, source, land)
        self.assertEqual(2, sum(session.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[land.object_id].zone)
        self.assertEqual([], self.tokens(session, 'Pest Token'))

        self.activate_for(session, source, artifact_creature)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[artifact_creature.object_id].zone)
        pest, = self.tokens(session, 'Pest Token')
        self.assertEqual('A', pest.controller)
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(pest.object_id, stat) for stat in ('power', 'toughness')))
        self.assertNotIn('deathtouch', session.engine._combat_keywords(pest))

        self.attack(session, [(pest, 'B')])
        self.assertEqual((2, 1), tuple(session.engine._numeric_stat(pest.object_id, stat) for stat in ('power', 'toughness')))
        self.assertIn('deathtouch', session.engine._combat_keywords(session.state.cards[pest.object_id]))
        self.cast(session, 'B', answer, [pest.ref])
        self.until(session, lambda game: any(item.kind == 'triggered_ability' and item.source_object_id == pest.object_id
            for item in game.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.tokens(session, 'Pest Token'))
        self.assertEqual((41, 40, 40, 40), tuple(session.state.players[seat].life for seat in 'ABCD'))
        self.finish(session, private)

    def test_actual_generals_enforcer_pays_both_colors_and_creates_white_human_soldier(self):
        session, private = self.start(740120)
        source = self.add(session.engine, "General's Enforcer", zone='hand')
        legendary = self.add(session.engine, 'Sivitri Scarzam')
        target = self.add(session.engine, 'Grizzly Bears', seat='C', zone='graveyard')
        for card in (source, legendary, target):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 2, 'W': 2, 'B': 2})
        self.checkpoint(session)
        self.assertNotIn('indestructible', session.engine._combat_keywords(legendary))
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'C': 2, 'W': 1, 'B': 1},
            {color: n for color, n in session.state.players['A'].mana_pool.items() if n})
        self.assertIn('indestructible', session.engine._combat_keywords(session.state.cards[legendary.object_id]))
        self.assertNotIn('indestructible', session.engine._combat_keywords(session.state.cards[source.object_id]))

        self.activate_for(session, source, target)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        soldier, = self.tokens(session, 'Human Soldier Token')
        facts = session.engine._public_object_query_result(soldier)
        self.assertEqual('A', facts.controller)
        self.assertEqual({'human', 'soldier'}, set(facts.subtypes))
        self.assertEqual({'W'}, set(facts.colors))
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(soldier.object_id, stat) for stat in ('power', 'toughness')))
        self.assertNotIn('indestructible', session.engine._combat_keywords(soldier))
        self.finish(session, private)

    def test_actual_stale_creature_target_never_creates_and_ground_seal_disables_the_offer(self):
        for seed, sealed in ((740130, False), (740131, True)):
            with self.subTest(sealed=sealed):
                session, private = self.start(seed)
                source = self.add(session.engine, 'Feral Appetite', zone='hand')
                target = self.add(session.engine, 'Grizzly Bears', seat='D', zone='graveyard')
                for card in (source, target):
                    self.assert_closed(session, card)
                if sealed:
                    self.assert_closed(session, self.add(session.engine, 'Ground Seal', seat='D'))
                else:
                    answer = self.add(session.engine, 'Cremate', seat='B', zone='hand')
                    self.assert_closed(session, answer)
                    session.state.players['B'].mana_pool['B'] = 1
                self.prepare(session, mana={'C': 3, 'G': 2})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.assertIsNone(self.resolve(session))
                if sealed:
                    self.assertIsNone(self.activation(session, source))
                    current = session.state.cards[source.object_id]
                    ability = session.engine._activated_abilities(current)[0]
                    self.reject(session, 'A', {'action_id': f'activate:{current.ref}:{ability.ability_id}',
                        'targets': [target.ref], 'pay': 'auto'})
                    self.assertEqual(2, sum(session.state.players['A'].mana_pool.values()))
                    self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
                else:
                    self.activate_for(session, source, target)
                    self.cast(session, 'B', answer, [target.ref])
                    self.resolve_top(session)
                    session = self.replay(session, load=True)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual('exile', session.state.cards[target.object_id].zone)
                self.assertEqual([], self.tokens(session, 'Pest Token'))
                self.assertEqual(40, session.state.players['A'].life)
                self.finish(session, private)

    def test_actual_token_entry_fault_rolls_back_exile_token_and_priority_then_replays(self):
        from unittest.mock import patch

        from quorune import token_creation as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash

        session, private = self.start(740140)
        source = self.add(session.engine, 'Feral Appetite', zone='hand')
        target = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        for card in (source, target):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 3, 'G': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.activate_for(session, source, target)
        self.priority(session, 'D')
        before = authoritative_state_hash(session.state)
        recorded = []
        original = owner._commit_token_object

        def fault(*args, **kwargs):
            created_id = original(*args, **kwargs)
            created = session.state.cards[created_id]
            recorded.append((session.state.cards[target.object_id].zone, created.printed_name))
            raise GameRuleError('Injected after graveyard movement and token entry')

        command = {'action_id': 'pass'}
        with patch.object(owner, '_commit_token_object', side_effect=fault):
            result = session.act('pilot:D', command)
        self.assertFalse(result.ok)
        self.assertIn('Injected after graveyard movement and token entry', result.summary)
        self.assertEqual([('exile', 'Pest Token')], recorded)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual([], self.tokens(session, 'Pest Token'))
        session = self.replay(session, load=True)
        retry = session.act('pilot:D', command)
        self.assertTrue(retry.ok, retry.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.assertEqual(1, len(self.tokens(session, 'Pest Token')))
        self.finish(session, private)

    def test_fresh_native_creature_type_mutant_is_killed_by_the_paid_pest_witness(self):
        from unittest.mock import patch

        from quorune.rules import graveyard_exile_branches as owner

        def trial(seed):
            session, private = self.start(seed)
            source = self.add(session.engine, 'Feral Appetite', zone='hand')
            target = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
            for card in (source, target):
                self.assert_closed(session, card)
            self.prepare(session, mana={'C': 3, 'G': 2})
            self.checkpoint(session)
            self.cast(session, 'A', source)
            self.assertIsNone(self.resolve(session))
            self.activate_for(session, source, target)
            self.assertIsNone(self.resolve(session))
            self.assertEqual('exile', session.state.cards[target.object_id].zone)
            self.assertEqual(1, len(self.tokens(session, 'Pest Token')))
            self.finish(session, private)

        trial(740160)
        with patch.object(owner, '_committed_creature_card', return_value=False):
            with self.assertRaisesRegex(AssertionError, '1 != 0'):
                trial(740161)

    def test_genuinely_verified_token_owner_withdrawals_respect_both_originals_and_the_intrinsic(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.graveyard_exile_token_model import GRAVEYARD_EXILE_TOKEN_CAPABILITY
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_data = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        profile = 'commander_review'

        def verified(data):
            registry = CapabilityRegistry(data)
            sealed_index = build_index(registry_value=data, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(sealed_index, registry=registry))
            return registry

        def ready(name, registry):
            card = self.db.lookup(name)
            semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile=profile,
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True)
            program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=registry, capability_profile=profile)
            return bind_card_program_runtime(program, capability_registry=registry, profile=profile)['strict_capability_ready']

        feral, soldier, professor, cremate = ('Feral Appetite', "General's Enforcer", 'Professor of Zoomancy', 'Cremate')
        both = {feral, soldier}
        affected = {
            GRAVEYARD_EXILE_TOKEN_CAPABILITY: both,
            'card.exile.public_graveyard': both | {cremate},
            'target.revalidate_resolution': both | {cremate},
            'token.creation.fixed_definition': both | {professor},
            'token.creation.typed_ability_definition': {feral, professor},
            'life.change.effect': {feral, professor},
            'trigger.event.normalized_zone_change': {feral, professor},
        }
        positives = (feral, soldier, professor, cremate, 'Grizzly Bears')
        baseline = verified(deepcopy(registry_data))
        for name in positives:
            self.assertTrue(ready(name, baseline), name)
        self.assertFalse(ready('Ghost Vacuum', baseline))

        for missing, blocked in affected.items():
            with self.subTest(withdrawn=missing):
                raw = deepcopy(registry_data)
                entry = next(row for row in raw['capabilities'] if row['id'] == missing)
                entry.update(status='blocked', blockers=['Fixed exile token or a native result owner withdrawn'])
                registry = verified(raw)
                report = registry.closure((missing,), profile=profile)
                self.assertFalse(report.trusted)
                self.assertIn('status:' + missing + ':blocked', report.blockers)
                self.assertFalse(any('unverified' in value for value in report.blockers))
                for name in positives:
                    self.assertEqual(name not in blocked, ready(name, registry), name)
