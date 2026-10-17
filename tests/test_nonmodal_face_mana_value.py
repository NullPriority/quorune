from __future__ import annotations

"""CR 202.3b distinguishes an original back face from a copy of that face."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.carddb_characteristics import base_card_characteristics
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/nonmodal-face-mana-value-cards.json'


def face_record(**changes):
    record = CardRecord(oracle_id='fixture:nonmodal-mana-value', name='Front // Back', mana_cost='{4}{G}',
        mana_value=5, type_line='Creature — Human', oracle_text='', power='2', toughness='3',
        loyalty=None, defense=None, colors=('G',), color_identity=('G',), keywords=(), produced_mana=(),
        layout='transform', released_at='2026-01-01', legalities={'commander': 'legal'}, raw={},
        faces=({'name': 'Front', 'mana_cost': '{4}{G}', 'type_line': 'Creature — Human', 'oracle_text': '',
                'power': '2', 'toughness': '3', 'colors': ['G']},
            {'name': 'Back', 'mana_cost': '', 'type_line': 'Creature — Eldrazi', 'oracle_text': '',
                'power': '7', 'toughness': '5', 'colors': []}))
    return replace(record, **changes)


def face_card(**changes):
    card = CardInstance(object_id='fixture:face-card', ref='face-card', oracle_id='fixture:nonmodal-mana-value',
        printed_name='Front // Back', owner='B', controller='A', zone='battlefield', active_face='Back')
    return replace(card, **changes)


class NonmodalFaceManaValueTests(unittest.TestCase):
    def test_original_back_face_retains_front_value_without_front_cost_or_color(self):
        record = face_record()
        for zone in ('battlefield', 'stack'):
            with self.subTest(zone=zone):
                data = base_card_characteristics(face_card(zone=zone), record)
                self.assertEqual(5, data['mana_value'])
                self.assertEqual('', data['mana_cost'])
                self.assertEqual([], data['colors'])
                self.assertEqual('7', data['power'])
        self.assertEqual(5, base_card_characteristics(face_card(active_face='Front'), record)['mana_value'])
        self.assertEqual(5, base_card_characteristics(face_card(zone='graveyard', active_face=None), record)['mana_value'])

    def test_modal_faces_and_split_selected_stack_and_prospective_views_keep_their_values(self):
        faces = (face_record().faces[0], {**face_record().faces[1], 'mana_cost': '{2}{U}', 'colors': ['U']})
        modal = face_record(layout='modal_dfc', faces=faces)
        for zone in ('battlefield', 'stack'):
            self.assertEqual(3, base_card_characteristics(face_card(zone=zone), modal)['mana_value'])
        split = face_record(layout='split', faces=faces, mana_value=8)
        self.assertEqual(3, base_card_characteristics(face_card(zone='stack'), split)['mana_value'])
        self.assertEqual(8, base_card_characteristics(face_card(zone='graveyard'), split)['mana_value'])
        self.assertEqual(3, base_card_characteristics(face_card(zone='graveyard',
            annotations={'prospective_cast_face': True}), split)['mana_value'])

    def test_copiable_back_face_value_is_derived_from_its_cost_and_keeps_copy_exceptions(self):
        from quorune.carddb_characteristics import copiable_card_characteristics
        record = face_record()
        card = face_card()
        source = base_card_characteristics(card, record)
        before = deepcopy(source)
        copied = copiable_card_characteristics(card, record, source)
        self.assertEqual(0, copied['mana_value'])
        self.assertEqual('', copied['mana_cost'])
        self.assertEqual(before, source)
        # A prior copy effect can replace that face's copiable mana cost.
        changed = face_card(annotations={'copy_overrides': {'mana_cost': '{2}{U}', 'mana_value': 3}})
        self.assertEqual(3, copiable_card_characteristics(changed, record, source)['mana_value'])
        normal = face_record(layout='normal', faces=(), name='Normal')
        self.assertEqual(5, copiable_card_characteristics(face_card(active_face=None), normal,
            base_card_characteristics(face_card(active_face=None), normal))['mana_value'])


class NonmodalFaceManaValueRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    def add(self, engine, name, **kwargs):
        card = actions.SpreeModeCostRuntimeTests.add(self, engine, name, **kwargs)
        record = self.db.lookup(name)
        if record.faces and card.zone == 'battlefield':
            card.active_face = str(record.faces[0]['name'])
        return card

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'nonmodal-value.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Nonmodal value witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def transform(self, session, card, **payment):
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:' + card.ref + ':') and 'Transform' in row['label'])
        self.reject(session, action['id'], 'C', pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        return session.state.cards[card.object_id]

    def test_actual_kessig_transform_keeps_one_mana_value(self):
        session = self.session(20203002)
        source = self.add(session.engine, 'Kessig Prowler // Sinuous Predator', ref='value-kessig')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'G': 1})
        self.checkpoint(session)
        current = self.transform(session, source)
        self.assertEqual('Sinuous Predator', current.active_face)
        self.assertEqual(1, session.engine._effective_card_data(current)['mana_value'])
        self.assertEqual('', session.engine._effective_card_data(current)['mana_cost'])
        self.replay(session, load=True)

    def test_actual_eliminate_rejects_transformed_five_mana_creature(self):
        session = self.session(20203003)
        source = self.add(session.engine, 'Bonded Herdbeast // Plated Kilnbeast', ref='value-herd')
        removal = self.add(session.engine, 'Eliminate', zone='hand', ref='value-removal')
        self.add(session.engine, 'Generic Bound Body', seat='B', ref='value-small-contrast')
        self.assert_closed(session, source)
        self.assert_closed(session, removal)
        self.prepare(session, mana={'C': 5, 'B': 1})
        self.checkpoint(session)
        current = self.transform(session, source)
        self.assertEqual('Plated Kilnbeast', current.active_face)
        self.reject(session, 'cast:' + removal.ref, targets=[current.ref], pay='auto')
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_actual_clone_of_back_face_is_zero_and_eliminate_uses_that_value(self):
        session = self.session(20203004)
        engine = session.engine
        source = self.add(engine, 'Bonded Herdbeast // Plated Kilnbeast', ref='value-original-back')
        clone = self.add(engine, 'Clone', zone='hand', ref='value-physical-clone')
        removal = self.add(engine, 'Eliminate', zone='hand', ref='value-copy-removal')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='value-private')
        for card in (source, clone, removal):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 8, 'U': 1, 'B': 1})
        self.checkpoint(session)
        current = self.transform(session, source)
        self.assertEqual(5, session.engine._effective_card_data(current)['mana_value'])
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        self.cast(session, 'A', clone)
        pending = self.resolve(session)
        self.assertEqual('replacement.order', pending.kind)
        session = self.replay(session, load=True)
        option = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['options']
            if current.ref in row['id'] and not row['id'].startswith('decline:'))
        self.reject(session, 'choose', 'D', replacement=option['id'])
        result = session.act('pilot:A', {'action_id': 'choose', 'replacement': option['id']})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        copied = session.state.cards[clone.object_id]
        self.assertEqual(0, session.engine._effective_card_data(copied)['mana_value'])
        self.assertEqual('', session.engine._effective_card_data(copied)['mana_cost'])
        self.assertEqual(5, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        self.reject(session, 'cast:' + removal.ref, targets=[session.state.cards[source.object_id].ref], pay='auto')
        self.cast(session, 'A', removal, [copied.ref])
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[clone.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_actual_token_copy_of_back_face_has_zero_mana_value(self):
        session = self.session(20203005)
        engine = session.engine
        source = self.add(engine, 'Bonded Herdbeast // Plated Kilnbeast', ref='value-token-original')
        spell = self.add(engine, 'Cackling Counterpart', zone='hand', ref='value-token-copy')
        self.assert_closed(session, source)
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 5, 'U': 2})
        self.checkpoint(session)
        current = self.transform(session, source, reserve={'U': 2})
        self.assertEqual(5, session.engine._effective_card_data(current)['mana_value'])
        self.cast(session, 'A', spell, [current.ref])
        self.resolve(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        self.assertEqual(0, session.engine._effective_card_data(tokens[0])['mana_value'])
        self.assertEqual('', session.engine._effective_card_data(tokens[0])['mana_cost'])
        self.replay(session, load=True)

    def test_original_face_value_mutation_is_detected_by_actual_transform(self):
        from quorune.rules.face_characteristic_values import printed_face_mana_value
        with mock.patch('quorune.carddb_characteristics.current_face_mana_value',
                side_effect=lambda record, face: printed_face_mana_value(face)):
            with self.assertRaises(AssertionError):
                self.test_actual_kessig_transform_keeps_one_mana_value()

    def test_copying_original_derived_value_is_detected_by_actual_clone(self):
        def old_snapshot(card, record, base):
            values = dict(base)
            values.update(deepcopy(dict(card.annotations.get('copy_overrides') or {})))
            return values
        with mock.patch('quorune.engine.copiable_card_characteristics', side_effect=old_snapshot):
            with self.assertRaises(AssertionError):
                self.test_actual_clone_of_back_face_is_zero_and_eliminate_uses_that_value()
