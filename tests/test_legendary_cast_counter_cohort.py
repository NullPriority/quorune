"""Both cast colors use the retained legendary creature cohort."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.captured_counter_keyword_grant import (
    CAPTURED_COUNTER_FLYING_VERSION, CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY,
    CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION, captured_counter_keyword_instruction,
    decode_captured_counter_keyword_instruction,
)
from scripts.build_test_database import build_fixture_database


CARD = 'Ardbert, Warrior of Darkness'
PROFILE = 'commander_review'


class LegendaryCastCounterCohortTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'legendary-cast-counter-cohort.sqlite3'
        fixtures = ROOT / 'tests/fixtures'
        build_fixture_database([fixtures / name for name in (
            'bound-effect-program-cards.json', 'ardbert-cards.json', 'counter-prohibition-cards.json',
            'state-qualified-zone-cards.json', 'all-player-turn-rule-cards.json',
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = 'Generic Bound Commander'
        cls.deck = DeckDefinition('Legendary cast counter cohort', [
            DeckEntry(commander, 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], [commander])

    def test_two_exact_cast_and_keyword_pairs_require_the_new_typed_authority(self):
        card = self.db.lookup(CARD, fuzzy=False)
        settings = {'capability_registry': self.registry, 'capability_profile': PROFILE}
        ir = compile_oracle_card(card, **settings)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        self.assertEqual(2, len(nodes))
        for line, node in zip(card.oracle_text.splitlines(), nodes, strict=True):
            self.assertEqual(line, card.oracle_text[node.span.start:node.span.end])
            self.assertIn(CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY, node.capability_dependencies)
            self.assertEqual(('spell.cast', 'battlefield'), (node.event, node.active_zone))
            self.assertIsNone(node.target_schema)
            self.assertEqual(CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION, node.effects[0]['schema_version'])
        self.assertEqual([('W', 'Vigilance'), ('B', 'Menace')],
                         [(node.effects[0]['cast_color'], node.effects[0]['keyword']) for node in nodes])
        game = self.session(60320301)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        for program in game.engine.semantics.programs_for_oracle(card.oracle_id):
            deps = [value for value in program.capability_dependencies
                    if value != CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY]
            omitted = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(report['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY,
                          report['blockers'])
            changed = replace(program, effects=[{**program.effects[0], 'keyword': 'Flying'}])
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE)['strict'])
        for version in (1, CAPTURED_COUNTER_FLYING_VERSION):
            original = captured_counter_keyword_instruction(version=version)
            self.assertEqual({'op', 'schema_version', 'player', 'query'}, set(original))
            self.assertEqual((), decode_captured_counter_keyword_instruction(original).supertypes_all)
        with patch('quorune.compiler.captured_counter_keyword_mode.captured_legendary_cast_counter_result',
                   return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(card, **settings).status)

    def test_hybrid_cast_creates_both_triggers_even_when_the_spell_or_counters_are_stopped(self):
        for prevented in (False, True):
            with self.subTest(prevented=prevented):
                game = self.session(60320310 + prevented)
                ardbert = self.add(game.engine, CARD)
                ally = self.add(game.engine, 'Isamaru, Hound of Konda', ref='own-legend')
                other = self.add(game.engine, 'Isamaru, Hound of Konda', seat='C', ref='foreign-legend')
                ordinary = self.add(game.engine, 'Grizzly Bears', ref='own-nonlegend')
                guildmage = self.add(game.engine, 'Orzhov Guildmage', zone='hand')
                counter = self.add(game.engine, 'Counterspell', seat='B', zone='hand')
                if prevented:
                    solemnity = self.add(game.engine, 'Solemnity', seat='D')
                    self.assert_closed(game, solemnity)
                for card in (ardbert, guildmage, counter):
                    self.assert_closed(game, card)
                game.state.config.semantic_policy = 'trusted_only'
                game.state.players['B'].mana_pool['U'] = 2
                self.prepare(game, mana={'W': 2})
                self.checkpoint(game)
                self.cast_for(game, 'A', guildmage)
                decision = game.state.pending_decision
                self.assertEqual(('trigger.order', ['A']), (decision.kind, decision.actors))
                native_spell, = [item for item in game.state.stack if item.kind == 'spell']
                game = self.replay(game, load=True)
                context = game.packet('pilot:A', full=True)['decision']['ctx']
                command = {'action_id': 'order', 'triggers': [row['id'] for row in context['triggers']]}
                self.assertEqual(2, len(command['triggers']))
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act('pilot:B', command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
                observers = [item for item in game.state.stack if item.source_object_id == ardbert.object_id]
                self.assertEqual(2, len(observers))
                self.assertEqual({'W', 'B'}, set(observers[0].context['colors']))
                self.assertEqual({'Vigilance', 'Menace'}, {
                    game.engine.semantics.get(item.semantic_key).effects[0]['keyword'] for item in observers
                })
                self.cast_for(game, 'B', counter, [native_spell.ref])
                self.resolve_top(game)
                self.assertEqual('graveyard', game.state.cards[guildmage.object_id].zone)
                self.assertEqual(2, len(game.state.stack))
                self.resolve_top(game)
                self.resolve_top(game)
                for creature in (ardbert, ally, ordinary, other):
                    member = creature.object_id in {ardbert.object_id, ally.object_id}
                    live = game.state.cards[creature.object_id]
                    self.assertEqual(2 if member and not prevented else 0, live.counters.get('+1/+1', 0))
                    granted = {'vigilance', 'menace'} & set(game.engine._combat_keywords(live))
                    self.assertEqual({'vigilance', 'menace'} if member else set(), granted)
                turn = game.state.turn_sequence
                self.until(game, lambda current: current.state.turn_sequence > turn)
                for creature in (ardbert, ally):
                    self.assertFalse({'vigilance', 'menace'} & set(game.engine._combat_keywords(
                        game.state.cards[creature.object_id])))
                self.replay(game)
