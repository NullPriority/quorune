"""Independent controlled and defending attack power thresholds on a complete original."""

from dataclasses import replace
import json
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
from quorune.qualified_attack_model import (
    ATTACK_FACT_PREFIX, ATTACK_RECIPIENT_KIND_FIELD, QUALIFIED_ATTACK_CAPABILITY, qualified_player_defense_condition,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_controller_information import occurrence_controller_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MACCREADY = 'MacCready, Lamplight Mayor'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'
ORIGINAL = FIXTURES / 'declared-attacker-equal-stats-cards.json'


class FixedPowerQualifiedAttackTests(unittest.TestCase):
    def test_whole_original_binds_both_attacks_and_rejects_forged_defenders(self):
        registry = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'power-attacks.sqlite3'
            build_fixture_database([ORIGINAL], path)
            with CardDatabase(path) as db:
                card = db.lookup(MACCREADY, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                offense, defense = ir.faces[0].nodes
                self.assertEqual(('creature.attacks', 'creature.attacks'), (offense.event, defense.event))
                self.assertEqual(card.oracle_text.splitlines(),
                    [card.oracle_text[n.span.start:n.span.end] for n in (offense, defense)])
                self.assertIsNone(offense.target_schema)
                self.assertIsNone(defense.target_schema)
                self.assertIn({'field': ATTACK_FACT_PREFIX + 'power', 'op': 'lte', 'value': 2},
                    offense.event_condition['all'])
                self.assertEqual((['Skulk'], '$event.permanent_object'),
                    (offense.effects[0]['keywords'], offense.effects[0]['card']))
                self.assertEqual(qualified_player_defense_condition(4), defense.event_condition)
                self.assertEqual((
                    {'op': 'lose_life', 'player': occurrence_controller_reference(), 'amount': 2},
                    {'op': 'life', 'player': '$controller', 'delta': 2},
                ), defense.effects)
                ready, semantics = register_card(db, registry, MACCREADY)
                self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                    if p.effects and p.effects[0]['op'] == 'lose_life']
                clauses = program.event_condition['all']
                for altered in (
                    [c for c in clauses if c['field'] != ATTACK_RECIPIENT_KIND_FIELD],
                    [{**c, 'value': 'planeswalker'} if c['field'] == ATTACK_RECIPIENT_KIND_FIELD else c for c in clauses],
                    [{**c, 'value': '$source.owner'} if c['field'] == 'defending_player' else c for c in clauses],
                    [{**c, 'value': True} if c['field'] == ATTACK_FACT_PREFIX + 'power' else c for c in clauses],
                ):
                    with self.subTest(altered=altered):
                        raw = bind_semantic_program_runtime(replace(program, event_condition={'all': altered}),
                            capability_registry=registry, profile=PROFILE)
                        self.assertFalse(raw['strict'], raw)
                for old, excluded in (
                    ('attacks you,', 'attacks you or a planeswalker you control,'),
                    ('power 4 or greater', 'toughness 4 or greater'),
                    ('power 2 or less', 'power X or less'),
                ):
                    with self.subTest(excluded=excluded):
                        altered = replace(card, oracle_text=card.oracle_text.replace(old, excluded, 1))
                        self.assertNotEqual('exact', compile_oracle_card(altered,
                            capability_registry=registry, capability_profile=PROFILE).status)


class FixedPowerQualifiedAttackNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'power-attacks-native.sqlite3'
        build_fixture_database([
            basic, ORIGINAL, FIXTURES / 'targeted-graveyard-exile-cards.json',
            FIXTURES / 'source-attack-alone-cards.json', FIXTURES / 'recipient-control-cards.json',
            FIXTURES / 'announced-x-source-discard-cards.json',
            FIXTURES / 'controller-creature-keyword-absence-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Qualified attacking power', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def finish_private(self, game, hidden):
        for seat in game.engine.active_seats:
            if seat != hidden.owner:
                self.assertNotIn(hidden.ref, json.dumps(game.packet('pilot:' + seat, full=True)))
        self.replay(game, load=True)

    def test_paid_skulk_uses_declared_power_then_current_blocking_power(self):
        game = self.session(508300201)
        source = self.add(game.engine, MACCREADY, zone='hand')
        eligible = self.add(game.engine, 'Grizzly Bears', ref='skulk-eligible-attacker')
        excluded = self.add(game.engine, 'Hill Giant', ref='skulk-ineligible-attacker')
        big = self.add(game.engine, 'Colossal Dreadmaw', seat='B')
        small = self.add(game.engine, 'Grizzly Bears', seat='B', ref='skulk-small-blocker')
        boost = self.add(game.engine, 'Giant Growth', zone='hand')
        self.add(game.engine, 'Forest')
        hidden = self.add(game.engine, 'Shock', seat='D', zone='hand', ref='skulk-hidden')
        for card in (eligible, excluded):
            card.acquired_control_turn_count = -1
        for card in (source, eligible, excluded, big, small, boost):
            self.assert_closed(game, card)
        self.prepare(game, mana={'W': 1, 'B': 1})
        self.checkpoint(game)
        self.cast(game, 'A', source)
        self.assertIsNone(self.resolve(game))

        self.attack(game, [(eligible, 'B'), (excluded, 'C')])
        self.assertEqual(1, self.trigger_count(game, source))
        trigger, = game.state.stack
        self.assertEqual(2, trigger.context[ATTACK_FACT_PREFIX + 'power'])
        self.cast(game, 'A', boost, [eligible.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(eligible.object_id, 'power'))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn('skulk', game.engine._combat_keywords(game.state.cards[eligible.object_id]))
        self.assertNotIn('skulk', game.engine._combat_keywords(game.state.cards[excluded.object_id]))
        self.until(game, lambda state: state.state.pending_decision.kind == 'combat.blockers')
        schema = game.packet('pilot:B', full=True)['decision']['ctx']['legal_blocks']
        self.assertIn(eligible.ref, schema[small.ref])
        self.assertNotIn(eligible.ref, schema.get(big.ref, []))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act('pilot:B', {'action_id': 'block', 'blocks': {big.ref: eligible.ref}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act('pilot:B', {'action_id': 'block', 'blocks': {small.ref: eligible.ref}})
        self.assertTrue(result.ok, result.summary)
        self.finish_private(game, hidden)

    def test_paid_defense_locks_the_player_attack_and_reads_the_attackers_current_or_departure_controller(self):
        for leaves in (False, True):
            with self.subTest(leaves=leaves):
                game = self.session(508300220 + leaves)
                source = self.add(game.engine, MACCREADY, zone='hand')
                eligible = self.add(game.engine, 'Rumbling Baloth', seat='B', ref='defense-eligible')
                low = self.add(game.engine, 'Hill Giant', seat='B', ref='defense-below-power')
                wrong = self.add(game.engine, 'Colossal Dreadmaw', seat='B', ref='defense-wrong-seat')
                planeswalker = self.add(game.engine, 'Jace Beleren')
                planeswalker.counters['loyalty'] = 3
                attacks_walker = self.add(game.engine, 'Rumbling Baloth', seat='B', ref='defense-wrong-kind')
                transfer = self.add(game.engine, 'Act of Treason', seat='C', zone='hand')
                response = self.add(game.engine, 'Unsummon' if leaves else 'Humble', seat='B', zone='hand')
                hidden = self.add(game.engine, 'Shock', seat='D', zone='hand', ref='defense-private')
                self.add(game.engine, 'Vedalken Orrery', ref='defense-source-timing')
                self.add(game.engine, 'Vedalken Orrery', seat='C', ref='defense-transfer-timing')
                self.add(game.engine, 'Mountain', seat='C')
                self.add(game.engine, 'Plains', seat='C', ref='defense-transfer-first')
                self.add(game.engine, 'Plains', seat='C', ref='defense-transfer-second')
                if leaves:
                    self.add(game.engine, 'Island', seat='B')
                else:
                    for index in range(2):
                        self.add(game.engine, 'Plains', seat='B', ref=f'defense-humble-{index}')
                for card in (eligible, low, wrong, attacks_walker):
                    card.acquired_control_turn_count = -1
                for card in (source, eligible, low, wrong, planeswalker, transfer, response):
                    self.assert_closed(game, card)
                self.prepare(game, actor='A', active='B', mana={'W': 1, 'B': 1})
                self.checkpoint(game)
                self.cast(game, 'A', source)
                self.assertIsNone(self.resolve(game))

                self.until(game, lambda state: state.state.pending_decision.kind == 'combat.attackers')
                command = {'action_id': 'attack', 'attackers': {
                    eligible.ref: 'A', low.ref: 'A', wrong.ref: 'C', attacks_walker.ref: planeswalker.ref,
                }}
                attacked = game.act('pilot:B', command)
                self.assertTrue(attacked.ok, attacked.summary)
                trigger, = game.state.stack
                self.assertEqual(source.object_id, trigger.source_object_id)
                self.assertEqual(('A', 'player', eligible.object_id, 4), (
                    trigger.context['defending_player'], trigger.context[ATTACK_RECIPIENT_KIND_FIELD],
                    trigger.context['card_object_id'], trigger.context[ATTACK_FACT_PREFIX + 'power'],
                ))
                self.cast(game, 'C', transfer, [eligible.ref])
                self.resolve_top(game)
                self.assertEqual('C', game.state.cards[eligible.object_id].controller)
                self.cast(game, 'B', response, [eligible.ref])
                self.resolve_top(game)
                if leaves:
                    self.assertEqual('hand', game.state.cards[eligible.object_id].zone)
                else:
                    self.assertEqual(0, game.engine._numeric_stat(eligible.object_id, 'power'))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual((42, 40, 38, 40), tuple(game.state.players[seat].life for seat in 'ABCD'))
                self.finish_private(game, hidden)

    def test_omitting_the_paid_skulk_result_breaks_actual_combat(self):
        from quorune.effect_runtime.objects_stack_and_tokens import HANDLERS

        operation = 'apply_source_characteristics_until_end_of_turn'
        original = HANDLERS[operation]
        removed = []

        def omit_skulk(host, effect, **context):
            if effect.get('keywords') == ['Skulk']:
                removed.append(effect['card'])
                return None
            return original(host, effect, **context)

        with patch.dict(HANDLERS, {operation: omit_skulk}):
            with self.assertRaisesRegex(AssertionError, 'skulk'):
                self.test_paid_skulk_uses_declared_power_then_current_blocking_power()
        self.assertEqual(1, len(removed))

    def test_verified_attack_skulk_and_controller_withdrawals_block_complete_maccready(self):
        from copy import deepcopy

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        baseline = verified(deepcopy(source))
        names = (MACCREADY, 'Grizzly Bears')
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
        for cap in (QUALIFIED_ATTACK_CAPABILITY, 'combat.block.skulk', 'resolution.target_controller_information'):
            with self.subTest(withdrawn=cap):
                rows = deepcopy(source)
                next(r for r in rows['capabilities'] if r['id'] == cap).update(
                    status='blocked', blockers=['Exact qualified attack family dependency withdrawn'])
                registry = verified(rows)
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn('status:' + cap + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name != MACCREADY, ready['strict_capability_ready'], (name, ready['blockers']))
