from __future__ import annotations

"""CR 106/605: a mana ability uses its source's retained public color."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from common import ROOT
from quorune.abilities import ActivatedAbility
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.mana_abilities import fixed_mana_specs_from_descriptors
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


OWNER = 'mana.production.source_chosen_color'


class ChosenColorManaCompilerTests(unittest.TestCase):
    def test_chosen_color_output_costs_and_codec_close_without_any_color_fallback(self):
        registry = load_default_capability_registry()
        for cost, body, amount in (
            ('{T}', 'Add one mana of the chosen color.', 1),
            ('{T}, Sacrifice this artifact', 'Add two mana of the chosen color.', 2),
            ('{T}, Sacrifice an artifact', 'Add two mana of the chosen color.', 2),
            ('{1}, {T}', 'Add {W} or one mana of the chosen color.', 1),
            ('{T}', 'Add four mana of the chosen color.', 4),
        ):
            with self.subTest(cost=cost, body=body):
                ir = compile_oracle_card(record(cost + ': ' + body, 'Artifact'),
                                        capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIn(OWNER, node.capability_dependencies)
                spec, = fixed_mana_specs_from_descriptors(node.handlers)
                ability = spec.to_activated_ability()
                self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
                self.assertEqual((), ability.fixed_mana_outputs)
                self.assertEqual('source_chosen_color_mana', ability.dynamic_mana_output['kind'])
                self.assertEqual(amount, ability.dynamic_mana_output['amount'])
                self.assertEqual(body, ability.effect_text)

    def test_open_amounts_choices_results_and_mandatory_costs_stay_residual(self):
        registry = load_default_capability_registry()
        for text in (
            '{T}: Add X mana of the chosen color.',
            '{T}: Add one mana of the color chosen by an opponent.',
            '{T}: Add one mana of the chosen color. Draw a card.',
            '{T}: Add one mana of the chosen color. Spend this mana only to rewrite history.',
            '{T}, Pay an unrepresented fee: Add one mana of the chosen color.',
            '{T}: Add zero mana of the chosen color.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(record(text, 'Artifact'),
                    capability_registry=registry, capability_profile='commander_review').status)

    def test_chosen_output_descriptor_and_dependency_withdrawal_fail_closed(self):
        text = '{T}: Add one mana of the chosen color.'
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (OWNER, 'zone.entry.public_designation', 'mana.activated.fixed_output', 'mana.activated.restricted_fixed_output'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['chosen output owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Artifact'),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)
        ir = compile_oracle_card(record(text, 'Artifact'), capability_registry=load_default_capability_registry(),
                                 capability_profile='commander_review')
        spec, = fixed_mana_specs_from_descriptors(ir.faces[0].nodes[0].handlers)
        for change in ({'amount': True}, {'amount': 0}, {'kind': 'any_color'}, {'unknown': True}):
            payload = spec.to_activated_ability().to_dict()
            payload['dynamic_mana_output'].update(change)
            with self.assertRaises(ValueError):
                ActivatedAbility.from_dict(payload)

    def test_current_designation_modes_and_opponent_land_color_reader_agree(self):
        from quorune.mana_ability_runtime import mana_modes_for_ability, _opponent_land_colors
        ir = compile_oracle_card(record('{T}: Add one mana of the chosen color.', 'Land'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        spec, = fixed_mana_specs_from_descriptors(ir.faces[0].nodes[0].handlers)
        ability = spec.to_activated_ability()
        land = SimpleNamespace(object_id='land', ref='P-land', controller='A', zone='battlefield',
                               phased_out=False, annotations={'chosen_color': 'R'})
        host = SimpleNamespace(active_seats=['A', 'B'], state=SimpleNamespace(cards={'land': land}, players={
            'A': SimpleNamespace(zones={'battlefield': ['land']}), 'B': SimpleNamespace(zones={'battlefield': []})}),
            _effective_card_data=lambda card: {'type_line': 'Land'},
            _type_parts=lambda text: ({'land'}, set(), set()), _activated_abilities=lambda card: (ability,))
        for color in ('R', 'U'):
            land.annotations['chosen_color'] = color
            mode, = mana_modes_for_ability(host, 'A', land, ability)
            self.assertEqual({color: 1}, {k: v for k, v in mode.bundle.items() if v})
            self.assertEqual((color,), _opponent_land_colors(host, 'B'))
        for value in (None, 'C', 'blue'):
            land.annotations['chosen_color'] = value
            self.assertEqual((), mana_modes_for_ability(host, 'A', land, ability))
            self.assertEqual((), _opponent_land_colors(host, 'B'))


class ChosenColorManaRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'chosen-color-mana.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json', ROOT / 'tests/fixtures/granted-skulk-cards.json',
            ROOT / 'tests/fixtures/chosen-color-mana-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Chosen mana witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def choose_color(self, session, color):
        self.resolve(session)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        selected = next(row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['options']
                        if row['id'].endswith(':' + color))
        result = session.act('pilot:A', {'action_id': 'choose', 'choices': {'replacement': selected}})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)

    def action(self, session, source, ability, actor='A', required=True):
        for _ in range(16):
            if session.state.priority_player == actor:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        rows = session.packet('pilot:' + actor, full=True)['decision']['ctx']['legal']['actions']
        action = next((row for row in rows if row.get('source') == source.ref and row.get('ability') == ability), None)
        if required:
            self.assertIsNotNone(action, 'Missing supported chosen-color mana action')
        return action

    def reject(self, session, action, actor='A', **response):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + actor, {'action_id': action, **response}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_actual_grail_cast_choice_explicit_and_automatic_payment_use_only_chosen_color(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                session = self.session(414000 + automatic)
                source = self.add(session.engine, 'Sol Grail', zone='hand', ref='chosen-mana-original-grail')
                spell = self.add(session.engine, 'Generic Chosen Red Spell', zone='hand', ref='chosen-mana-red-payment')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='chosen-mana-private')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 3})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.resolve(session)
                session = self.replay(session, load=True)
                self.choose_color(session, 'R')
                source = session.state.cards[source.object_id]
                action = self.action(session, source, 'ab2')
                self.reject(session, action['id'], 'D', mana_output={'R': 1})
                self.reject(session, action['id'], mana_output={'U': 1})
                self.reject(session, action['id'], mana_output={'R': 2})
                if not automatic:
                    result = session.act('pilot:A', {'action_id': action['id'], 'mana_output': {'R': 1}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertFalse(session.state.stack)
                    self.assertEqual(1, session.state.players['A'].mana_pool['R'])
                self.cast(session, 'A', spell)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.resolve(session)
                self.assertEqual(41, session.state.players['A'].life)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_actual_quirion_fresh_tap_guard_and_real_control_change_keep_source_color(self):
        session = self.session(414010)
        source = self.add(session.engine, 'Quirion Elves', zone='hand', ref='chosen-mana-original-elf')
        control = self.add(session.engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='chosen-mana-real-control')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'G': 1})
        session.state.players['D'].mana_pool.update(W=1)
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.choose_color(session, 'R')
        self.assertIsNone(self.action(session, source, 'ab3', required=False))
        self.reject(session, 'activate:' + source.ref + ':ab3', mana_output={'R': 1})
        self.cast(session, 'D', control, [source.ref])
        self.resolve(session)
        session = self.replay(session, load=True)
        source = session.state.cards[source.object_id]
        self.assertEqual('D', source.controller)
        self.assertEqual('R', source.annotations['chosen_color'])
        action = self.action(session, source, 'ab3', actor='D')
        self.reject(session, action['id'], 'A', mana_output={'R': 1})
        result = session.act('pilot:D', {'action_id': action['id'], 'mana_output': {'R': 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((0, 1), (session.state.players['A'].mana_pool['R'], session.state.players['D'].mana_pool['R']))
        self.assertTrue(source.tapped)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_actual_coldsteel_tapped_entry_untap_and_reentry_reset_the_color(self):
        session = self.session(414020)
        source = self.add(session.engine, 'Coldsteel Heart', zone='hand', ref='chosen-mana-original-heart')
        untap = self.add(session.engine, 'Generic Chosen Mana Untap', zone='hand', ref='chosen-mana-real-untap')
        bounce = self.add(session.engine, 'Generic Skulk Bounce', zone='hand', ref='chosen-mana-real-bounce')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.choose_color(session, 'U')
        self.assertTrue(source.tapped)
        self.assertIsNone(self.action(session, source, 'ab3', required=False))
        self.cast(session, 'A', untap, [source.ref])
        self.resolve(session)
        action = self.action(session, source, 'ab3')
        result = session.act('pilot:A', {'action_id': action['id']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players['A'].mana_pool['U'])
        self.cast(session, 'A', bounce, [source.ref])
        self.resolve(session)
        self.assertNotIn('chosen_color', session.state.cards[source.object_id].annotations)
        self.cast(session, 'A', session.state.cards[source.object_id])
        self.choose_color(session, 'R')
        self.assertEqual('R', session.state.cards[source.object_id].annotations['chosen_color'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session, load=True)

    def test_source_and_selected_source_sacrifice_keep_original_choice_and_rollback_bad_output(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                session = self.session(414030 + selected)
                source = self.add(session.engine, 'Generic Chosen Selected Sacrifice' if selected else 'Generic Chosen Source Sacrifice',
                                  zone='hand', ref='chosen-mana-paid-source')
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.choose_color(session, 'R')
                action = self.action(session, source, 'ab2')
                cost = {'cost_cards': [source.ref]} if selected else {}
                self.reject(session, action['id'], mana_output={'U': 2}, **cost)
                result = session.act('pilot:A', {'action_id': action['id'], 'mana_output': {'R': 2}, **cost})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.assertNotIn('chosen_color', session.state.cards[source.object_id].annotations)
                self.assertEqual(2, session.state.players['A'].mana_pool['R'])
                self.assertFalse(session.state.stack)
                self.replay(session, load=True)
