"""Retain exact source counts while preserving static/live quantity boundaries."""
from dataclasses import replace
import json
import unittest
import qualified_quantity_retention_fixture as fixtures
from quorune.compiler.query_characteristic_templates import query_characteristic_quantity, _canonical_tap_quantity_query
from quorune.compiler.public_quantity_mana import public_quantity_mana_template
from quorune.object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from quorune.record import authoritative_state_hash


class QualifiedQuantityRetentionCompilerTests(unittest.TestCase):
    def test_pure_tap_count_queries_keep_legacy_encoding_without_dropping_other_state(self):
        for tapped, word in ((True, 'tapped'), (False, 'untapped')):
            quantity = query_characteristic_quantity(word + ' creatures you control', source_name='Quantity Source', definition_extensions=True)
            self.assertEqual(1, quantity.schema_version);self.assertIs(tapped, quantity.query.tapped)
            self.assertIsNone(quantity.query.state_predicate);PublicQueryAmountSpec(quantity)
            query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), state_predicate=PermanentStatePredicateSpec(tapped=tapped))
            self.assertEqual(replace(query, tapped=tapped, state_predicate=None), _canonical_tap_quantity_query(query))
            for state in (PermanentStatePredicateSpec(attacking=True), PermanentStatePredicateSpec(counter_name='+1/+1', minimum_counter_count=1), PermanentStatePredicateSpec(entered_this_turn=True)):
                other = replace(query, state_predicate=state)
                self.assertEqual(other, _canonical_tap_quantity_query(other))

    def test_mana_uses_existing_live_keyword_fallback_and_keeps_static_boundary_closed(self):
        text = 'Add {G} for each creature you control with defender.'
        template = public_quantity_mana_template(text, source_name='Quantity Source')
        self.assertIsNotNone(template);self.assertIsInstance(template.amount, LiveBattlefieldQuantitySpec)
        self.assertEqual(('defender',), template.amount.query.keywords_all)
        static = query_characteristic_quantity('creatures you control with defender', source_name='Quantity Source', definition_extensions=True)
        with self.assertRaises(ValueError):PublicQueryAmountSpec(static)
        self.assertIsNone(public_quantity_mana_template(text.replace('defender', 'unknown ability'), source_name='Quantity Source'))
        counter = public_quantity_mana_template('Add {G} for each charge counter on this artifact.', source_name='Quantity Source')
        self.assertIsNotNone(counter);self.assertEqual('charge', counter.amount.counter_name)


class QualifiedQuantityRetentionNativeTests(fixtures.QualifiedQuantityRetentionFixture, unittest.TestCase):
    def test_original_guardian_and_battlement_count_current_controlled_defenders_and_pay_real_spell(self):
        s = self.session(672100)
        guardian = self.add(s.engine, 'Axebane Guardian');battlement = self.add(s.engine, 'Overgrown Battlement')
        wall = self.add(s.engine, 'Wall of Stone');self.add(s.engine, 'Wall of Stone', seat='B', ref='foreign-defender')
        frog = self.add(s.engine, 'Turn to Frog', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B');self.add(s.engine, 'Wastes', seat='B')
        draw = self.add(s.engine, 'Divination', zone='hand', ref='defender-funded-draw')
        guardian.acquired_control_turn_count = -1;battlement.acquired_control_turn_count = -1
        self.assert_closed(s, guardian);self.assert_closed(s, battlement);self.ready(s, {})
        row = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + guardian.ref + ':'))
        command = {'action_id': row['id'], 'mana_output': {'G': 2, 'U': 1}}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        self.assertEqual((2, 1), (s.state.players['A'].mana_pool['G'], s.state.players['A'].mana_pool['U']))
        self.assertFalse(s.state.stack)
        self.cast(s, 'A', draw);self.settle(s)
        self.assertEqual((0, 0), (s.state.players['A'].mana_pool.get('G', 0), s.state.players['A'].mana_pool.get('U', 0)))
        self.cast(s, 'B', frog, [wall.ref]);self.settle(s)
        self.assertNotIn('defender', s.engine._combat_keywords(s.state.cards[wall.object_id]))
        self.activate_counter_source(s, battlement)
        self.assertEqual(2, s.state.players['A'].mana_pool['G']);self.assertFalse(s.state.stack)
        self.private(s);self.replay(s, load=True)

    def test_original_crash_the_party_counts_before_creating_tapped_tokens(self):
        s = self.session(672110);spell = self.add(s.engine, 'Crash the Party', zone='hand')
        for index in range(2):self.add(s.engine, 'Grizzly Bears', ref='party-counted-' + str(index)).tapped = True
        self.add(s.engine, 'Grizzly Bears', ref='party-untapped')
        self.add(s.engine, 'Grizzly Bears', seat='B', ref='party-foreign').tapped = True
        self.add(s.engine, 'Sol Ring').tapped = True
        self.assert_closed(s, spell);self.ready(s, {'C': 5, 'G': 1})
        self.cast(s, 'A', spell);self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.controller == 'A']
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertTrue(token.tapped);self.assertEqual((4, 4), (s.engine._numeric_stat(token.object_id, 'power'), s.engine._numeric_stat(token.object_id, 'toughness')))
            types, subtypes, _ = s.engine._type_parts(s.engine._effective_card_data(token)['type_line'])
            self.assertIn('creature', types);self.assertTrue({'rhino', 'warrior'} <= set(subtypes))
        self.private(s);self.replay(s, load=True)

    def test_original_knotvine_counts_untapped_friends_after_attack_taps_source_and_expires(self):
        s = self.session(672120);source = self.add(s.engine, 'Knotvine Paladin');source.acquired_control_turn_count = -1
        self.add(s.engine, 'Grizzly Bears', ref='knotvine-friend');self.add(s.engine, 'Grizzly Bears', seat='B', ref='knotvine-foreign')
        self.assert_closed(s, source);self.ready(s, {})
        self.attack(s, [(source, 'B')]);self.assertTrue(s.state.cards[source.object_id].tapped)
        self.settle(s)
        self.assertEqual((3, 3), (s.engine._numeric_stat(source.object_id, 'power'), s.engine._numeric_stat(source.object_id, 'toughness')))
        self.finish_turn(s)
        self.assertEqual((2, 2), (s.engine._numeric_stat(source.object_id, 'power'), s.engine._numeric_stat(source.object_id, 'toughness')))
        self.private(s);self.replay(s, load=True)

    def test_original_lydia_surveils_only_controlled_tapped_assassins_with_private_choice(self):
        s = self.session(672130);source = self.add(s.engine, 'Lydia Frye');source.tapped = True
        self.add(s.engine, 'Royal Assassin', ref='lydia-tapped-assassin').tapped = True
        self.add(s.engine, 'Royal Assassin', ref='lydia-untapped-assassin')
        self.add(s.engine, 'Royal Assassin', seat='B', ref='lydia-foreign-assassin').tapped = True
        self.add(s.engine, 'Grizzly Bears', ref='lydia-non-assassin').tapped = True
        grave = self.add(s.engine, 'Forest', zone='library', ref='lydia-private-grave-choice')
        top = self.add(s.engine, 'Wastes', zone='library', ref='lydia-private-top-choice')
        self.assert_closed(s, source);self.ready(s, {})
        self.step(s, 'A', 'ending', 'end_step')
        decision = self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.choice', s.state.pending_decision.kind)
        for seat in 'BCD':
            packet = json.dumps(s.packet('pilot:' + seat, full=True));self.assertNotIn(grave.ref, packet);self.assertNotIn(top.ref, packet)
        s = self.replay(s, load=True)
        command = {'action_id': 'choose', 'cards': {'top': [top.ref], 'graveyard': [grave.ref]}}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[grave.object_id].zone)
        self.assertEqual(top.object_id, s.state.players['A'].zones['library'][-1])
        for seat in 'BCD':self.assertNotIn(top.ref, json.dumps(s.packet('pilot:' + seat, full=True)))
        self.private(s);self.replay(s, load=True)

    def test_original_throne_counts_at_resolution_and_each_opponent_after_untap_response(self):
        for index, respond in enumerate((False, True)):
            with self.subTest(response=respond):
                s = self.session(672140 + index);source = self.add(s.engine, 'Throne of the God-Pharaoh')
                for number in range(2):self.add(s.engine, 'Grizzly Bears', ref='throne-tapped-' + str(number)).tapped = True
                self.add(s.engine, 'Grizzly Bears', seat='B', ref='throne-foreign').tapped = True
                if respond:
                    bell = self.add(s.engine, 'Village Bell-Ringer', zone='hand');ring = self.add(s.engine, 'Sol Ring');plains = self.add(s.engine, 'Plains')
                self.assert_closed(s, source);self.ready(s, {});life = {seat:s.state.players[seat].life for seat in 'ABCD'}
                self.step(s, 'A', 'ending', 'end_step')
                if respond:
                    self.tap_mana(s, ring);self.tap_mana(s, plains);self.cast(s, 'A', bell);self.resolve_top(s);self.resolve_top(s)
                    s = self.replay(s, load=True)
                self.settle(s)
                self.assertEqual(life['A'], s.state.players['A'].life)
                for seat in 'BCD':self.assertEqual(life[seat] - (0 if respond else 2), s.state.players[seat].life)
                self.private(s)
                if respond:self.replay(s, load=True)
