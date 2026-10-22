from __future__ import annotations

"""CR 111.3/603.2/702.108: Prowess belongs to the created token definition."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls

NAMES = ('Goblin Wizardry', 'Elemental Eruption', 'Monastery Mentor', "Stormchaser's Talent")
CAPABILITY = 'token.creation.prowess_definition'


class FixedProwessTokenTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ProvokeTests.session
    add = controls.ProvokeTests.add
    prepare = controls.ProvokeTests.prepare
    checkpoint = controls.ProvokeTests.checkpoint
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
        path = Path(cls.temp.name)/'fixed-prowess-tokens.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-prowess-token-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Token Prowess', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def legal(self, session, seat='A'):
        return session.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']

    def order_triggers(self, session):
        if session.state.pending_decision is not None and session.state.pending_decision.kind == 'trigger.order':
            principal = session.pending_principals()[0]
            seat = principal.removeprefix('pilot:')
            refs = [item.ref for item in session.state.pending_trigger_batches[0].items if item.controller == seat]
            self.assertTrue(refs)
            result = session.act(principal, {'action_id': 'order', 'triggers': refs})
            self.assertTrue(result.ok, result.summary)

    def resolve(self, session):
        for _ in range(16):
            self.order_triggers(session)
            decision = controls.ProvokeTests.resolve(self, session)
            if decision is None or decision.kind != 'trigger.order':return decision
        self.fail('Token trigger ordering did not settle')

    def cast(self, session, seat, card, payment, targets=(), **choices):
        self.priority(session, seat)
        rows = [r for r in self.legal(session, seat) if r['id'] == 'cast:'+card.ref]
        self.assertEqual(1, len(rows), card.printed_name)
        command = {'action_id': rows[0]['id'], 'pay': 'manual', 'payment': payment, 'targets': list(targets), **choices}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def tokens(self, session, subtype=None):
        return [c for c in session.state.cards.values() if c.zone == 'battlefield' and c.is_token
            and (subtype is None or subtype in session.engine._type_parts(session.engine._effective_card_data(c)['type_line'])[1])]

    def stats(self, session, card):
        return (session.engine._numeric_stat(card.object_id, 'power'), session.engine._numeric_stat(card.object_id, 'toughness'))

    def test_complete_fixed_prowess_token_creators_have_normal_admission(self):
        for i, name in enumerate(NAMES):
            session = self.session(758010+i)
            source = self.add(session.engine, name, zone='hand')
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])

    def test_actual_wizardry_tokens_trigger_once_per_own_noncreature_cast_and_expire(self):
        session = self.session(758030)
        source = self.add(session.engine, 'Goblin Wizardry', zone='hand')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand', ref='prowess-bolt-A')
        bears = self.add(session.engine, 'Grizzly Bears', zone='hand')
        foreign = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand', ref='prowess-bolt-B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='prowess-private-C')
        session.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'C': 4, 'R': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'C': 3, 'R': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'goblin')
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertEqual((1, 1), self.stats(session, token))
            data = session.engine._effective_card_data(token)
            self.assertEqual(['R'], data['colors'])
            self.assertIn('wizard', session.engine._type_parts(data['type_line'])[1])
        self.cast(session, 'B', foreign, {'R': 1}, ['A'])
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(self.stats(session, t) == (1, 1) for t in tokens))
        self.cast(session, 'A', bears, {'G': 1, 'C': 1})
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(self.stats(session, t) == (1, 1) for t in tokens))
        self.cast(session, 'A', bolt, {'R': 1}, ['B'])
        self.order_triggers(session)
        prowess = [item for item in session.state.stack if item.kind == 'triggered_ability']
        self.assertEqual(2, len(prowess))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(self.stats(session, t) == (2, 2) for t in tokens))
        self.assertEqual((37, 37), (session.state.players['A'].life, session.state.players['B'].life))
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertTrue(all(self.stats(session, t) == (1, 1) for t in tokens))
        self.private_replay(session, private)

    def test_actual_mentor_triggers_existing_sources_but_not_the_newly_created_token(self):
        session = self.session(758040)
        source = self.add(session.engine, 'Monastery Mentor', zone='hand')
        bolts = [self.add(session.engine, 'Lightning Bolt', zone='hand', ref='mentor-bolt-'+str(i)) for i in range(2)]
        self.prepare(session, mana={'W': 1, 'C': 2, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'W': 1, 'C': 2})
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', bolts[0], {'R': 1}, ['B'])
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'monk')
        self.assertEqual(1, len(tokens))
        old = tokens[0]
        self.assertEqual(((3, 3), (1, 1)), (self.stats(session, source), self.stats(session, old)))
        self.cast(session, 'A', bolts[1], {'R': 1}, ['B'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'monk')
        self.assertEqual(2, len(tokens))
        new = [c for c in tokens if c.object_id != old.object_id]
        self.assertEqual(1, len(new))
        self.assertEqual(((4, 4), (2, 2), (1, 1)), (self.stats(session, source), self.stats(session, old), self.stats(session, new[0])))
        self.assertEqual(34, session.state.players['B'].life)
        self.replay(session)

    def test_actual_eruption_storm_copies_create_flying_prowess_definitions_without_casting_copies(self):
        session = self.session(758050)
        source = self.add(session.engine, 'Elemental Eruption', zone='hand')
        opener = self.add(session.engine, 'Expedite', zone='hand')
        body = self.add(session.engine, 'Generic Bound Body')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.prepare(session, mana={'C': 4, 'R': 4})
        self.checkpoint(session)
        self.cast(session, 'A', opener, {'R': 1}, [body.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', source, {'C': 4, 'R': 2})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'dragon')
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertEqual((4, 4), self.stats(session, token))
            self.assertIn('elemental', session.engine._type_parts(session.engine._effective_card_data(token)['type_line'])[1])
            self.assertIn('flying', session.engine._combat_keywords(token))
        self.cast(session, 'A', bolt, {'R': 1}, ['B'])
        self.order_triggers(session)
        self.assertEqual(2, sum(item.kind == 'triggered_ability' for item in session.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(self.stats(session, t) == (5, 5) for t in tokens))
        self.replay(session)

    def test_actual_talent_entry_and_levels_keep_token_and_trigger_timing(self):
        session = self.session(758055)
        source = self.add(session.engine, "Stormchaser's Talent", zone='hand')
        returned = self.add(session.engine, 'Counterspell', zone='graveyard')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.prepare(session, mana={'U': 3, 'C': 8, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'U': 1})
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'otter')
        self.assertEqual(1, len(tokens))
        old = tokens[0]
        self.assertEqual((1, 1), self.stats(session, old))
        def level(number, payment):
            abilities = [a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if a.effect_text == f'Level {number}']
            self.assertEqual(1, len(abilities))
            action = 'activate:'+source.ref+':'+abilities[0].ability_id
            self.assertIn(action, {row['id'] for row in self.legal(session)})
            result = session.act('pilot:A', {'action_id': action, 'pay': 'manual', 'payment': payment})
            self.assertTrue(result.ok, result.summary)
        level(2, {'U': 1, 'C': 3})
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [returned.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[returned.object_id].zone)
        self.assertEqual((1, 1), self.stats(session, old))
        level(3, {'U': 1, 'C': 5})
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', bolt, {'R': 1}, ['B'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'otter')
        self.assertEqual(2, len(tokens))
        new = [c for c in tokens if c.object_id != old.object_id]
        self.assertEqual(1, len(new))
        self.assertEqual(((2, 2), (1, 1)), (self.stats(session, old), self.stats(session, new[0])))
        for token in tokens:self.assertEqual(['U', 'R'], session.engine._effective_card_data(token)['colors'])
        self.replay(session)

    def test_actual_copy_token_inherits_prowess_without_copying_temporary_bonus(self):
        session = self.session(758056)
        source = self.add(session.engine, 'Goblin Wizardry', zone='hand')
        copy = self.add(session.engine, 'Cackling Counterpart', zone='hand')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.prepare(session, mana={'C': 4, 'R': 2, 'U': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'C': 3, 'R': 1})
        self.assertIsNone(self.resolve(session))
        old = self.tokens(session, 'goblin')
        self.assertEqual(2, len(old))
        self.cast(session, 'A', copy, {'C': 1, 'U': 2}, [old[0].ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'goblin')
        self.assertEqual(3, len(tokens))
        new = [c for c in tokens if c.object_id not in {t.object_id for t in old}]
        self.assertEqual(1, len(new))
        self.assertTrue(all(self.stats(session, t) == (2, 2) for t in old))
        self.assertEqual((1, 1), self.stats(session, new[0]))
        self.cast(session, 'A', bolt, {'R': 1}, ['B'])
        self.order_triggers(session)
        self.assertEqual(3, sum(item.kind == 'triggered_ability' for item in session.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(self.stats(session, t) == (3, 3) for t in old))
        self.assertEqual((2, 2), self.stats(session, new[0]))
        self.replay(session)

    def test_token_ability_loss_stops_future_triggers_but_not_already_locked_instances(self):
        session = self.session(758057)
        source = self.add(session.engine, 'Goblin Wizardry', zone='hand')
        bolts = [self.add(session.engine, 'Lightning Bolt', zone='hand', ref='loss-bolt-'+str(i)) for i in range(2)]
        humble = self.add(session.engine, 'Humble', zone='hand')
        self.prepare(session, mana={'C': 4, 'R': 3, 'W': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'C': 3, 'R': 1})
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session, 'goblin')
        self.assertEqual(2, len(tokens))
        self.cast(session, 'A', bolts[0], {'R': 1}, ['B'])
        self.order_triggers(session)
        self.cast(session, 'A', humble, {'C': 1, 'W': 1}, [tokens[0].ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(((2, 3), (3, 3)), (self.stats(session, tokens[0]), self.stats(session, tokens[1])))
        self.cast(session, 'A', bolts[1], {'R': 1}, ['B'])
        self.order_triggers(session)
        self.assertEqual(1, sum(item.kind == 'triggered_ability' for item in session.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(((2, 3), (4, 4)), (self.stats(session, tokens[0]), self.stats(session, tokens[1])))
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertTrue(all(self.stats(session, t) == (1, 1) for t in tokens))
        self.replay(session)

    def test_definition_grammar_and_fragment_pairing_are_closed(self):
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        template = fixed_token_creation_effect_template('Create a 1/1 white Monk creature token with prowess.')
        self.assertIsNotNone(template)
        def caps(effect):return fixed_token_creation_node_capabilities(effects=(effect,), target_schema=None, mechanic_ids=set(template.mechanics))
        self.assertIn(CAPABILITY, caps(template.effect))
        for mutation in ('missing_fragment', 'extra_fragment', 'missing_keyword', 'wrong_kind'):
            effect = deepcopy(template.effect)
            characteristics = effect['characteristics']
            if mutation == 'missing_fragment':characteristics['ability_fragments'] = []
            elif mutation == 'extra_fragment':characteristics['ability_fragments'] *= 2
            elif mutation == 'missing_keyword':characteristics['keywords'] = []
            else:characteristics['ability_fragments'][0]['value']['kind'] = 'storm'
            self.assertEqual((), caps(effect), mutation)
        for text in ('Create a 1/1 white Monk creature token with prowess 2.',
                'Create a 1/1 white Monk creature token with prowess and unknown.',
                'Create a 1/1 white Monk creature token with prowess and prowess.'):
            self.assertIsNone(fixed_token_creation_effect_template(text), text)

    def test_created_keyword_and_underlying_trigger_are_independent_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(758060)
        source = self.add(session.engine, 'Goblin Wizardry', zone='hand')
        programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects]
        self.assertEqual(1, len(programs))
        program = programs[0]
        for cap in (CAPABILITY, 'trigger.keyword.prowess'):
            deps = tuple(d for d in program.capability_dependencies if d != cap)
            stripped = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict() if deps else None)
            bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+cap, bound['blockers'])
        effects = deepcopy(program.effects)
        effects[0]['characteristics']['ability_fragments'] = []
        bound = bind_semantic_program_runtime(replace(program, effects=effects), capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertTrue(any(b.startswith('runtime_token_prowess:invalid:') for b in bound['blockers']), bound['blockers'])

    def test_canonically_verified_definition_owner_withdrawal_keeps_complete_creators_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn token Prowess definition authority'])
        registry = CapabilityRegistry(raw)
        decl = json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index = build_index(registry_value=raw, declaration_source=decl, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_fresh_keyword_fragment_and_actual_trigger_omissions_are_killed(self):
        import quorune.compiler.token_templates as templates
        original_keywords = templates._fixed_keyword_list
        def no_keyword(value):
            return None if value and 'prowess' in value.casefold() else original_keywords(value)
        original_producer = templates._single_fixed_token_creation_effect_template
        def no_fragment(text):
            template = original_producer(text)
            if template is None:return None
            effect = deepcopy(template.effect)
            if 'Prowess' in effect.get('characteristics', {}).get('keywords', ()):
                effect['characteristics']['ability_fragments'] = []
            return replace(template, effect=effect)
        cases = (('quorune.compiler.token_templates._fixed_keyword_list', no_keyword,
                    'test_complete_fixed_prowess_token_creators_have_normal_admission'),
                 ('quorune.compiler.token_templates._single_fixed_token_creation_effect_template', no_fragment,
                    'test_complete_fixed_prowess_token_creators_have_normal_admission'),
                 ('quorune.trigger_discovery._granted_prowess_trigger_items', lambda *args,**kwargs: [],
                    'test_actual_wizardry_tokens_trigger_once_per_own_noncreature_cast_and_expire'))
        for owner, replacement, method in cases:
            with patch(owner, replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
