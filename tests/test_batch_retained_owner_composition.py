from __future__ import annotations

import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_public_query_effect_amounts import query_amount_record


class RetainedOwnerCompositionCompilerTests(unittest.TestCase):
    def test_mana_source_exclusion_and_optional_sacrifice_source_counter_keep_closed_owners(self):
        cases = (
            ('{2}, {T}: Add X mana of any one color, where X is the number of other Gates you control.', 'Land — Gate'),
            ('Whenever a player sacrifices a creature, you may put a +1/+1 counter on this creature.', 'Creature — Insect'),
        )
        for i, (text, kind) in enumerate(cases):
            with self.subTest(text=text):
                ir = compile_oracle_card(query_amount_record(text, suffix=365208001+i, type_line=kind),
                    capability_registry=load_default_capability_registry())
                self.assertEqual('exact', ir.status, ir.material_residuals)


class RetainedOwnerCompositionActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'retained-composition.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/retained-owner-composition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Retained', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    replay = _bound.BoundEffectProgramRuntimeTests.replay
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    register_fixture_cards_on_add = True

    def test_original_baldurs_gate_excludes_itself_from_offered_and_paid_mana(self):
        session = self.session(365208010)
        engine = session.engine
        source = self.add(engine, "Baldur's Gate")
        self.add(engine, 'Golgari Guildgate')
        self.add(engine, 'Golgari Guildgate', ref='second-other-gate')
        self.add(engine, 'Golgari Guildgate', seat='B', ref='enemy-gate')
        self.ready(session, source, {'C': 2})
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'].startswith('activate:' + source.ref + ':ab2'))
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto', 'mana_choice': 'G'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['G'])
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual([], session.state.stack)
        self.replay(session, load=True)

    def test_original_mortician_beetle_optional_counter_uses_sacrifice_occurrence(self):
        from quorune.record import authoritative_state_hash
        for apply in (True, False):
            session = self.session(365208020 + apply)
            engine = session.engine
            beetle = self.add(engine, 'Mortician Beetle')
            feeder = self.add(engine, 'Carrion Feeder')
            victim = self.add(engine, 'Generic Bound Body')
            offered = self.ready(session, feeder, {})
            self.checkpoint(session)
            accepted = session.act('pilot:A', {'action_id': offered['id'], 'cost_cards': [victim.ref]})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            session = self.replay(session, load=True)
            before = authoritative_state_hash(session.state)
            denied = session.act('pilot:B', {'action_id': 'choose', 'choice': 'put'})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            chosen = session.act('pilot:A', {'action_id': 'choose', 'choice': 'put' if apply else 'decline'})
            self.assertTrue(chosen.ok, chosen.summary)
            self.resolve(session)
            self.assertEqual(int(apply), session.state.cards[beetle.object_id].counters.get('+1/+1', 0))
            self.assertEqual(1, session.state.cards[feeder.object_id].counters.get('+1/+1', 0))
            self.replay(session)
