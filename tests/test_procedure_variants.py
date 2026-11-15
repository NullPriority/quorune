"""Complete library alternatives retain public predicates and sealed cast choices."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import test_recipient_control as recipients
import test_opponent_greater_metrics as searches
import test_fixed_creature_power_damage as compiler_controls
import bargain_fixture as bargain_controls
from inherited_composition_fixture import InheritedNativeFixture
from quorune.compiler.procedure_variants import literal_procedure_variant
from quorune.declared_cost_conditions import DeclaredCastCostCondition
from quorune.oracle_ir import compile_oracle_card, _reviewed_effect_template
from quorune.rules.capabilities import load_default_capability_registry

PILGRIMAGE = 'Search your library for up to two basic Forest cards, reveal those cards, and put one onto the battlefield tapped and the rest into your hand. Then shuffle.\nSpell mastery — If there are two or more instant and/or sorcery cards in your graveyard, search your library for up to three basic Forest cards instead of two.'
FARSIGHT = 'Look at the top four cards of your library. If this spell was bargained, look at the top eight cards of your library instead. Put two of them into your hand and the rest on the bottom of your library in a random order.'
RECLAIM = 'Search your library for a basic land card, reveal it, put it into your hand, then shuffle. If this spell was kicked, search your library for two basic land cards instead of one.'


class ProcedureVariantCompilerTests(unittest.TestCase):
    def test_count_override_retains_allocation_reveal_shuffle_and_original_span(self):
        record = compiler_controls.card_record(PILGRIMAGE)
        ir = compile_oracle_card(record, capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes;self.assertEqual((0, len(PILGRIMAGE)), (node.span.start, node.span.end))
        wrapper, = node.effects
        self.assertEqual((3, 2), (wrapper['effects'][0]['count']['maximum'], wrapper['otherwise_effects'][0]['count']['maximum']))
        for effect in (*wrapper['effects'], *wrapper['otherwise_effects']):
            self.assertTrue(effect['shuffle_after']);self.assertTrue(effect['reveal']);self.assertEqual(1, effect['allocation']['first_count'])
        variant = literal_procedure_variant(RECLAIM)
        self.assertIn('reveal them', variant.alternative);self.assertIn('put them into your hand, then shuffle.', variant.alternative)
        self.assertIsNone(literal_procedure_variant(RECLAIM.replace('instead of one', 'instead of two')))

    def test_bargain_shared_tail_retains_selected_count_and_unknown_siblings_fail_closed(self):
        registry = load_default_capability_registry()
        record = replace(compiler_controls.card_record('Bargain\n' + FARSIGHT, type_line='Instant'), keywords=('Bargain',))
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability');wrapper, = node.effects
        self.assertEqual(12, wrapper['schema_version'])
        self.assertEqual((8, 4), (wrapper['effects'][0]['look_count'], wrapper['otherwise_effects'][0]['look_count']))
        self.assertTrue(all(e['minimum'] == e['maximum'] == 2 for e in (*wrapper['effects'], *wrapper['otherwise_effects'])))
        for text in (FARSIGHT + ' Invent an unknown result.', FARSIGHT.replace('random order', 'unrepresented order'),
                FARSIGHT.replace('this spell was bargained', 'an unrepresented event happened')):
            changed = replace(record, oracle_text='Bargain\n' + text)
            self.assertNotEqual('exact', compile_oracle_card(changed, capability_registry=registry).status, text)

    def test_any_card_surface_is_single_unqualified_search_not_any_number(self):
        result = _reviewed_effect_template('Search your library for any card, put it into your hand, then shuffle.', card_name='Witness')
        self.assertIsNotNone(result[0]);effect, = result[1]
        self.assertEqual({}, effect['selector']);self.assertEqual({'minimum': 1, 'maximum': 1}, effect['count']);self.assertFalse(effect['reveal'])
        self.assertIsNone(_reviewed_effect_template('Search your library for any number of cards, put them into your hand, then shuffle.', card_name='Witness')[0])


class ProcedureVariantNativeTests(InheritedNativeFixture, unittest.TestCase):
    setUpClass = classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene = recipients.RecipientControlNativeTests.scene
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish
    search = searches.OpponentGreaterMetricNativeTests.search
    cast_bargain = bargain_controls.BargainFixture.cast_bargain
    bargain_action = bargain_controls.BargainFixture.bargain_action

    def board(self, name, *, applies=True):
        extra = {}
        def before(s, source, gift):
            for key, record in (('forest1', 'Forest'), ('forest2', 'Forest'), ('forest3', 'Snow-Covered Forest'),
                    ('nonbasic', 'Temple Garden'), ('island', 'Island'), ('secret', 'Counterspell'), ('demon', 'Demon of Catastrophes')):
                extra[key] = self.add(s.engine, record, zone='library', ref='procedure-' + key)
            extra['price'] = next(c for c in s.state.cards.values() if c.printed_name == 'Fervor')
            extra['copy'] = self.add(s.engine, 'Reverberate', zone='hand', ref='procedure-copy')
            extra['bounce'] = self.add(s.engine, 'Boomerang', seat='B', zone='hand', ref='procedure-bounce')
            extra['cremate'] = self.add(s.engine, 'Cremate', seat='B', zone='hand', ref='procedure-cremate')
            extra['blink'] = self.add(s.engine, 'Cloudshift', zone='hand', ref='procedure-blink')
            extra['opponent'] = self.add(s.engine, 'Hill Giant', seat='B', ref='procedure-opponent')
            s.state.players['B'].mana_pool.update({'U': 10, 'B': 10})
            if name == "Nissa's Triumph":
                extra['nissa'] = self.add(s.engine, 'Nissa, Voice of Zendikar', seat='A' if applies else 'B')
                extra['nissa'].counters['loyalty'] = 3;extra['nissa'].annotations['loyalty_initialized'] = True
            if name == "Nissa's Pilgrimage":
                extra['grave'] = self.add(s.engine, 'Giant Growth', zone='graveyard', ref='procedure-grave-instant')
                if applies:self.add(s.engine, 'Cultivate', zone='graveyard', ref='procedure-grave-sorcery')
                self.add(s.engine, 'Counterspell', seat='B', zone='graveyard', ref='procedure-other-grave-one')
                self.add(s.engine, 'Cultivate', seat='B', zone='graveyard', ref='procedure-other-grave-two')
            if name == 'Demonic Counsel':
                self.add(s.engine, 'Universal Automaton', zone='graveyard', ref='delirium-two-types')
                self.add(s.engine, 'Island', zone='graveyard', ref='delirium-land')
                if applies:self.add(s.engine, 'Cultivate', zone='graveyard', ref='delirium-sorcery')
                self.add(s.engine, 'Cultivate', seat='B', zone='graveyard', ref='delirium-other-player')
        s, source, gift, private = self.scene(name, before=before)
        return s, source, private, extra

    def submit_search(self, s, refs):
        command = {'action_id': 'choose', 'search_cards': list(refs)};self.reject(s, 'B', command)
        s = self.replay(s, load=True);before = s.state.players['A'].stats.get('shuffle_count', 0)
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(before + 1, s.state.players['A'].stats.get('shuffle_count', 0));return s

    def test_original_pilgrimage_and_triumph_preserve_count_query_and_placement_variants(self):
        for name in ("Nissa's Pilgrimage", "Nissa's Triumph"):
            for applies in (False, True):
                with self.subTest(card=name, applies=applies):
                    s, source, private, extra = self.board(name, applies=applies)
                    self.cast(s, 'A', source);d = self.search(s)
                    action = next(a for a in d['legal_actions'] if a['id'] == 'choose')
                    self.assertEqual(3 if applies else 2, action['choice_schema']['maximum'])
                    refs = [extra[k].ref for k in ('forest1', 'forest2', 'forest3')[:3 if applies else 2]]
                    if name == "Nissa's Triumph" and applies:refs[0] = extra['nonbasic'].ref
                    else:self.reject(s, 'A', {'action_id': 'choose', 'search_cards': [extra['nonbasic'].ref]})
                    s = self.submit_search(s, refs)
                    cards = [next(c for c in s.state.cards.values() if c.ref == ref) for ref in refs]
                    self.assertEqual(['battlefield', *(['hand'] * (len(refs) - 1))] if name == "Nissa's Pilgrimage" else ['hand'] * len(refs), [c.zone for c in cards])
                    if name == "Nissa's Pilgrimage":self.assertTrue(cards[0].tapped)
                    self.assertIn(cards[-1].ref, json.dumps(s.packet('pilot:B', full=True)));self.finish(s, private)

    def test_responses_remove_nissa_or_spell_mastery_before_the_current_branch(self):
        for name in ("Nissa's Pilgrimage", "Nissa's Triumph"):
            s, source, private, extra = self.board(name)
            self.cast(s, 'A', source)
            self.cast(s, 'B', extra['cremate'] if name == "Nissa's Pilgrimage" else extra['bounce'], [extra['grave'].ref if name == "Nissa's Pilgrimage" else extra['nissa'].ref])
            self.resolve_top(s);d = self.search(s)
            action = next(a for a in d['legal_actions'] if a['id'] == 'choose');self.assertEqual(2, action['choice_schema']['maximum'])
            s = self.submit_search(s, [extra['forest1'].ref, extra['forest2'].ref]);self.finish(s, private)

    def test_original_reclaim_kicker_changes_the_complete_revealed_search_once(self):
        for kicked in (False, True):
            s, source, private, extra = self.board('Reclaim the Wastes')
            self.priority(s, 'A');row = self.action(s, 'A', source)
            command = {'action_id': row['id'], 'pay': 'auto', **({'cost_option': 'kicked'} if kicked else {})};self.reject(s, 'B', command)
            result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary);d = self.search(s)
            count = 2 if kicked else 1;self.assertEqual(count, next(a for a in d['legal_actions'] if a['id'] == 'choose')['choice_schema']['maximum'])
            refs = [extra['forest1'].ref, extra['island'].ref][:count];s = self.submit_search(s, refs)
            self.assertTrue(all(next(c for c in s.state.cards.values() if c.ref == ref).zone == 'hand' for ref in refs));self.finish(s, private)

    def farsight(self, chosen):
        s, source, private, extra = self.board('Farsight Ritual')
        self.cast_bargain(s, source, extra['price'], chosen=chosen)
        d = self.resolve(s);self.assertIsNotNone(d)
        packet = s.packet('pilot:A', full=True)['decision'];refs = [r['id'] for r in packet['ctx']['objects']]
        self.assertEqual(8 if chosen else 4, len(refs), 'bargain-procedure-look-count')
        action = next(a for a in packet['legal_actions'] if a['id'] == 'choose')
        self.assertEqual((2, 2), (action['choice_schema']['minimum'], action['choice_schema']['maximum']))
        for seat in 'BCD':
            for ref in refs:self.assertNotIn(ref, json.dumps(s.packet('pilot:' + seat, full=True)))
        command = {'action_id': 'choose', 'cards': refs[:2]};self.reject(s, 'B', command)
        self.reject(s, 'A', {**command, 'cards': refs[:3]});s = self.replay(s, load=True)
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard' if chosen else 'battlefield', s.state.cards[extra['price'].object_id].zone)
        for ref in refs[:2]:
            self.assertEqual('hand', next(c for c in s.state.cards.values() if c.ref == ref).zone)
            self.assertNotIn(ref, json.dumps(s.packet('pilot:B', full=True)))
        self.assertEqual(0, s.state.players['A'].stats.get('shuffle_count', 0));self.finish(s, private)

    def test_original_farsight_bargain_changes_only_private_look_count(self):
        for chosen in (False, True):
            with self.subTest(chosen=chosen):self.farsight(chosen)

    def test_original_demonic_counsel_counts_types_and_keeps_upgraded_hand_private(self):
        for applies in (False, True):
            s, source, private, extra = self.board('Demonic Counsel', applies=applies)
            self.cast(s, 'A', source);self.search(s)
            selected = extra['secret'] if applies else extra['demon']
            if applies:self.reject(s, 'A', {'action_id': 'choose', 'search_cards': []})
            else:self.reject(s, 'A', {'action_id': 'choose', 'search_cards': [extra['secret'].ref]})
            s = self.submit_search(s, [selected.ref])
            self.assertEqual('hand', s.state.cards[selected.object_id].zone)
            if applies:self.assertNotIn(selected.ref, json.dumps(s.packet('pilot:B', full=True)))
            else:self.assertIn(selected.ref, json.dumps(s.packet('pilot:B', full=True)))
            self.finish(s, private)

    def test_original_farsight_copy_retains_bargain_without_repaying_sacrifice(self):
        s, source, private, extra = self.board('Farsight Ritual')
        self.cast_bargain(s, source, extra['price'], chosen=True)
        original = next(item for item in s.state.stack if item.card_object_id == source.object_id)
        self.cast(s, 'A', extra['copy'], [original.ref])
        for _ in range(2):
            self.assertIsNotNone(self.resolve(s));packet = s.packet('pilot:A', full=True)['decision']
            refs = [r['id'] for r in packet['ctx']['objects']];self.assertEqual(8, len(refs))
            s = self.replay(s, load=True);command = {'action_id': 'choose', 'cards': refs[:2]};self.reject(s, 'B', command)
            result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard', s.state.cards[extra['price'].object_id].zone)
        self.assertEqual(0, s.state.players['A'].stats.get('shuffle_count', 0));self.finish(s, private)

    def test_original_agatha_bargain_entry_keeps_optional_target_and_source_incarnation(self):
        for chosen, target, blink in ((False, False, False), (True, False, False), (True, True, False), (True, True, True)):
            with self.subTest(chosen=chosen, target=target, blink=blink):
                s, source, private, extra = self.board("Agatha's Champion")
                self.cast_bargain(s, source, extra['price'], chosen=chosen)
                decision = self.resolve(s)
                if chosen:
                    self.assertEqual('semantic.target', decision.kind);self.target(s, 'A', [extra['opponent'].ref] if target else [])
                    if blink:
                        self.cast(s, 'A', extra['blink'], [source.ref]);self.resolve_top(s)
                    self.assertIsNone(self.resolve(s))
                else:self.assertIsNone(decision)
                self.assertEqual('graveyard' if chosen and target and not blink else 'battlefield', s.state.cards[extra['opponent'].object_id].zone)
                self.assertEqual(3 if chosen and target and not blink else 0, s.state.cards[source.object_id].marked_damage)
                self.finish(s, private)

    def test_procedure_owner_minima_remain_required_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name, missing in (('Farsight Ritual', 'library.select.fixed_controller'),
                ('Farsight Ritual', 'casting.additional_cost.declared_instance'),
                ("Nissa's Pilgrimage", 'library.search.ordered_destination_allocation'),
                ("Nissa's Pilgrimage", 'resolution.effect.fixed_public_self_replacement')):
            s, source, private, extra = self.board(name)
            p = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if missing in p.capability_dependencies and p.event == 'resolve')
            positive = bind_semantic_program_runtime(replace(p, coverage=()), capability_registry=self.registry, profile='commander_review')
            self.assertTrue(positive['strict'], positive)
            deps = tuple(c for c in p.capability_dependencies if c != missing)
            changed = replace(p, coverage=(), capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:' + missing, result['blockers'])

    def test_fresh_farsight_witness_kills_ignoring_the_sealed_bargain_choice(self):
        self.farsight(True)
        with patch.object(DeclaredCastCostCondition, 'matches', return_value=False):
            with self.assertRaisesRegex(AssertionError, 'bargain-procedure-look-count'):self.farsight(True)
