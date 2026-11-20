"""Normal casting, entry and priority for reflexive token-creation results."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.token_creation_reflexive_templates import token_creation_reflexive_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import _reviewed_effect_template, compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.reflexive_trigger_model import REFLEXIVE_PAYMENT_CAPABILITY, TOKEN_CREATION_REFLEXIVE_FIELD
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.token_creation_reflexive import token_creation_reflexive_parts
from quorune.target_source import TARGETING_SOURCE_CONTEXT
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FAEBLOOM = 'Faebloom Trick'
HORDE = 'Diregraf Horde'
EASTFARTHING = 'Eastfarthing Farmer'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'
ORIGINALS = (FIXTURES / 'death-keyword-absence-cards.json', FIXTURES / 'token-creation-reflexive-results-cards.json')


class TokenCreationReflexiveCompilerTests(unittest.TestCase):
    def test_two_clause_template_preserves_the_parent_and_rejects_unowned_antecedents(self):
        def compile_effect(text):
            return _reviewed_effect_template(text, card_name='Synthetic fixture')

        parent = 'Create two 1/1 blue Faerie creature tokens with flying.'
        child = 'Tap target creature an opponent controls.'
        template, effects, targets, mechanics = token_creation_reflexive_template(
            parent + ' When you do, ' + child, compile_effect=compile_effect,
        )
        self.assertIsNotNone(template)
        self.assertIsNone(targets)
        creation, consequence = token_creation_reflexive_parts(effects[0])
        self.assertEqual(compile_effect(parent)[1][0], creation)
        self.assertEqual(compile_effect(child)[2], consequence.to_dict()['target_schema'])
        self.assertEqual(compile_effect(child)[1][0], consequence.to_dict()['effects'][0])
        self.assertIn('fixed-action-reflexive-trigger', mechanics)
        for bad in (
            parent + ' If you do, ' + child,
            'You may ' + parent.lower() + ' When you do, ' + child,
            parent + ' When you do, ' + child + ' Draw a card.',
            parent + ' When you do, untap it.',
            parent + ' When you do, draw a card.',
        ):
            with self.subTest(text=bad):
                self.assertIsNone(token_creation_reflexive_template(bad, compile_effect=compile_effect))

    def test_originals_close_both_phases_and_raw_parents_fail_closed(self):
        registry = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'token-reflexive.sqlite3'
            build_fixture_database(ORIGINALS, path)
            with CardDatabase(path) as db:
                for name, event, child_op in (
                    (FAEBLOOM, 'resolve', 'tap'), (HORDE, 'permanent.enter.self', 'exile_public_graveyard_targets'),
                ):
                    with self.subTest(card=name):
                        original = db.lookup(name, fuzzy=False)
                        ir = compile_oracle_card(original, capability_registry=registry, capability_profile=PROFILE)
                        self.assertEqual('exact', ir.status, ir.material_residuals)
                        node, = ir.faces[0].nodes
                        self.assertEqual(event, node.event)
                        self.assertIsNone(node.target_schema)
                        self.assertTrue({REFLEXIVE_PAYMENT_CAPABILITY, 'target.revalidate_resolution',
                            'trigger.placement.apnap', 'token.creation.fixed_definition'} <= set(node.capability_dependencies))
                        creation, child = token_creation_reflexive_parts(node.effects[0])
                        self.assertEqual(2, creation['quantity'])
                        self.assertEqual(child_op, child.effects[0]['op'])
                        self.assertIsNotNone(child.target_schema)

                        result, semantics = register_card(db, registry, name)
                        self.assertTrue(result['strict_capability_ready'], result)
                        program, = [p for p in semantics.programs_for_oracle(original.oracle_id) if p.event == event]
                        for changes in (
                            {'event': 'activate'},
                            {'target_schema': child.to_dict()['target_schema']},
                            {'effects': [creation]},
                            {'effects': [{**program.effects[0], TOKEN_CREATION_REFLEXIVE_FIELD: {
                                **child.to_dict(), 'schema_version': True,
                            }}]},
                        ):
                            with self.subTest(changes=changes):
                                raw = bind_semantic_program_runtime(replace(program, **changes),
                                    capability_registry=registry, profile=PROFILE)
                                self.assertFalse(raw['strict'], raw)
                                self.assertTrue(any('runtime_token_creation_reflexive:invalid:' in value
                                    for value in raw['blockers']), raw['blockers'])
                farmer, _ = register_card(db, registry, EASTFARTHING)
                self.assertTrue(farmer['strict_capability_ready'], farmer['blockers'])


class TokenCreationReflexiveNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] in {'Generic Bound Plains', 'Unsummon'}]
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'token-reflexive-native.sqlite3'
        build_fixture_database([
            basic, FIXTURES / 'targeted-graveyard-exile-cards.json',
            FIXTURES / 'controller-creature-keyword-absence-cards.json',
            FIXTURES / 'token-creation-trigger-cards.json',
            FIXTURES / 'additional-token-definition-cards.json',
            FIXTURES / 'ability-source-counter-cards.json', *ORIGINALS,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Reflexive token targets', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def tokens(self, session, *, seat='A', name=None):
        return [card for card in session.state.cards.values()
            if card.zone == 'battlefield' and card.controller == seat and card.is_token
            and (name is None or card.printed_name == name)]

    def child(self, session, parent_ref):
        children = [item for item in session.state.stack if item.context.get('reflexive_parent') == parent_ref]
        self.assertEqual(1, len(children))
        return children[0]

    def choose_targets(self, session, refs):
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': list(refs)})
        self.assertTrue(result.ok, result.summary)

    def assert_rejected(self, session, actor, command):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def finish_private(self, session, hidden):
        for seat in session.engine.active_seats:
            if seat != hidden.owner:
                packet = json.dumps(session.packet('pilot:' + seat, full=True))
                self.assertNotIn(hidden.ref, packet)
                self.assertNotIn(hidden.printed_name, packet)
        self.replay(session, load=True)

    def test_paid_faebloom_queues_one_child_after_replacement_and_retries_atomically(self):
        from quorune.rules import reflexive_triggers

        game = self.session(60312701)
        provider = self.add(game.engine, FAEBLOOM, zone='hand')
        parallel = self.add(game.engine, 'Parallel Lives')
        doubler = self.add(game.engine, 'Anointed Procession')
        target = self.add(game.engine, 'Grizzly Bears', seat='B', ref='reflexive-fae-target')
        protected = self.add(game.engine, 'Great Sable Stag', seat='C')
        private = self.add(game.engine, 'Humble', seat='D', zone='hand', ref='reflexive-fae-private')
        for card in (provider, parallel, doubler, target, protected):
            self.assert_closed(game, card)
        self.prepare(game, mana={'C': 2, 'U': 1})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        parent, = game.state.stack
        self.assertEqual([], parent.targets)
        self.assertEqual('spell', parent.kind)
        self.until(game, lambda s: s.state.pending_decision.kind == 'replacement.order')
        self.assertEqual([(parent.ref, 'spell')], [(i.ref, i.kind) for i in game.state.stack])
        self.assertEqual([], self.tokens(game))
        self.assertFalse(game.state.pending_trigger_batches)

        game = self.replay(game, load=True)
        options = game.packet('pilot:A', full=True)['decision']['ctx']['options']
        self.assertEqual({parallel.ref, doubler.ref}, {item['source'] for item in options})
        command = {'action_id': 'choose', 'replacement': next(o['id'] for o in options if o['source'] == parallel.ref)}
        self.assert_rejected(game, 'B', command)
        before = authoritative_state_hash(game.state)
        original = reflexive_triggers.enqueue_token_creation_reflexive
        inserted = []

        def inject(host, source, consequence):
            child_ref = original(host, source, consequence)
            inserted.append((child_ref, len(host.state.players['A'].zones['battlefield'])))
            raise GameRuleError('Injected failure after reflexive creation')

        with patch.object(reflexive_triggers, 'enqueue_token_creation_reflexive', inject):
            rejected = game.act('pilot:A', command)
        self.assertFalse(rejected.ok)
        self.assertIn('Injected failure after reflexive creation', rejected.summary)
        self.assertEqual(1, len(inserted))
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual([], self.tokens(game))
        result = game.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

        self.assertEqual('graveyard', game.state.cards[provider.object_id].zone)
        child = self.child(game, parent.ref)
        self.assertEqual('A', child.controller)
        self.assertEqual(['U'], child.context[TARGETING_SOURCE_CONTEXT]['colors'])
        self.assertEqual(8, len(self.tokens(game, name='Faerie Token')))
        self.assertTrue(all('flying' in game.engine._combat_keywords(c) for c in self.tokens(game, name='Faerie Token')))
        self.assertEqual(8, len(self.tokens(game)))
        self.assertEqual('semantic.target', game.state.pending_decision.kind)
        legal = game.packet('pilot:A', full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertIn(target.ref, legal)
        self.assertNotIn(protected.ref, legal)
        self.assert_rejected(game, 'A', {'action_id': 'choose', 'targets': [protected.ref]})
        self.assertFalse(game.state.cards[target.object_id].tapped)
        game = self.replay(game, load=True)
        self.choose_targets(game, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[target.object_id].tapped)
        self.finish_private(game, private)

    def test_paid_faebloom_copy_with_no_legal_child_target_keeps_its_tokens(self):
        game = self.session(60312702)
        provider = self.add(game.engine, FAEBLOOM, zone='hand')
        copy_spell = self.add(game.engine, 'Twincast', seat='B', zone='hand')
        protected = self.add(game.engine, 'Great Sable Stag', seat='C')
        for index in range(2):
            self.add(game.engine, 'Island', seat='B', ref=f'faerie-copy-payment-{index}')
        for card in (provider, protected, copy_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={'C': 2, 'U': 1})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        parent, = game.state.stack
        self.cast(game, 'B', copy_spell, [parent.ref])
        self.resolve_top(game)
        copied = game.state.stack[-1]
        self.assertEqual(('spell_copy', 'B'), (copied.kind, copied.controller))
        self.resolve_top(game)
        self.assertEqual([parent.ref], [item.ref for item in game.state.stack])
        copy_tokens = self.tokens(game, seat='B', name='Faerie Token')
        self.assertEqual(2, len(copy_tokens))
        self.resolve_top(game)
        child = self.child(game, parent.ref)
        self.assertEqual('A', child.controller)
        legal = game.packet('pilot:A', full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertEqual({token.ref for token in copy_tokens}, set(legal))
        self.assertNotIn(protected.ref, legal)
        self.choose_targets(game, [copy_tokens[0].ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[copy_tokens[0].object_id].tapped)
        self.assertEqual(2, len(self.tokens(game, name='Faerie Token')))
        self.replay(game, load=True)

    def test_paid_horde_uses_original_departure_and_revalidates_two_public_graveyards(self):
        game = self.session(60312703)
        provider = self.add(game.engine, HORDE, zone='hand')
        bounce = self.add(game.engine, 'Unsummon', seat='B', zone='hand')
        response = self.add(game.engine, 'Rotten Reunion', seat='B', zone='hand')
        self.add(game.engine, 'Island', seat='B')
        self.add(game.engine, 'Swamp', seat='B')
        first = self.add(game.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='horde-b-graveyard')
        second = self.add(game.engine, 'Grizzly Bears', seat='C', zone='graveyard', ref='horde-c-graveyard')
        private = self.add(game.engine, 'Humble', seat='D', zone='hand', ref='horde-private-hand')
        for card in (provider, bounce, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={'C': 4, 'B': 1})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        self.resolve_top(game)
        parent, = game.state.stack
        self.assertEqual(('triggered_ability', []), (parent.kind, parent.targets))
        original_identity = game.state.cards[provider.object_id].logical_object_id
        self.cast(game, 'B', bounce, [game.state.cards[provider.object_id].ref])
        self.resolve_top(game)
        self.assertEqual('hand', game.state.cards[provider.object_id].zone)
        self.resolve_top(game)
        child = self.child(game, parent.ref)
        self.assertEqual(original_identity, child.context[TARGETING_SOURCE_CONTEXT]['logical_object_id'])
        self.assertEqual(2, len(self.tokens(game, name='Zombie Token')))
        self.assertTrue(all('decayed' in game.engine._combat_keywords(c) for c in self.tokens(game)))
        self.assertEqual('semantic.target', game.state.pending_decision.kind)
        self.assert_rejected(game, 'B', {'action_id': 'choose', 'targets': [first.ref]})
        self.choose_targets(game, [first.ref, second.ref])
        self.cast(game, 'B', response, [first.ref])
        self.resolve_top(game)
        self.assertEqual('exile', game.state.cards[first.object_id].zone)
        self.assertEqual('graveyard', game.state.cards[second.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual('exile', game.state.cards[second.object_id].zone)
        self.finish_private(game, private)

    def test_paid_horde_can_choose_zero_targets_on_the_separate_result(self):
        game = self.session(60312704)
        provider = self.add(game.engine, HORDE, zone='hand')
        target = self.add(game.engine, 'Grizzly Bears', seat='B', zone='graveyard')
        self.assert_closed(game, provider)
        self.prepare(game, mana={'C': 4, 'B': 1})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        self.resolve_top(game)
        parent, = game.state.stack
        self.resolve_top(game)
        self.child(game, parent.ref)
        self.assertEqual('semantic.target', game.state.pending_decision.kind)
        self.choose_targets(game, [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual('graveyard', game.state.cards[target.object_id].zone)
        self.assertEqual(2, len(self.tokens(game, name='Zombie Token')))
        self.replay(game, load=True)

    def test_omitting_the_child_breaks_the_paid_horde_witness(self):
        from quorune.rules import reflexive_triggers

        with patch.object(reflexive_triggers, 'enqueue_token_creation_reflexive', return_value=None):
            with self.assertRaisesRegex(AssertionError, '1 != 0'):
                self.test_paid_horde_can_choose_zero_targets_on_the_separate_result()

    def test_verified_producer_reflexive_and_result_withdrawals_block_the_right_originals(self):
        from copy import deepcopy

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (FAEBLOOM, HORDE, 'Grizzly Bears')
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
        withdrawals = {
            REFLEXIVE_PAYMENT_CAPABILITY: {FAEBLOOM, HORDE},
            'token.creation.fixed_definition': {FAEBLOOM, HORDE},
            'permanent.tap.effect': {FAEBLOOM},
            'card.exile.public_graveyard': {HORDE},
        }
        for cap, blocked in withdrawals.items():
            with self.subTest(withdrawn=cap):
                rows = deepcopy(source)
                next(r for r in rows['capabilities'] if r['id'] == cap).update(
                    status='blocked', blockers=['Exact token creation reflexive dependency withdrawn'])
                registry = verified(rows)
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn('status:' + cap + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready['strict_capability_ready'], (name, ready['blockers']))
