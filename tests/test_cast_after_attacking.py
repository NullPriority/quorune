"""Player-specific restrictions after a creature's declared attack."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_targeted_graveyard_exile as previous

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.compiler.static_cast_rule_templates import static_cast_rule_handler
from quorune.semantic_runtime.static_cast_rules import (
    StaticCastCondition,
    StaticCastPlayerScope,
    StaticCastRuleKind,
    StaticCastRuleSourceContext,
    _condition_applies,
    _scope_applies,
    default_static_cast_rule_registry,
)


CLAUSE = "Each opponent who attacked with a creature this turn can't cast spells."


class CastAfterAttackingTests(unittest.TestCase):
    def test_exact_grammar_compiles_all_spell_prohibition_and_keeps_adjacent_shapes_closed(self):
        _, descriptor, capability = static_cast_rule_handler(CLAUSE, source_name='Witness')
        self.assertEqual('casting.prohibition.fixed_public', capability)
        self.assertEqual(StaticCastRuleKind.CAST_PROHIBITION, StaticCastRuleKind(descriptor['kind']))
        self.assertEqual(StaticCastPlayerScope.SOURCE_OPPONENTS, StaticCastPlayerScope(descriptor['player_scope']))
        self.assertEqual(StaticCastCondition.PLAYER_ATTACKED_WITH_CREATURE_THIS_TURN,
            StaticCastCondition(descriptor['condition']))
        self.assertEqual([], descriptor['origin_zones'])
        for changed in (
            CLAUSE.replace('Each opponent', 'Each player'),
            CLAUSE.replace('attacked with a creature', 'was attacked by a creature'),
            CLAUSE.replace("can't cast spells", "can't cast creature spells"),
            CLAUSE + ' Draw a card.',
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(static_cast_rule_handler(changed, source_name='Witness'))

    def test_exact_prohibition_reads_only_the_candidate_players_declared_attack_history(self):
        _, descriptor, _ = static_cast_rule_handler(CLAUSE, source_name='Witness')
        context = StaticCastRuleSourceContext(source_ref='public-source', source_logical_object_id='source@1',
            source_controller='A', source_tapped=False, chosen_name='', attached_player=None)
        registry = default_static_cast_rule_registry()
        rule, = registry.lower(descriptor, context)

        events = {'creature_attacked': [SimpleNamespace(actor='A'), SimpleNamespace(actor='B')],
            'spell_cast': [SimpleNamespace(actor='C')]}
        host = SimpleNamespace(_current_turn_history=lambda kind: events.get(kind, ()))
        self.assertFalse(_scope_applies(rule, 'A'))
        self.assertTrue(_scope_applies(rule, 'B'))
        self.assertTrue(_condition_applies(host, rule, player='B'))
        self.assertFalse(_condition_applies(host, rule, player='C'))
        self.assertFalse(_condition_applies(host, rule))
        events['creature_attacked'] = []
        self.assertFalse(_condition_applies(host, rule, player='B'))

    def test_attack_condition_rejects_widened_scope_origin_or_named_spell(self):
        _, descriptor, _ = static_cast_rule_handler(CLAUSE, source_name='Witness')
        context = StaticCastRuleSourceContext(source_ref='public-source', source_logical_object_id='source@1',
            source_controller='A', source_tapped=False, chosen_name='', attached_player=None)
        registry = default_static_cast_rule_registry()
        for changes in ({'player_scope': 'all_players'}, {'origin_zones': ['graveyard']}, {'chosen_name': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                registry.lower({**descriptor, **changes}, context)

class CastAfterAttackingNativeTests(InheritedNativeFixture, unittest.TestCase):
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
        db_path = folder / 'cast-after-attacking.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/graveyard-card-result-cards.json',
            ROOT / 'tests/fixtures/upkeep-creature-token-absence-cards.json',
            ROOT / 'tests/fixtures/cast-after-attacking-cards.json',
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Cast after declared attack', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def current_offers(self, session, seat):
        self.priority(session, seat)
        return session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']

    def test_actual_late_arbiter_restricts_only_the_declaring_player_and_removal_restores_casting(self):
        session, private = self.start(743110)
        arbiter = self.add(session.engine, 'Angelic Arbiter', zone='hand')
        orrery = self.add(session.engine, 'Vedalken Orrery')
        attacker = self.add(session.engine, 'Grizzly Bears', seat='B')
        attacker.acquired_control_turn_count = -1
        instant = self.add(session.engine, 'Shock', seat='B', zone='hand')
        mountain = self.add(session.engine, 'Mountain', seat='B')
        removal = self.add(session.engine, 'Doom Blade', seat='C', zone='hand')
        lands = [self.add(session.engine, 'Plains', ref=f'arbiter-price-{i}') for i in range(7)]
        payment = [self.add(session.engine, name, seat='C') for name in ('Swamp', 'Forest')]
        for card in (arbiter, orrery, attacker, instant, mountain, removal, *lands, *payment):
            self.assert_closed(session, card)
        self.prepare(session, active='B')
        self.checkpoint(session)
        self.until(session, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'combat.attackers')
        offered = session.packet('pilot:B', full=True)
        self.assertEqual('combat.attackers', offered['decision']['kind'])
        command = {'action_id': 'attack', 'attackers': {attacker.ref: 'D'}}
        self.reject(session, 'A', command)
        result = session.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(['B'], [r.actor for r in session.engine._current_turn_history('creature_attacked')])

        prefix = 'cast:' + instant.ref
        old_offer = next(row for row in self.current_offers(session, 'B') if row['id'].startswith(prefix))
        self.cast(session, 'A', arbiter)
        self.resolve_top(session)
        self.assertTrue(all(session.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual('battlefield', session.state.cards[arbiter.object_id].zone)
        self.assertIn('flying', session.engine._combat_keywords(session.state.cards[arbiter.object_id]))

        available = self.current_offers(session, 'B')
        self.assertFalse(any(row['id'].startswith(prefix) for row in available))
        self.reject(session, 'B', {'action_id': old_offer['id'], 'targets': ['D'], 'pay': 'auto'})
        mana_offer = next(row for row in available if row.get('source') == mountain.ref and row.get('mana_ability'))
        result = session.act('pilot:B', {'action_id': mana_offer['id']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players['B'].mana_pool['R'])
        session = self.replay(session, load=True)

        self.cast(session, 'C', removal, [arbiter.ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[arbiter.object_id].zone)
        self.assertTrue(any(row['id'].startswith(prefix) for row in self.current_offers(session, 'B')))
        self.cast(session, 'B', instant, ['D'])
        self.resolve_top(session)
        self.assertEqual(38, session.state.players['D'].life)
        self.finish(session, private)

    def test_fresh_native_omitting_the_attack_history_check_breaks_the_actual_offer_witness(self):
        from unittest.mock import patch

        from quorune.semantic_runtime import static_cast_rules as owner

        original = owner._condition_applies

        def omitted(host, rule, **kwargs):
            if rule.condition is StaticCastCondition.PLAYER_ATTACKED_WITH_CREATURE_THIS_TURN:
                return False
            return original(host, rule, **kwargs)

        with patch.object(owner, '_condition_applies', omitted):
            with self.assertRaisesRegex(AssertionError, 'True is not false'):
                self.test_actual_late_arbiter_restricts_only_the_declaring_player_and_removal_restores_casting()

    def test_genuinely_verified_prohibition_and_flying_withdrawals_block_only_supported_arbiter(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        profile = 'commander_review'

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def admitted(name, registry):
            card = self.db.lookup(name, fuzzy=False)
            semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile=profile,
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            p = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=registry, capability_profile=profile)
            return bind_card_program_runtime(p, capability_registry=registry, profile=profile)['strict_capability_ready']

        subjects = ('Angelic Arbiter', 'Grizzly Bears', 'Vedalken Orrery')
        baseline = verified(deepcopy(raw))
        for name in subjects:
            self.assertTrue(admitted(name, baseline), name)
        for missing in ('casting.prohibition.fixed_public', 'combat.block.flying'):
            with self.subTest(withdrawn=missing):
                rows = deepcopy(raw)
                next(row for row in rows['capabilities'] if row['id'] == missing).update(
                    status='blocked', blockers=['Exact Arbiter owner withdrawn'])
                registry = verified(rows)
                closure = registry.closure((missing,), profile=profile)
                self.assertFalse(closure.trusted)
                self.assertIn('status:' + missing + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in value for value in closure.blockers))
                self.assertFalse(admitted(subjects[0], registry))
                for name in subjects[1:]:
                    self.assertTrue(admitted(name, registry), name)
