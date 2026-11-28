"""Private ordering and draw followed by the original source's library move."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.owner_library_position_shapes import DRAW_SOURCE_OWNER_TOP_CAPABILITY, DRAW_SOURCE_OWNER_TOP_FIELD, draw_source_owner_top_effects
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
from top_order_fixture import TopOrderFixture


CARD = "Sensei's Divining Top"
PROFILE = 'commander_review'


class DrawSourceOwnerTopTests(TopOrderFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        directory = Path(cls.temporary.name)
        fixtures = ROOT / 'tests/fixtures'
        pinned = json.loads((fixtures / 'upkeep-source-counter-sacrifice-cards.json').read_bytes())
        disenchant, = (card for card in pinned['cards'] if card['name'] == 'Disenchant')
        extra = directory / 'disenchant.json'
        extra.write_text(json.dumps({'schema_version': 1, 'cards': [disenchant],
                                    'rulings': [row for row in pinned['rulings'] if row['oracle_id'] == disenchant['oracle_id']]}),
                         encoding='utf-8')
        database = directory / 'draw-source-top.sqlite3'
        build_fixture_database([fixtures / 'bound-effect-program-cards.json', fixtures / 'fixed-top-order-cards.json', extra], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = 'Isamaru, Hound of Konda'
        cls.deck = DeckDefinition('Draw and source order', [DeckEntry(commander, 1, 'commander'),
                                                              DeckEntry('Generic Bound Plains', 30)], [commander])

    def activate(self, game, source, ability):
        expected = f'activate:{source.ref}:{ability}'
        actions = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = (row for row in actions if row['id'] == expected)
        self.assertTrue(game.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)

    def start_draw(self, seed, *, owner='A', destroy=False):
        game = self.session(seed)
        source = self.add(game.engine, CARD)
        source.owner = owner
        spell = self.add(game.engine, 'Disenchant', seat='B', zone='hand') if destroy else None
        if destroy:
            for index in range(2):
                self.add(game.engine, 'Generic Bound Plains', seat='B', ref=f'top-disenchant-mana-{index}')
        self.top_cards(game)
        self.assert_closed(game, source)
        game.state.config.semantic_policy = 'trusted_only'
        self.prepare(game)
        self.checkpoint(game)
        drawn = game.state.players['A'].zones['library'][-1]
        self.activate(game, source, 'ab2')
        self.assertTrue(game.state.cards[source.object_id].tapped)
        if spell is not None:
            self.cast_for(game, 'B', spell, [source.ref])
            self.resolve_top(game)
            self.assertEqual('graveyard', game.state.cards[source.object_id].zone)
        return game, source, drawn

    def check_draw_and_move(self, game, source, drawn):
        self.resolve_top(game)
        self.assertIn(drawn, game.state.players['A'].zones['hand'])
        current = game.state.cards[source.object_id]
        self.assertEqual('library', current.zone, 'The original source moves to its owner library')
        self.assertEqual(source.object_id, game.state.players[current.owner].zones['library'][-1])
        identity = game.state.cards[drawn].ref
        for seat in 'BCD':
            self.assertNotIn(identity, json.dumps(game.packet(f'pilot:{seat}', full=True)))

    def test_actual_paid_top_order_stays_private_and_replays(self):
        game = self.session(12108301)
        source = self.add(game.engine, CARD)
        self.top_cards(game)
        expected = self.top_refs(game, 3)
        self.assert_closed(game, source)
        game.state.config.semantic_policy = 'trusted_only'
        self.prepare(game, mana={'C': 1})
        self.checkpoint(game)
        self.activate(game, source, 'ab1')
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual('semantic.choice', self.resolve(game).kind)
        game, chosen = self.choose_order(game, expected)
        self.settle(game)
        self.assertEqual(chosen, self.top_refs(game, 3))
        self.replay(game, load=True)

    def test_actual_draw_precedes_owner_top_placement_and_replays(self):
        for owner in 'AB':
            with self.subTest(owner=owner):
                game, source, drawn = self.start_draw(12108302 + (owner == 'B'), owner=owner)
                game = self.replay(game, load=True)
                self.check_draw_and_move(game, source, drawn)
                self.replay(game, load=True)

    def test_actual_departed_source_leaves_draw_and_no_move_and_replays(self):
        game, source, drawn = self.start_draw(12108304, destroy=True)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn(drawn, game.state.players['A'].zones['hand'])
        self.assertEqual('graveyard', game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_native_mutant_omitting_source_move_is_killed(self):
        from quorune.semantic_runtime import executor

        game, source, drawn = self.start_draw(12108305)
        bind = executor._bind_event_resolution_results

        def omit(host, item, effect):
            if DRAW_SOURCE_OWNER_TOP_FIELD in effect:
                return item, None
            return bind(host, item, effect)

        with patch.object(executor, '_bind_event_resolution_results', side_effect=omit):
            with self.assertRaisesRegex(AssertionError, 'The original source moves to its owner library'):
                self.check_draw_and_move(game, source, drawn)

    def test_exact_draw_then_source_move_requires_the_original_price_and_sequence(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        order, draw = ir.faces[0].nodes
        self.assertEqual('fixed-controller-top-order-v1', order.template_id)
        self.assertEqual(draw_source_owner_top_effects(), tuple(draw.effects))
        admission, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admission['strict_capability_ready'], admission['blockers'])
        cap = DRAW_SOURCE_OWNER_TOP_CAPABILITY
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id) if cap in row.capability_dependencies)
        dependencies = tuple(value for value in program.capability_dependencies if value != cap)
        missing = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)['strict'])
        for changed in (replace(program, effects=tuple(reversed(program.effects))),
                        replace(program, cost_schema={**program.cost_schema, 'tap_source': False})):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)['strict'])
