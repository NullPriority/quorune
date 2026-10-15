from __future__ import annotations

import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_public_query_effect_amounts import query_amount_record


class MultisentenceEffectCompilerTests(unittest.TestCase):
    def test_multisentence_component_omission_and_dependency_withdrawal_fail_closed(self):
        import json
        from common import ROOT
        from unittest.mock import patch
        from quorune.rules.capabilities import CapabilityRegistry
        text = ('Look at the top three cards of your library. You may reveal a permanent card from among them '
            'and put it into your hand. Put the rest on the bottom of your library in any order. You gain 3 life.')
        record = query_amount_record(text, suffix=365205004)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('library.select.fixed_controller', 'resolution.effect_program.closed_components'):
            from copy import deepcopy
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == cap).update(status='blocked', blockers=['Producer withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(changed)).status)
        with patch('quorune.compiler.multisentence_effect_components.multisentence_effect_program', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record,
                capability_registry=load_default_capability_registry()).status)

    def test_independent_multisentence_selection_keeps_complete_component_and_printed_order(self):
        selection = ('Look at the top three cards of your library. You may reveal a permanent card from among them '
            'and put it into your hand. Put the rest on the bottom of your library in any order.')
        for i, (text, operations) in enumerate(((selection + ' You gain 3 life.', ['fixed_library_selection', 'life']),
                ('Target creature gets +1/+1 until end of turn. ' + selection,
                    ['modify_stats_until_end_of_turn', 'fixed_library_selection']))):
            ir = compile_oracle_card(query_amount_record(text, suffix=365205001 + i),
                capability_registry=load_default_capability_registry())
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertEqual(operations, [e['op'] for e in ir.faces[0].nodes[0].effects])
        for tail in ('If you do, draw a card.', 'That card gains flying until end of turn.',
                'Perform an unrepresented operation.'):
            ir = compile_oracle_card(query_amount_record(selection + ' ' + tail, suffix=365205003),
                capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact', ir.status)


class MultisentenceEffectActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'multisentence.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/multisentence-effect-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Multisentence', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    import test_live_battlefield_quantities as _live
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    replay = _bound.BoundEffectProgramRuntimeTests.replay
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    action = _live.LiveBattlefieldQuantityActionTests.action
    register_fixture_cards_on_add = True

    def top(self, engine, names):
        cards = [self.add(engine, name, zone='library', ref='sequence-top-' + str(i)) for i, name in enumerate(names)]
        library = engine.state.players['A'].zones['library']
        for card in cards:
            library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        return cards

    def test_original_bond_private_partition_finishes_before_life_gain_and_replays(self):
        from quorune.record import authoritative_state_hash
        for selected in (True, False):
            with self.subTest(selected=selected):
                session = self.session(365205010 + selected)
                engine = session.engine
                cards = self.top(engine, ['Generic Bound Body', 'Lightning Bolt', 'Generic Bound Plains'])
                source = self.add(engine, 'Bond of Flourishing', zone='hand')
                offered = self.ready(session, source, {'G': 1, 'C': 1})
                self.checkpoint(session)
                self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                self.assertEqual(40, session.state.players['A'].life)
                for seat in 'BCD':
                    packet = str(session.packet('pilot:' + seat, full=True))
                    for card in cards:
                        self.assertNotIn(card.ref, packet)
                session = self.replay(session, load=True)
                for principal, hand in (('pilot:B', [cards[0].ref]), ('pilot:A', [cards[1].ref])):
                    before = authoritative_state_hash(session.state)
                    denied = session.act(principal, {'action_id': 'choose', 'cards': {
                        'hand': hand, 'bottom': [c.ref for c in cards if c.ref not in hand]}})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                hand = [cards[0].ref] if selected else []
                remaining = [c.ref for c in cards if c.ref not in hand]
                accepted = session.act('pilot:A', {'action_id': 'choose', 'cards': {'hand': hand, 'bottom': remaining}})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual(43, session.state.players['A'].life)
                self.assertEqual('hand' if selected else 'library', session.state.cards[cards[0].object_id].zone)
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.replay(session)

    def test_original_rals_damage_precedes_private_choice_and_invalid_target_stops_all(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                session = self.session(365205020 + blink)
                engine = session.engine
                cards = self.top(engine, ['Generic Bound Body', 'Lightning Bolt'])
                target = self.add(engine, 'Generic Bound Body', ref='rals-target')
                source = self.add(engine, "Ral's Outburst", zone='hand')
                shift = self.add(engine, 'Cloudshift', zone='hand')
                offered = self.ready(session, source, {'R': 1, 'U': 1, 'W': 1, 'C': 3})
                self.checkpoint(session)
                self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'targets': [target.ref], 'pay': 'auto'}).ok)
                if blink:
                    self.action(session, shift, targets=[target.ref])
                    for _ in range(4):
                        self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                    session = self.replay(session, load=True)
                    self.resolve(session)
                    self.assertEqual(0, session.state.cards[target.object_id].marked_damage)
                    self.assertEqual([], session.state.stack)
                    for card in cards:
                        self.assertEqual('library', session.state.cards[card.object_id].zone)
                    self.replay(session)
                else:
                    self.assertEqual('semantic.choice', self.resolve(session).kind)
                    self.assertEqual(3, session.state.cards[target.object_id].marked_damage)
                    session = self.replay(session, load=True)
                    accepted = session.act('pilot:A', {'action_id': 'choose', 'cards': {
                        'hand': [cards[0].ref], 'graveyard': [cards[1].ref]}})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve(session)
                    self.assertEqual('hand', session.state.cards[cards[0].object_id].zone)
                    self.assertEqual('graveyard', session.state.cards[cards[1].object_id].zone)
                    self.assertEqual(3, session.state.cards[target.object_id].marked_damage)
                    self.replay(session)

    def test_original_augury_resumes_into_current_proliferate_choice_once(self):
        session = self.session(365205030)
        engine = session.engine
        cards = self.top(engine, ['Generic Bound Body', 'Lightning Bolt', 'Generic Bound Plains'])
        permanent = self.add(engine, 'Generic Bound Body', seat='B', ref='augury-counter-subject')
        permanent.counters['+1/+1'] = 2
        engine.state.players['C'].poison = 1
        source = self.add(engine, 'Experimental Augury', zone='hand')
        offered = self.ready(session, source, {'U': 1, 'C': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual(2, permanent.counters['+1/+1'])
        accepted = session.act('pilot:A', {'action_id': 'choose', 'cards': {
            'hand': [cards[0].ref], 'bottom': [cards[1].ref, cards[2].ref]}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('semantic.choice', session.state.pending_decision.kind)
        session = self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'objects': [permanent.ref, 'C']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(3, session.state.cards[permanent.object_id].counters['+1/+1'])
        self.assertEqual(2, session.state.players['C'].poison)
        self.assertEqual('hand', session.state.cards[cards[0].object_id].zone)
        self.replay(session)
