"""A sealed paid creature fact feeds only represented fixed result slots."""
import unittest
from pathlib import Path
import json
import tempfile
from unittest.mock import patch

from common import ROOT
import test_activation_paid_sacrifice_stats as paid_actions
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardRecord, CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, scalar_amount_specs
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class ActivationPaidResultSlotCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.registry = load_default_capability_registry()

    def card(self, body):
        return CardRecord(oracle_id='fixture:activation-paid-slots', name='Paid Slot Witness',
            mana_cost='{2}', mana_value=2, type_line='Artifact',
            oracle_text='{T}, Sacrifice a creature: ' + body, power=None, toughness=None,
            loyalty=None, defense=None, colors=(), color_identity=(), keywords=(),
            produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={})

    def compile(self, body):
        return compile_oracle_card(self.card(body), capability_registry=self.registry,
            capability_profile='commander_review')

    def test_closed_paid_result_slots_keep_one_fixed_price_and_each_primitive_owner(self):
        bodies = (
            "Draw cards equal to the sacrificed creature's power.",
            "Create X 2/2 black Zombie creature tokens, where X is the sacrificed creature's power.",
            "Create an X/X blue Zombie creature token, where X is the sacrificed creature's toughness.",
            "Put a number of +1/+1 counters on this artifact equal to the sacrificed creature's toughness.",
            "Paid Slot Witness gets +X/+0 until end of turn, where X is the sacrificed creature's power.",
            "This artifact deals damage equal to the sacrificed creature's power to any target.",
            "Target player mills cards equal to the sacrificed creature's power.",
            "You gain life equal to the sacrificed creature's toughness. Draw a card.",
            "Draw cards equal to the sacrificed creature's power, then discard three cards.",
            "You gain X life and put X +1/+1 counters on target creature you control, where X is the sacrificed creature's power.",
        )
        class NoRulings:
            def rulings(self, card): return ()
        for body in bodies:
            with self.subTest(body=body):
                card = self.card(body); ir = self.compile(body)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                program = _compile_best_available_card_program_from_ir(NoRulings(), card, ir,
                    semantic_registry=SemanticRegistry(), capability_registry=self.registry,
                    capability_profile='commander_review')
                result = bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')
                self.assertTrue(result['strict_capability_ready'], result['blockers'])
                specs = scalar_amount_specs([a.effects for a in program.abilities])
                self.assertTrue(specs)
                self.assertTrue(all(s.origin is ScalarAmountOrigin.PAID_ACTIVATION_CREATURE for s in specs))

    def test_variable_target_domains_mana_and_unconsumed_siblings_stay_material(self):
        for body in (
            "Destroy target creature with toughness less than the sacrificed creature's toughness.",
            "Add an amount of {B} equal to the sacrificed creature's power.",
            "Draw cards equal to the sacrificed creature's power. Invent an unknown result.",
            "Create X 2/2 black Zombie creature tokens, where X is the sacrificed creature's power. Then secretly change an opponent's hand.",
        ):
            with self.subTest(body=body):
                self.assertNotEqual('exact', self.compile(body).status)

    def test_paid_quantity_cannot_replace_its_actual_token_result_owner(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        body = "Create X 2/2 black Zombie creature tokens, where X is the sacrificed creature's power."
        ir = self.compile(body)
        self.assertEqual('exact', ir.status)
        node, = ir.faces[0].nodes
        owners = [c for c in node.capability_dependencies if c.startswith('token.creation.')]
        self.assertTrue(owners)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for owner in owners:
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == owner).update(
                status='blocked', blockers=['Withheld actual token result owner'])
            blocked = compile_oracle_card(self.card(body), capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review')
            self.assertNotEqual('exact', blocked.status)


class ActivationPaidResultSlotNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = paid_actions.ActivationPaidSacrificeStatNativeTests.reject
    finish = paid_actions.ActivationPaidSacrificeStatNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'paid-slots.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/activation-paid-result-slot-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Paid result slots',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def scene(self, name, *, zero_power=False):
        s = self.session(602300 + len(name))
        source = self.add(s.engine, name)
        self.add(s.engine, 'Vedalken Orrery')
        if name == 'Scourge of Skola Vale': source.counters['+1/+1'] = 2
        body = self.add(s.engine, 'Spined Wurm', ref='paid-slot-body')
        # Current battlefield stats deliberately differ from the graveyard
        # base and distinguish power from toughness without copying counters.
        body.counters['+1/+1'] = 1
        if zero_power:
            body = self.add(s.engine, 'Darksteel Myr', ref='paid-zero-body')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        bounce = self.add(s.engine, 'Boomerang', seat='B', zone='hand')
        copy = self.add(s.engine, 'Cackling Counterpart', zone='hand')
        self.assert_closed(s, source)
        s.state.config.semantic_policy = 'trusted_only'
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.state.started = True; s.state.active_player = 'A'
        s.state.phase = 'precombat_main'; s.state.step = 'main'; s.state.phase_index = 3
        s.state.players['A'].mana_pool.update({'C': 30, **{c: 8 for c in 'WUBRG'}})
        s.state.players['B'].mana_pool['U'] = 4
        source.acquired_control_turn_count = body.acquired_control_turn_count = -1
        s.engine._grant_priority('A'); s.engine.pump(); self.checkpoint(s)
        return s, source, body, private, bounce, copy

    def activate(self, s, source, body, *, targets=()):
        self.priority(s, 'A')
        current = s.state.cards[source.object_id]
        ability = next(a for a in s.engine._activated_abilities(current)
            if a.choices and a.choices[0].fixed_zone_change_cost() is not None
            and a.choices[0].fixed_zone_change_cost().operation == 'sacrifice_one')
        action_id = 'activate:' + current.ref + ':' + ability.ability_id
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(a['id'] == action_id for a in actions))
        command = {'action_id': action_id, 'cost_objects': [body.ref], 'targets': list(targets), 'pay': 'auto'}
        self.reject(s, 'D', command)
        accepted = s.act('pilot:A', command); self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_token_results_keep_quantity_and_characteristics_in_their_own_slots(self):
        for name, count, stats in (('Ghoulcaller Gisa', 6, (2, 2)),
                ('Korozda Guildmage', 5, (1, 1)), ('Geralf, Visionary Stitcher', 1, (5, 5))):
            with self.subTest(name=name):
                s, source, body, private, bounce, copy = self.scene(name)
                self.activate(s, source, body); s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
                self.assertEqual(count, len(tokens))
                self.assertTrue(all(tuple(s.engine._numeric_stat(c.object_id, k)
                    for k in ('power', 'toughness')) == stats for c in tokens))
                if name == 'Geralf, Visionary Stitcher':
                    self.assertIn('flying', s.engine._combat_keywords(tokens[0]))
                self.finish(s, private)

    def test_actual_source_counter_and_modifier_results_do_not_follow_a_new_source_incarnation(self):
        for name in ('Scourge of Skola Vale', 'Dina, Soul Steeper'):
            for departed in (False, True):
                with self.subTest(name=name, departed=departed):
                    s, source, body, private, bounce, copy = self.scene(name)
                    self.activate(s, source, body)
                    if departed:
                        self.cast(s, 'B', bounce, [source.ref]); self.resolve_top(s)
                        self.cast(s, 'A', source); self.resolve_top(s)
                    s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
                    if name == 'Scourge of Skola Vale':
                        self.assertEqual(2 if departed else 7, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                    else:
                        self.assertEqual(1 if departed else 7, s.engine._numeric_stat(source.object_id, 'power'))
                    self.finish(s, private)

    def test_actual_paid_damage_keeps_target_legality_and_lifelink_dealt_amount(self):
        for name in ('Bloodshot Cyclops', 'Brion Stoutarm'):
            with self.subTest(name=name):
                s, source, body, private, bounce, copy = self.scene(name)
                self.activate(s, source, body, targets=['B'])
                self.cast(s, 'B', bounce, [source.ref]); self.resolve_top(s)
                s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
                self.assertEqual(34, s.state.players['B'].life)
                self.assertEqual(46 if name == 'Brion Stoutarm' else 40, s.state.players['A'].life)
                self.finish(s, private)

    def test_actual_mill_reads_paid_power_and_zero_still_pays_without_milling(self):
        for zero in (False, True):
            with self.subTest(zero=zero):
                self.paid_mill(zero)

    def paid_mill(self, zero):
        s, source, body, private, bounce, copy = self.scene('Altar of Dementia', zero_power=zero)
        library = len(s.state.players['B'].zones['library'])
        self.activate(s, source, body, targets=['B'])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(library - (0 if zero else 6), len(s.state.players['B'].zones['library']))
        self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
        self.finish(s, private)

    def test_wrong_paid_stat_is_killed_by_actual_mill_consequences(self):
        from quorune.activation_paid_characteristic_model import resolve_activation_paid_creature_stat
        def wrong_stat(item, characteristic):
            return resolve_activation_paid_creature_stat(item, 'toughness')
        with patch('quorune.activation_paid_characteristic_model.resolve_activation_paid_creature_stat', wrong_stat):
            with self.assertRaises(AssertionError): self.paid_mill(False)

    def test_zero_power_still_pays_but_does_not_create_any_token(self):
        s, source, body, private, bounce, copy = self.scene('Ghoulcaller Gisa', zero_power=True)
        self.activate(s, source, body)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertFalse(any(c.is_token and c.zone == 'battlefield' for c in s.state.cards.values()))
        self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
        self.finish(s, private)

    def test_actual_ordered_life_draw_and_draw_discard_keep_all_consequences_private(self):
        for name in ('Bushmeat Poacher', 'Greater Good'):
            with self.subTest(name=name):
                s, source, body, private, bounce, copy = self.scene(name)
                hand = len(s.state.players['A'].zones['hand'])
                self.activate(s, source, body)
                s = self.replay(s, load=True); decision = self.resolve(s)
                if name == 'Greater Good':
                    self.assertIsNotNone(decision)
                    self.assertEqual(hand + 6, len(s.state.players['A'].zones['hand']))
                    refs = [s.state.cards[c].ref for c in s.state.players['A'].zones['hand'][:3]]
                    command = {'action_id': 'choose', 'cards': refs}
                    self.reject(s, 'C', command)
                    result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(s))
                    self.assertEqual(hand + 3, len(s.state.players['A'].zones['hand']))
                else:
                    self.assertIsNone(decision)
                    self.assertEqual((45, hand + 1), (s.state.players['A'].life, len(s.state.players['A'].zones['hand'])))
                self.finish(s, private)

    def test_actual_dina_mixed_results_use_one_paid_value_and_illegal_target_does_not_refund(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                s, source, body, private, bounce, copy = self.scene('Dina, Essence Brewer')
                # This trigger draws for the paid sacrifice, independently of
                # the resolving life/counter instructions on the activation.
                self.activate(s, source, body, targets=[source.ref])
                if blink:
                    self.cast(s, 'B', bounce, [source.ref]); self.resolve_top(s)
                    self.cast(s, 'A', source); self.resolve_top(s)
                s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
                self.assertEqual(40 if blink else 46, s.state.players['A'].life)
                self.assertEqual(0 if blink else 6, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
                self.finish(s, private)

    def test_actual_life_chisel_is_phase_gated_and_resolves_paid_toughness_during_upkeep(self):
        from quorune.model import TurnEntry
        s, source, body, private, bounce, copy = self.scene('Life Chisel')
        ability = next(a for a in s.engine._activated_abilities(source) if a.choices)
        command = {'action_id': 'activate:' + source.ref + ':' + ability.ability_id,
            'cost_objects': [body.ref], 'targets': [], 'pay': 'auto'}
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'] == command['action_id'] for a in actions))
        self.reject(s, 'A', command)
        s.engine._begin_turn(TurnEntry(turn_id='chisel-upkeep-' + str(s.state.turn_sequence + 1),
            player='A', created_sequence=s.state.turn_sequence))
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.engine.pump(); self.checkpoint(s)
        self.activate(s, source, body)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(45, s.state.players['A'].life)
        self.finish(s, private)


if __name__ == '__main__': unittest.main()
