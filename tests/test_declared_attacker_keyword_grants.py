"""Whole-card declaration-time filters and attacker-bound keyword grants."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY, QUALIFIED_ATTACK_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


DREADHORDE = 'Dreadhorde Invasion'
REYAV = 'Reyav, Master Smith'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'
PINNED = FIXTURES / 'declared-attacker-keyword-grants-cards.json'


class DeclaredAttackerKeywordGrantTests(unittest.TestCase):
    def test_originals_use_declared_recipients_and_retain_every_ability(self):
        reg = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'attacker-keyword.sqlite3'
            build_fixture_database(PINNED, path)
            with CardDatabase(path) as db:
                for name, grant in ((DREADHORDE, 'Lifelink'), (REYAV, 'Double Strike')):
                    with self.subTest(original=name):
                        card = db.lookup(name, fuzzy=False)
                        ir = compile_oracle_card(card, capability_registry=reg, capability_profile=PROFILE)
                        self.assertEqual('exact', ir.status, ir.material_residuals)
                        self.assertEqual(('step.begin', 'creature.attacks') if name == DREADHORDE
                            else ('creature.attacks',), tuple(n.event for n in ir.faces[0].nodes))
                        attack = ir.faces[0].nodes[-1]
                        self.assertIsNone(attack.target_schema)
                        self.assertTrue({QUALIFIED_ATTACK_CAPABILITY, ATTACK_CHARACTERISTIC_RESULT_CAPABILITY}
                            <= set(attack.capability_dependencies))
                        grant_effect, = attack.effects
                        self.assertEqual(('$event.permanent_object', [grant]), (grant_effect['card'], grant_effect['keywords']))
                        ready, _ = register_card(db, reg, name)
                        self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
                        if name == DREADHORDE:
                            upkeep = ir.faces[0].nodes[0]
                            self.assertTrue({'keyword_action.amass.fixed', 'life.change.effect'} <= set(upkeep.capability_dependencies))
                            self.assertIn({'field': 'attacker_characteristics_power', 'op': 'gte', 'value': 6},
                                attack.event_condition['all'])
                            self.assertIn({'field': 'attacker_characteristics_token', 'op': 'eq', 'value': True},
                                attack.event_condition['all'])
                        else:
                            either = {'any': [{'field': 'attacker_characteristics_' + field, 'op': 'eq', 'value': True}
                                for field in ('enchanted', 'equipped')]}
                            self.assertIn(either, attack.event_condition['all'])

                pending, _ = register_card(db, reg, 'Master Symmetrist')
                self.assertFalse(pending['strict_capability_ready'])
                card = db.lookup(DREADHORDE, fuzzy=False)
                for old, unsupported in (('6 or greater', 'X or greater'), ('6 or greater', '6 or less'),
                        ('Zombie token', 'Zombie or Goblin token'), ('until end of turn', 'until your next turn')):
                    with self.subTest(excluded=unsupported):
                        attacked = replace(card, oracle_text=card.oracle_text.replace(old, unsupported, 1))
                        result = compile_oracle_card(attacked, capability_registry=reg, capability_profile=PROFILE)
                        self.assertNotEqual('exact', result.status)


class DeclaredAttackerKeywordGrantNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'attacker-keyword-native.sqlite3'
        build_fixture_database([
            basic, FIXTURES / 'targeted-graveyard-exile-cards.json',
            FIXTURES / 'upkeep-creature-token-absence-cards.json',
            FIXTURES / 'controller-creature-keyword-absence-cards.json',
            FIXTURES / 'fixed-emblem-event-cards.json', PINNED,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Declaration keyword grants', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def creature_token(self, session, *, name, subtypes, strength):
        ref, = session.engine.create_token('A', name=name, characteristics={
            'type_line': 'Token Creature — ' + subtypes, 'colors': ['B'],
            'power': str(strength), 'toughness': str(strength),
        }, reason='Prepared battlefield for declared-attack evidence')
        card = next(card for card in session.state.cards.values() if card.ref == ref)
        card.acquired_control_turn_count = -1
        return card

    def privacy_and_replay(self, game, hidden):
        for seat in game.engine.active_seats:
            if seat != hidden.owner:
                packet = json.dumps(game.packet('pilot:' + seat, full=True))
                self.assertNotIn(hidden.ref, packet)
                self.assertNotIn(hidden.printed_name, packet)
        self.replay(game, load=True)

    def test_paid_invasion_amasses_then_grants_lifelink_from_the_sealed_six_power_attack(self):
        game = self.session(50830070)
        provider = self.add(game.engine, DREADHORDE, zone='hand')
        shrink = self.add(game.engine, 'Disfigure', seat='B', zone='hand')
        hidden = self.add(game.engine, 'Humble', seat='C', zone='hand', ref='attacker-private-hand')
        self.add(game.engine, 'Vedalken Orrery')
        for index in range(2):
            self.add(game.engine, 'Swamp', ref=f'attacker-invasion-payment-{index}')
        self.add(game.engine, 'Swamp', seat='B')
        army = self.creature_token(game, name='Zombie Army Token', subtypes='Zombie Army', strength=5)
        below = self.creature_token(game, name='Zombie Token', subtypes='Zombie', strength=5)
        for seat in 'ABCD':
            for oid in list(game.state.players[seat].zones['hand'])[4:]:
                if oid not in {provider.object_id, shrink.object_id, hidden.object_id}:
                    game.engine.move_card(oid, 'library', log=False)

        for card in (provider, shrink):
            self.assert_closed(game, card)
        self.prepare(game, active='D', actor='D')
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        self.assertIsNone(self.resolve(game))
        for _ in game.state.turn_order:
            if game.state.active_player == 'A':
                break
            sequence = game.state.turn_sequence
            self.until(game, lambda s: s.state.turn_sequence > sequence)
        self.until(game, lambda s: s.state.active_player == 'A' and s.state.step == 'upkeep')
        self.assertEqual(1, self.trigger_count(game, provider))
        self.resolve_top(game)
        self.assertEqual(39, game.state.players['A'].life)
        self.assertEqual(1, game.state.cards[army.object_id].counters.get('+1/+1'))
        other_life = {seat: game.state.players[seat].life for seat in 'BC'}

        self.attack(game, [(army, 'B'), (below, 'C')])
        self.assertEqual(1, self.trigger_count(game, provider))
        trigger, = game.state.stack
        self.assertEqual(6, trigger.context['attacker_characteristics_power'])
        self.cast(game, 'B', shrink, [army.ref])
        self.resolve_top(game)
        self.assertEqual(4, game.engine._numeric_stat(army.object_id, 'power'))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn('lifelink', game.engine._combat_keywords(game.state.cards[army.object_id]))
        self.assertNotIn('lifelink', game.engine._combat_keywords(game.state.cards[below.object_id]))
        self.until(game, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual(other_life['B'] - 4, game.state.players['B'].life)
        self.assertEqual(other_life['C'] - 5, game.state.players['C'].life)
        self.assertEqual(43, game.state.players['A'].life)
        self.privacy_and_replay(game, hidden)

    def test_paid_reyav_grants_double_strike_once_after_both_attachments_leave(self):
        game = self.session(50830080)
        provider = self.add(game.engine, REYAV, zone='hand')
        bearer = self.add(game.engine, 'Grizzly Bears', ref='keyword-fully-attached-attacker')
        ordinary = self.add(game.engine, 'Grizzly Bears', ref='keyword-unattached-attacker')
        aura = self.add(game.engine, 'Holy Strength')
        equipment = self.add(game.engine, 'Short Sword')
        hidden = self.add(game.engine, 'Humble', seat='D', zone='hand', ref='reyav-private-hand')
        answers = [self.add(game.engine, 'Disenchant', seat='B', zone='hand', ref=f'reyav-response-{i}')
            for i in range(2)]
        for i in range(4):
            self.add(game.engine, 'Plains', seat='B', ref=f'reyav-mana-{i}')
        for card in (bearer, ordinary):
            card.acquired_control_turn_count = -1
        for attachment in (aura, equipment):
            attach_objects(game.state.cards, attachment, bearer,
                source_timestamp=attachment.zone_timestamp, players=game.state.players)
        for card in (provider, aura, equipment, *answers):
            self.assert_closed(game, card)

        self.prepare(game, mana={'R': 1, 'W': 1})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        self.assertIsNone(self.resolve(game))
        self.attack(game, [(bearer, 'B'), (ordinary, 'C')])
        self.assertEqual(1, self.trigger_count(game, provider))
        trigger, = game.state.stack
        self.assertIs(trigger.context['attacker_characteristics_enchanted'], True)
        self.assertIs(trigger.context['attacker_characteristics_equipped'], True)
        for answer, attachment in zip(answers, (aura, equipment), strict=True):
            self.cast(game, 'B', answer, [attachment.ref])
            self.resolve_top(game)
            self.assertEqual('graveyard', game.state.cards[attachment.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn('double strike', game.engine._combat_keywords(game.state.cards[bearer.object_id]))
        self.assertNotIn('double strike', game.engine._combat_keywords(game.state.cards[ordinary.object_id]))
        self.until(game, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual((36, 38), (game.state.players['B'].life, game.state.players['C'].life))
        self.privacy_and_replay(game, hidden)


    def test_wrong_attacker_keyword_breaks_the_actual_reyav_witness(self):
        from unittest.mock import patch

        from quorune.effect_runtime.objects_stack_and_tokens import HANDLERS

        operation = 'apply_source_characteristics_until_end_of_turn'
        original = HANDLERS[operation]
        changed = []

        def first_strike(host, effect, **context):
            if effect.get('keywords') == ['Double Strike']:
                changed.append(effect['card'])
                effect = {**effect, 'keywords': ['First Strike']}
            return original(host, effect, **context)

        with patch.dict(HANDLERS, {operation: first_strike}):
            with self.assertRaisesRegex(AssertionError, 'double strike'):
                self.test_paid_reyav_grants_double_strike_once_after_both_attachments_leave()
        self.assertEqual(1, len(changed))

    def test_verified_attacker_grant_and_amass_withdrawals_block_the_affected_originals(self):
        from copy import deepcopy

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (DREADHORDE, REYAV, 'Grizzly Bears')
        baseline = verified(deepcopy(source))
        for name in names:
            bound, _ = register_card(self.db, baseline, name)
            self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        withdrawals = {
            QUALIFIED_ATTACK_CAPABILITY: {DREADHORDE, REYAV},
            ATTACK_CHARACTERISTIC_RESULT_CAPABILITY: {DREADHORDE, REYAV},
            'keyword_action.amass.fixed': {DREADHORDE},
            'combat.damage.participation.strike_steps': {REYAV},
        }
        for capability, blocked in withdrawals.items():
            with self.subTest(withdrawn=capability):
                rows = deepcopy(source)
                next(row for row in rows['capabilities'] if row['id'] == capability).update(
                    status='blocked', blockers=['Exact attacker grant family dependency withdrawn'])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn('status:' + capability + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in value for value in closure.blockers))
                for name in names:
                    bound, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, bound['strict_capability_ready'], (name, bound['blockers']))
