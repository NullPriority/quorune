from __future__ import annotations

import unittest

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


def manifest_record(text: str) -> CardRecord:
    return CardRecord(oracle_id='00000000-0000-4000-8000-000000311100', name='Generic Manifest Instruction',
        mana_cost='{G}', mana_value=1, type_line='Sorcery', oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=('G',), color_identity=('G',),
        keywords=(), produced_mana=(), layout='normal', released_at='2026-01-01',
        legalities={'commander': 'legal'}, faces=(), raw={})


class ManifestCompilerTests(unittest.TestCase):
    def test_normal_and_dread_manifest_are_distinct_typed_instructions(self):
        registry = load_default_capability_registry()
        for text, mode in (('Manifest the top card of your library.', 'top'), ('Manifest dread.', 'dread')):
            with self.subTest(mode=mode):
                ir = compile_oracle_card(manifest_record(text), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                effect = ir.faces[0].nodes[0].effects[0]
                self.assertEqual('manifest_card', effect['op'])
                self.assertEqual(mode, effect['mode'])
                self.assertEqual(text, ir.faces[0].nodes[0].text)


class ManifestChoiceModelTests(unittest.TestCase):
    def test_manifest_empty_and_single_card_dread_keep_exact_cardinality(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.manifest import ManifestChoiceHandler
        handler = ManifestChoiceHandler()
        query = SimpleNamespace(library_refs=lambda *_a, **_kw: (), object=lambda *_a, **_kw: None)
        context = SimpleNamespace(query=query, actor='A', stack_ref='S1', stack_label='Manifest')
        empty = handler.prepare({'op': 'manifest_card', 'player': 'A', 'mode': 'dread'}, context)
        self.assertIsNone(empty.request)
        self.assertEqual((), empty.preparation_intents)
        row = SimpleNamespace(ref='only', printed_name='Private', logical_object_id='only@0')
        query.library_refs = lambda *_a, **_kw: ('only',)
        query.object = lambda *_a, **_kw: row
        selected = handler.prepare({'op': 'manifest_card', 'player': 'A', 'mode': 'dread'}, context)
        completion = handler.complete(SimpleNamespace(effect=selected.continuation_effect, note='Manifest'), {'card': 'only'}, query)
        self.assertEqual(1, len(completion.intents))
        self.assertTrue(completion.intents[0].manifest_entry)

    def test_manifest_producer_withdrawal_and_malformed_fields_fail_closed(self):
        from unittest.mock import patch
        from quorune.rules.manifest_shapes import manifest_node_capabilities
        effects = ({'op': 'manifest_card', 'player': '$controller', 'mode': 'dread'},)
        self.assertIn('keyword_action.manifest_dread', manifest_node_capabilities(
            effects=effects, target_schema=None, mechanics={'manifest', 'manifest dread'}))
        for changed in ({**effects[0], 'mode': 'unknown'}, {**effects[0], 'player': '$opponent'},
            {**effects[0], 'unknown': True}):
            self.assertEqual((), manifest_node_capabilities(effects=(changed,), target_schema=None, mechanics={'manifest'}))
        with patch('quorune.compiler.manifest_templates.manifest_effect_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(manifest_record('Manifest dread.'),
                capability_registry=load_default_capability_registry()).status)
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'keyword_action.manifest.card').update(
            status='blocked', blockers=['Manifest authority withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(manifest_record('Manifest dread.'),
            capability_registry=CapabilityRegistry(raw)).status)

    def test_dread_choice_pins_private_top_cards_and_orders_entry_before_remainder(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.manifest import ManifestChoiceHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        rows = {ref: SimpleNamespace(ref=ref, printed_name='Private ' + ref, logical_object_id=ref + '@0')
            for ref in ('first', 'second')}
        query = SimpleNamespace(library_refs=lambda *_a, **_kw: ('first', 'second'),
            object=lambda ref, **_kw: rows[ref])
        context = SimpleNamespace(query=query, actor='A', stack_ref='S1', stack_label='Manifest dread')
        handler = ManifestChoiceHandler()
        preparation = handler.prepare({'op': 'manifest_card', 'player': 'A', 'mode': 'dread'}, context)
        self.assertEqual('actor_private', preparation.request.choice.visibility)
        continuation = SimpleNamespace(effect=preparation.continuation_effect, note='Manifest dread')
        completed = handler.complete(continuation, {'card': 'second'}, query)
        self.assertTrue(completed.intents[0].manifest_entry)
        self.assertEqual('second', completed.intents[0].object_ref)
        self.assertEqual('graveyard', completed.intents[1].destination)
        self.assertEqual('first', completed.intents[1].object_ref)
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {'card': 'foreign'}, query)
        rows['second'].logical_object_id = 'second@1'
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {'card': 'second'}, query)

    def test_manifest_intent_codec_preserves_legacy_and_rejects_unbound_entry(self):
        from quorune.semantic_runtime.zone_move_intent import ZoneMoveIntent
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity
        legacy = ZoneMoveIntent(actor='A', object_ref='body', expected_zones=('graveyard',), destination='hand', reason='return')
        kind, identity = semantic_intent_identity(legacy)
        self.assertNotIn('manifest_entry', identity)
        self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))
        manifested = ZoneMoveIntent(actor='A', object_ref='body', expected_zones=('library',), destination='battlefield',
            new_controller='A', reason='manifest', expected_zone_change_counter=0, manifest_entry=True)
        kind, identity = semantic_intent_identity(manifested)
        self.assertTrue(validate_semantic_intent_identity(kind, identity)['manifest_entry'])
        with self.assertRaises(ValueError):
            ZoneMoveIntent(actor='A', object_ref='body', expected_zones=('hand',), destination='battlefield',
                new_controller='A', reason='manifest', expected_zone_change_counter=0, manifest_entry=True)


class ManifestEntryOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'manifest.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/manifest-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Manifest', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Witness
    session = _Witness.session
    add = _Witness.add
    register_fixture_cards_on_add = True
    ready = _Witness.ready
    checkpoint = _Witness.checkpoint
    replay = _Witness.replay
    resolve = _Witness.resolve

    def test_original_soul_summons_manifests_hidden_card_then_turns_up_without_entry_trigger(self):
        from quorune.record import authoritative_state_hash
        session = self.session(311102)
        engine = session.engine
        spell = self.add(engine, 'Soul Summons', zone='hand')
        card = self.add(engine, 'Viashino Firstblade', zone='library', ref='manifest-native-top')
        library = engine.state.players['A'].zones['library']
        library.remove(card.object_id)
        library.append(card.object_id)
        offered = self.ready(session, spell, {'W': 3, 'R': 1, 'C': 4})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertTrue(engine.state.cards[card.object_id].face_down)
        self.assertEqual(2, engine._numeric_stat(card.object_id, 'power'))
        self.assertFalse(any(item.source_object_id == card.object_id for item in engine.state.stack))
        for seat in 'BCD':
            self.assertNotIn('Viashino Firstblade', str(session.packet('pilot:' + seat, full=True)))
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:B', {'action_id': 'turn-face-up:' + card.ref + ':manifest'})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'] == 'turn-face-up:' + card.ref + ':manifest')
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.cards[card.object_id].face_down)
        self.assertEqual(2, session.engine._numeric_stat(card.object_id, 'power'))
        self.assertEqual([], session.state.stack)
        self.replay(session)

    def test_original_manifest_dread_privately_selects_then_moves_remainder_and_replays(self):
        from quorune.record import authoritative_state_hash
        session = self.session(311103)
        engine = session.engine
        spell = self.add(engine, 'Manifest Dread', zone='hand')
        first = self.add(engine, 'Lightning Bolt', zone='library', ref='dread-first')
        second = self.add(engine, 'Viashino Firstblade', zone='library', ref='dread-second')
        library = engine.state.players['A'].zones['library']
        for card in (first, second):
            library.remove(card.object_id)
        library.extend([second.object_id, first.object_id])
        offered = self.ready(session, spell, {'G': 1, 'C': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        for seat in 'BCD':
            projected = str(session.packet('pilot:' + seat, full=True))
            for card in (first, second):
                self.assertNotIn(card.ref, projected)
        session = self.replay(session, load=True)
        for principal, selected in (('pilot:B', first.ref), ('pilot:A', 'foreign')):
            before = authoritative_state_hash(session.state)
            denied = session.act(principal, {'action_id': 'choose', 'card': selected})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': 'choose', 'card': second.ref})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[first.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[second.object_id].zone)
        self.assertTrue(session.state.cards[second.object_id].face_down)
        self.replay(session)

    def test_original_manifested_megamorph_offers_both_costs_and_counter_only_for_megamorph(self):
        for method in ('manifest', 'megamorph'):
            with self.subTest(method=method):
                session = self.session(311104 + int(method == 'megamorph'))
                engine = session.engine
                spell = self.add(engine, 'Soul Summons', zone='hand')
                card = self.add(engine, 'Aerie Bowmasters', zone='library', ref='manifest-megamorph')
                library = engine.state.players['A'].zones['library']
                library.remove(card.object_id)
                library.append(card.object_id)
                offered = self.ready(session, spell, {'W': 1, 'G': 2, 'C': 8})
                self.checkpoint(session)
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                session = self.replay(session, load=True)
                actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                methods = {a.get('method') for a in actions if a.get('action') == 'turn_face_up'}
                self.assertEqual({'manifest', 'megamorph'}, methods)
                offered = next(a for a in actions if a.get('method') == method)
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertFalse(session.state.cards[card.object_id].face_down)
                self.assertEqual(int(method == 'megamorph'), session.state.cards[card.object_id].counters.get('+1/+1', 0))
                self.replay(session)

    def test_original_sultai_emissary_dies_then_manifests_after_sacrifice_cost(self):
        session = self.session(311106)
        engine = session.engine
        source = self.add(engine, 'Sultai Emissary')
        feeder = self.add(engine, 'Carrion Feeder')
        top = self.add(engine, 'Lightning Bolt', zone='library', ref='manifest-death-top')
        library = engine.state.players['A'].zones['library']
        library.remove(top.object_id)
        library.append(top.object_id)
        offered = self.ready(session, feeder, {})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'cost_cards': [source.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(1, session.state.cards[feeder.object_id].counters.get('+1/+1', 0))
        self.assertTrue(session.state.cards[top.object_id].face_down)
        self.assertEqual('battlefield', session.state.cards[top.object_id].zone)
        self.replay(session)

    def test_manifested_instant_has_no_turn_up_offer_or_accepted_action(self):
        from quorune.record import authoritative_state_hash
        session = self.session(311107)
        engine = session.engine
        spell = self.add(engine, 'Soul Summons', zone='hand')
        card = self.add(engine, 'Lightning Bolt', zone='library', ref='manifest-instant')
        library = engine.state.players['A'].zones['library']
        library.remove(card.object_id)
        library.append(card.object_id)
        offered = self.ready(session, spell, {'W': 1, 'C': 2})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('card') == card.ref and a.get('action') == 'turn_face_up' for a in actions))
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {'action': 'turn_face_up', 'card': card.ref, 'method': 'manifest', 'pay': 'auto'})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session)

    def test_typed_entry_manifests_noncreature_without_printed_characteristics(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        for name in ('Lightning Bolt', 'Viashino Firstblade'):
            with self.subTest(name=name):
                session = self.session(311100)
                engine = session.engine
                card = self.add(engine, name, zone='library', ref='manifest-owner-card')
                ZoneTransitionOwner(engine).move_card(card.object_id, 'battlefield', controller='A',
                    manifest_entry=True, semantic_events=True, reason='Manifest owner diagnostic')
                self.assertTrue(card.face_down)
                self.assertEqual('battlefield', card.zone)
                self.assertEqual((2, 2), (engine._numeric_stat(card.object_id, 'power'), engine._numeric_stat(card.object_id, 'toughness')))
                self.assertEqual([], engine._effective_card_data(card)['keywords'])
                self.assertEqual([], engine.state.stack)
                for seat in 'BCD':
                    projected = str(session.packet('pilot:' + seat, full=True))
                    self.assertNotIn(name, projected)

    def test_manifested_creature_has_controller_mana_turn_up_action_and_noncreature_does_not(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.rules.morph_actions import build_turn_face_up_offer
        session = self.session(311101)
        engine = session.engine
        creature = self.add(engine, 'Viashino Firstblade', zone='library', ref='manifest-turn-up')
        instant = self.add(engine, 'Lightning Bolt', zone='library', ref='manifest-noncreature')
        for card in (creature, instant):
            ZoneTransitionOwner(engine).move_card(card.object_id, 'battlefield', controller='A',
                manifest_entry=True, semantic_events=True, reason='Manifest turn-up owner diagnostic')
        engine.state.players['A'].mana_pool.update({'W': 1, 'R': 1, 'C': 1})
        self.assertIsNotNone(build_turn_face_up_offer(engine, 'A', creature))
        self.assertIsNone(build_turn_face_up_offer(engine, 'B', creature))
        self.assertIsNone(build_turn_face_up_offer(engine, 'A', instant))


if __name__ == '__main__':
    unittest.main()
