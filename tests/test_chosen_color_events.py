"""Chosen-color casts use event-time colors and the existing public designation."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.compiler.chosen_color_event_bindings import chosen_color_event_binding_spec
from quorune.rules.chosen_color_events import CHOSEN_COLOR_CAST_CAPABILITY, chosen_color_cast_dependencies
from quorune.trigger_discovery import semantic_event_condition_matches


class ChosenColorEventTests(unittest.TestCase):
    def matches(self, *, chosen='R', colors=('R',), caster='B', any_player=False):
        actor = 'a player casts' if any_player else 'you cast'
        spec = chosen_color_event_binding_spec(
            f'Whenever {actor} a spell of the chosen color, you gain 1 life.')
        source = SimpleNamespace(controller='B', owner='A', ref='SOURCE', object_id='source-id', annotations={})
        if chosen is not None:
            source.annotations['chosen_color'] = chosen
        return semantic_event_condition_matches(
            SimpleNamespace(state=SimpleNamespace(active_player='D')), spec.condition, source=source,
            context={'controller': caster, 'player': caster, 'colors': list(colors),
                     'color_identity': ['W'], 'mana_colors_spent': ['U']})

    def test_current_color_membership_and_controller_relation(self):
        self.assertTrue(self.matches())
        self.assertTrue(self.matches(colors=('R', 'U')))
        self.assertTrue(self.matches(caster='D', any_player=True))
        for kwargs in ({'caster': 'A'}, {'colors': ('U',)}, {'colors': ()},
                       {'chosen': None}, {'chosen': 'C'}, {'chosen': 'red'}, {'chosen': True}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.matches(**kwargs))

    def test_nearby_grammar_remains_unbound(self):
        for text in (
            'Whenever you casts a spell of the chosen color, you gain 1 life.',
            'Whenever an opponent casts a spell of the chosen color, you gain 1 life.',
            'Whenever you cast a spell of the chosen color or type, you gain 1 life.',
            'Whenever you copy a spell of the chosen color, you gain 1 life.',
            'Whenever you cast a spell with the chosen color identity, you gain 1 life.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(chosen_color_event_binding_spec(text))

    def test_compiler_and_runtime_require_same_designation_dependency(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from test_granted_skulk import record
        text = ('As this artifact enters, choose a color.\n'
                'Whenever you cast a spell of the chosen color, you gain 1 life.')
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(text, 'Artifact'), capability_registry=registry,
                                 capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[-1]
        self.assertIn(CHOSEN_COLOR_CAST_CAPABILITY, node.capability_dependencies)
        spec = chosen_color_event_binding_spec(text.splitlines()[-1])
        program = SimpleNamespace(event=spec.event, event_condition=spec.condition)
        self.assertEqual((CHOSEN_COLOR_CAST_CAPABILITY,), chosen_color_cast_dependencies(program))
        program.event = 'spell.cast_or_copy'
        self.assertEqual((), chosen_color_cast_dependencies(program))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        for capability in (CHOSEN_COLOR_CAST_CAPABILITY, 'zone.entry.public_designation'):
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == capability).update(
                status='blocked', blockers=['chosen color owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Artifact'),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)

    def test_multiple_observers_keep_separate_public_designations(self):
        self.assertTrue(self.matches(chosen='R', colors=('R', 'U')))
        self.assertTrue(self.matches(chosen='U', colors=('R', 'U')))
        self.assertFalse(self.matches(chosen='G', colors=('R', 'U')))

    def test_exact_implementation_mutants_are_distinguished(self):
        import quorune.trigger_discovery as owner
        original = owner._semantic_condition_operator_matches
        with patch.object(owner, '_semantic_condition_operator_matches',
                side_effect=lambda op, actual, expected: actual == expected if op == 'in'
                else original(op, actual, expected)):
            with self.assertRaises(AssertionError):
                self.assertTrue(self.matches(colors=('R', 'U')))
        value = owner.semantic_event_value
        with patch.object(owner, 'semantic_event_value', side_effect=lambda host, raw, **kwargs:
                kwargs['source'].owner if raw == '$source.controller' else value(host, raw, **kwargs)):
            with self.assertRaises(AssertionError):
                self.assertTrue(self.matches())


class ChosenColorNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'chosen-color-events.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/crime-cards.json',
            ROOT / 'tests/fixtures/chosen-color-event-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Chosen color event witnesses', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_crime import CrimeNativeTests as _crime_helpers
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _actions
    from test_chosen_color_mana import ChosenColorManaRuntimeTests as _color_helpers
    session = _crime_helpers.session
    start = _crime_helpers.start
    assert_closed = _crime_helpers.assert_closed
    cast = _crime_helpers.cast
    add = _actions.add
    checkpoint = _actions.checkpoint
    resolve = _actions.resolve
    replay = _actions.replay
    choose_color = _color_helpers.choose_color

    def priority(self, session, actor):
        for _ in range(32):
            session.engine.pump()
            if session.state.priority_player == actor:
                return
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.fail('Requested caster did not receive priority')

    def test_actual_chosen_entry_multicast_copy_and_countered_spell(self):
        session = self.session(644001)
        ring = self.add(session.engine, 'Prism Ring', zone='hand', ref='CHOSEN_RING')
        shock = self.add(session.engine, 'Shock', zone='hand', ref='CHOSEN_SHOCK')
        copy = self.add(session.engine, 'Reverberate', zone='hand', ref='CHOSEN_COPY')
        counter = self.add(session.engine, 'Counterspell', zone='hand', ref='CHOSEN_COUNTER')
        second = self.add(session.engine, 'Shock', zone='hand', ref='CHOSEN_COUNTERED_SHOCK')
        colorless = self.add(session.engine, 'Ornithopter', zone='hand', ref='CHOSEN_COLORLESS')
        wrong = self.add(session.engine, 'Watchwolf', zone='hand', ref='CHOSEN_OTHER_COLORS')
        for card in (ring, shock, second, copy, counter, colorless, wrong):
            self.assert_closed(session, card)
        self.start(session)
        self.cast(session, ring)
        self.choose_color(session, 'R')
        self.assertEqual('R', session.state.cards[ring.object_id].annotations['chosen_color'])
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                       if row.get('card') == shock.ref)
        before = authoritative_state_hash(session.state)
        with patch('quorune.rules.casting.commit.enqueue_trigger_batch',
                   side_effect=GameRuleError('injected after chosen-color discovery')):
            rejected = session.act('pilot:A', {'action_id': offered['id'], 'targets': ['B'], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertIn('injected after chosen-color discovery', rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, shock, ('B',))
        original = next(item for item in session.state.stack if item.card_object_id == shock.object_id)
        self.priority(session, 'A')
        self.cast(session, copy, (original.ref,))
        # Complete any copied-spell retarget choice without inventing a new cast.
        from test_crime import CrimeNativeTests
        CrimeNativeTests.settle(self, session)
        self.assertEqual(42, session.state.players['A'].life)
        self.assertEqual(36, session.state.players['B'].life)
        self.cast(session, second, ('B',))
        countered = next(item for item in session.state.stack if item.card_object_id == second.object_id)
        self.priority(session, 'A')
        self.cast(session, counter, (countered.ref,))
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[second.object_id].zone)
        self.assertEqual(36, session.state.players['B'].life)
        self.assertEqual(43, session.state.players['A'].life)
        self.cast(session, colorless)
        self.resolve(session)
        self.cast(session, wrong)
        self.resolve(session)
        self.assertEqual(43, session.state.players['A'].life)
        self.replay(session, load=True)

    def test_actual_multicolor_any_caster_and_optional_result_principals(self):
        session = self.session(644002)
        knight = self.add(session.engine, 'Diamond Knight', zone='hand', ref='CHOSEN_KNIGHT')
        borrowed = self.add(session.engine, 'Diamond Knight', seat='B', ref='CHOSEN_BORROWED_KNIGHT')
        borrowed.owner = 'A'
        borrowed.annotations['chosen_color'] = 'G'
        watchers = [self.add(session.engine, name, ref='CHOSEN_' + str(i)) for i, name in enumerate(
            ('Diamond Mare', 'Paradise Plume', 'Jeweled Torque', 'Curse of Wizardry'))]
        for source in watchers:
            source.annotations['chosen_color'] = 'G'
        spells = [self.add(session.engine, 'Colossal Might', seat=actor, zone='hand',
                           ref='CHOSEN_MULTICOLOR_' + actor) for actor in ('B', 'A')]
        for card in (knight, borrowed, *watchers, *spells):
            self.assert_closed(session, card)
        self.start(session)
        session.state.players['B'].mana_pool.update(C=6, U=4, R=4, G=4)
        self.checkpoint(session)
        self.cast(session, knight)
        self.choose_color(session, 'G')
        self.priority(session, 'B')
        self.cast(session, spells[0], (knight.ref,), actor='B')
        session = self.settle_options(session, accept=True)
        self.assertEqual(43, session.state.players['A'].life)
        self.assertEqual(39, session.state.players['B'].life)
        self.assertEqual(0, session.state.cards[knight.object_id].counters.get('+1/+1', 0))
        borrowed = session.state.cards[borrowed.object_id]
        self.assertEqual(('A', 'B', 'G'), (borrowed.owner, borrowed.controller, borrowed.annotations['chosen_color']))
        self.assertEqual(1, borrowed.counters.get('+1/+1', 0))
        self.priority(session, 'A')
        self.cast(session, spells[1], (knight.ref,))
        session = self.settle_options(session, accept=False)
        self.assertEqual(43, session.state.players['A'].life)
        self.assertEqual(1, session.state.cards[knight.object_id].counters.get('+1/+1', 0))
        self.assertEqual(1, session.state.cards[borrowed.object_id].counters.get('+1/+1', 0))
        for viewer in 'ABCD':
            visible = 'CRIME_PRIVATE_D_HAND' in json.dumps(session.packet('pilot:' + viewer, full=True))
            self.assertEqual(viewer == 'D', visible, viewer)
        self.replay(session, load=True)

    def settle_options(self, session, *, accept):
        from quorune.record import authoritative_state_hash
        checked_pending = False
        for _ in range(180):
            session.engine.pump()
            decision = session.state.pending_decision
            if decision is not None and decision.kind != 'priority':
                self.assertEqual(['pilot:A'], list(session.pending_principals()))
                context = session.packet('pilot:A', full=True)['decision']['ctx']
                if decision.kind == 'trigger.order':
                    result = session.act('pilot:A', {'action_id': 'order',
                        'triggers': [row['id'] for row in context['triggers']]})
                    self.assertTrue(result.ok, result.summary)
                    continue
                command = {'action_id': 'choose'}
                if decision.kind == 'semantic.choice':
                    if 'cost' in context:
                        command['pay'] = accept
                    else:
                        command['choice'] = 'apply' if accept else 'decline'
                else:
                    self.fail('Unexpected optional-result decision: ' + str((decision.kind, context)))
                if not checked_pending:
                    session = self.replay(session, load=True)
                    checked_pending = True
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B', command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                result = session.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
            elif session.state.stack:
                self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
            else:
                return session
        self.fail('Optional color event results did not settle')
