from __future__ import annotations

"""CR 702.16a-g: each printed fixed protection quality remains independent."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import parse_protection_line, protection_specs
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
from quorune.rules.evidence_storage import read_evidence_document


NAMES = ('Oversoul of Dusk', 'Elite Inquisitor')


class PrintedProtectionListTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ProvokeTests.session
    add = controls.ProvokeTests.add
    prepare = controls.ProvokeTests.prepare
    checkpoint = controls.ProvokeTests.checkpoint
    resolve = controls.ProvokeTests.resolve
    replay = controls.ProvokeTests.replay
    binding = controls.ProvokeTests.binding
    private_replay = controls.ProvokeTests.private_replay
    priority = controls.ProvokeTests.priority
    advance = controls.ProvokeTests.advance
    attack = controls.ProvokeTests.attack
    blockers = controls.ProvokeTests.blockers
    block = controls.ProvokeTests.block

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'printed-protection-lists.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/protection-list-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Printed protection lists', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def qualities(self, session, card):
        return tuple(s.quality if s.quality is not None else s.source_predicate.subtypes_all[0] for s in protection_specs(
            session.engine._effective_ability_fragments(session.state.cards[card.object_id])))

    def legal(self, session, seat):
        return session.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']

    def cast(self, session, seat, card, payment, targets=()):
        self.priority(session, seat)
        rows = [r for r in self.legal(session, seat) if r['id'] == 'cast:'+card.ref]
        self.assertEqual(1, len(rows), card.printed_name)
        command = {'action_id': rows[0]['id'], 'pay': 'manual', 'payment': payment, 'targets': list(targets)}
        if len(rows[0].get('cost_options', ())) > 1:
            options = [o for o in rows[0]['cost_options'] if all(
                n <= payment.get(c, 0) for c, n in o['requirements'].items() if c != 'GENERIC')]
            self.assertEqual(1, len(options), options)
            command['cost_option'] = options[0]['id']
        result = session.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_complete_originals_bind_every_quality_and_preserve_sibling_keywords(self):
        for i, (name, qualities, keywords) in enumerate((
                (NAMES[0], ('U', 'B', 'R'), ()),
                (NAMES[1], ('vampire', 'werewolf', 'zombie'), ('first strike', 'vigilance')))):
            session = self.session(754010+i)
            source = self.add(session.engine, name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])
            self.assertCountEqual(qualities, self.qualities(session, source))
            self.assertTrue(set(keywords) <= session.engine._combat_keywords(source))
            for face in ir.faces:
                for node in face.nodes:
                    self.assertEqual(node.text, self.db.lookup(name).oracle_text[node.span.start:node.span.end])

    def test_closed_list_grammar_retains_each_quality_and_rejects_ambiguous_continuations(self):
        specs = parse_protection_line('Protection from blue, from black, and from red')
        self.assertIsNotNone(specs)
        self.assertEqual(('U', 'B', 'R'), tuple(s.quality for s in specs))
        self.assertEqual(('W', 'B'), tuple(s.quality for s in parse_protection_line('Protection from white and from black.')))
        for text in ('Protection from blue, black, and red', 'Protection from blue, from made-up, and from red',
                'Protection from blue, from black, and red', 'Protection from blue,, from red',
                'Protection from blue, from black,', 'Protection from blue, from black or red'):
            self.assertIsNone(parse_protection_line(text), text)

    def test_actual_oversoul_cast_rejects_all_matching_targets_and_allows_white_draw(self):
        session = self.session(754020)
        source = self.add(session.engine, NAMES[0], zone='hand')
        spells = [self.add(session.engine, name, seat='B', zone='hand') for name in ('Boomerang', 'Murder', 'Lightning Bolt')]
        white = self.add(session.engine, 'Defiant Strike', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'G': 5, 'W': 1})
        session.state.players['B'].mana_pool.update({'U': 2, 'B': 2, 'R': 1, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'G': 5})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertCountEqual(('U', 'B', 'R'), self.qualities(session, source))
        library = len(session.state.players['A'].zones['library'])
        hand = set(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', white, {'W': 1}, [source.ref])
        self.priority(session, 'B')
        for spell in spells:
            action = next((r for r in self.legal(session, 'B') if r['id'] == 'cast:'+spell.ref), None)
            self.assertTrue(action is None or source.ref not in action['target_schema']['legal_refs'])
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:B', {'action_id': 'cast:'+spell.ref, 'pay': 'auto', 'targets': [source.ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': 'cast:'+spells[0].ref, 'targets': [source.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(library-1, len(session.state.players['A'].zones['library']))
        drawn = set(session.state.players['A'].zones['hand']) - hand
        self.assertEqual(1, len(drawn))
        for oid in drawn:
            for viewer in 'BCD':
                self.assertNotIn(session.state.cards[oid].ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
        self.assertEqual((6, 5), (session.engine._numeric_stat(source.object_id, 'power'), session.engine._numeric_stat(source.object_id, 'toughness')))
        self.assertEqual(0, session.state.cards[source.object_id].marked_damage)
        self.private_replay(session, private)

    def test_complete_keyword_line_keeps_siblings_and_rejects_unowned_list_grammar(self):
        base = CardRecord(oracle_id='generic-printed-protection-list', name='Generic Protection Compiler',
            mana_cost='{2}', mana_value=2, type_line='Creature — Human', oracle_text='', power='2', toughness='2',
            loyalty=None, defense=None, colors=(), color_identity=(), keywords=('Flying', 'Protection', 'Vigilance', 'Ward'),
            produced_mana=(), layout='normal', released_at='2026-08-07', legalities={'commander': 'legal'}, faces=(), raw={})
        for valid in ('Flying, protection from blue, from black, and from red, vigilance',
                'Ward {1}, protection from blue, from black, and from red'):
            ir = compile_oracle_card(replace(base, oracle_text=valid), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(any('protection.typed.debt' in node.capability_dependencies for face in ir.faces for node in face.nodes))
            if valid.startswith('Ward'):
                self.assertTrue(any('trigger.keyword.ward.fixed_generic' in node.capability_dependencies for face in ir.faces for node in face.nodes))
        for text in ('Flying, from blue', 'Protection from blue; from red',
                'Protection from blue, black, and red', 'Protection from blue, from unknown, and from red',
                'Protection from blue, from black, and from red, mystery',
                'Ward {X}, protection from blue, from black, and from red',
                'Protection from blue, from black, and from red. Draw a card.'):
            ir = compile_oracle_card(replace(base, oracle_text=text), capability_registry=self.registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, text)
            self.assertTrue(ir.material_residuals)

    def test_actual_white_ability_loss_ends_protection_and_allows_a_blue_bounce(self):
        session = self.session(754025)
        source = self.add(session.engine, NAMES[0])
        humble = self.add(session.engine, 'Humble', zone='hand')
        blue = self.add(session.engine, 'Boomerang', seat='B', zone='hand')
        self.prepare(session, mana={'W': 1, 'C': 1})
        session.state.players['B'].mana_pool['U'] = 2
        self.checkpoint(session)
        self.cast(session, 'A', humble, {'W': 1, 'C': 1}, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((), self.qualities(session, source))
        self.cast(session, 'B', blue, {'U': 2}, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_actual_red_sweeper_prevents_damage_only_to_matching_protected_object(self):
        session = self.session(754030)
        source = self.add(session.engine, NAMES[0])
        human = self.add(session.engine, 'Generic Protection Human')
        pyro = self.add(session.engine, 'Pyroclasm', seat='B', zone='hand')
        self.prepare(session, active='B', actor='B', mana={'R': 1, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'B', pyro, {'R': 1, 'C': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield', 0), (session.state.cards[source.object_id].zone, session.state.cards[source.object_id].marked_damage))
        self.assertEqual('graveyard', session.state.cards[human.object_id].zone)
        self.replay(session)

    def test_actual_oversoul_rejects_three_colored_blockers_and_accepts_white_blocker(self):
        session = self.session(754040)
        source = self.add(session.engine, NAMES[0])
        source.acquired_control_turn_count = -1
        denied = [self.add(session.engine, 'Generic Protection '+kind, seat='B') for kind in ('Wizard', 'Vampire', 'Warrior')]
        allowed = self.add(session.engine, 'Generic Protection Human', seat='B')
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, {source.ref: 'B'})
        self.blockers(session)
        for card in denied:
            self.block(session, {card.ref: [source.ref]}, valid=False)
        self.block(session, {allowed.ref: [source.ref]})
        session = self.replay(session, load=True)
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual('graveyard', session.state.cards[allowed.object_id].zone)
        self.assertEqual((2, 40), (session.state.cards[source.object_id].marked_damage, session.state.players['B'].life))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertTrue(all(session.state.cards[c.object_id].zone == 'battlefield' for c in denied))
        self.replay(session)

    def test_actual_elite_cast_retains_stats_first_strike_vigilance_and_all_protections(self):
        session = self.session(754050)
        source = self.add(session.engine, NAMES[1], zone='hand')
        self.prepare(session, mana={'W': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'W': 2})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        card = session.state.cards[source.object_id]
        self.assertEqual('battlefield', card.zone)
        self.assertEqual((2, 2), (session.engine._numeric_stat(card.object_id, 'power'), session.engine._numeric_stat(card.object_id, 'toughness')))
        self.assertTrue({'first strike', 'vigilance'} <= session.engine._combat_keywords(card))
        self.assertEqual(('vampire', 'werewolf', 'zombie'), self.qualities(session, card))
        self.replay(session)

    def test_actual_elite_subtype_blocking_preserves_first_strike_and_vigilance(self):
        session = self.session(754060)
        source = self.add(session.engine, NAMES[1])
        source.acquired_control_turn_count = -1
        denied = [self.add(session.engine, 'Generic Protection '+kind, seat='B') for kind in ('Vampire', 'Werewolf', 'Zombie')]
        allowed = self.add(session.engine, 'Generic Protection Human', seat='B')
        self.prepare(session)
        self.checkpoint(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.attack(session, {source.ref: 'B'})
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.blockers(session)
        for card in denied:
            self.block(session, {card.ref: [source.ref]}, valid=False)
        self.block(session, {allowed.ref: [source.ref]})
        session = self.replay(session, load=True)
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual('graveyard', session.state.cards[allowed.object_id].zone)
        self.assertEqual(('battlefield', 0, False), (session.state.cards[source.object_id].zone,
            session.state.cards[source.object_id].marked_damage, session.state.cards[source.object_id].tapped))
        self.assertEqual(40, session.state.players['B'].life)
        self.replay(session)

    def test_protection_runtime_minimum_cannot_be_stripped_from_list_programs(self):
        for i, name in enumerate(NAMES):
            session = self.session(754070+i)
            card = self.add(session.engine, name)
            programs = [p for p in session.engine.semantics.programs_for_oracle(card.oracle_id)
                if any(h.get('handler_id') == 'ability.static.protection.v1' for h in p.handlers)]
            self.assertEqual(1, len(programs), name)
            program = programs[0]
            deps = tuple(c for c in program.capability_dependencies if c != 'protection.typed.debt')
            stripped = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict() if deps else None)
            binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:protection.typed.debt', binding['blockers'])

    def test_verified_protection_owner_withdrawal_leaves_both_originals_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        changed = deepcopy(raw)
        cap = 'protection.typed.debt'
        next(r for r in changed['capabilities'] if r['id'] == cap).update(status='blocked', blockers=['withdrawn protection authority'])
        registry = CapabilityRegistry(changed)
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_whole_original_list_producer_and_combat_protection_omissions_are_killed(self):
        from quorune.protection import ProtectionVerdict
        cases = (('quorune.compiler.ability_keyword_fragments.parse_protection_line', None,
                    'test_complete_originals_bind_every_quality_and_preserve_sibling_keywords'),
                 ('quorune.engine.protection_verdict', ProtectionVerdict.ALLOWED,
                    'test_actual_elite_subtype_blocking_preserves_first_strike_and_vigilance'))
        for owner, result, method in cases:
            with patch(owner, return_value=result):
                with self.assertRaises(AssertionError):
                    getattr(type(self)(method), method)()
