"""CR122/608 and original Hapatra/Omen rulings, with complete printed sources."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.compiler.counter_removal_templates import (
    all_counter_removal_effect_template, all_named_source_counter_removal_effect_template,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.target_all_counter_removal import (
    NAMED_TARGET_ALL_COUNTER_CAPABILITY as NAMED, OPTIONAL_TARGET_ALL_COUNTER_CAPABILITY as OPTIONAL,
    target_all_counter_variant_capabilities, named_target_all_counter_payload_dependencies,
    target_all_counter_variant_runtime_dependencies,
)
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
from quorune.semantic_runtime.counter_removal_handlers import AllCounterRemovalHandler
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as controls
import test_bound_effect_programs as compiler_controls

FIXTURE = ROOT / 'tests/fixtures/target-all-counter-variants-cards.json'
CANDIDATES = ("Hapatra's Mark", 'Purging Stormbrood // Absorb Essence')


class TargetAllCounterModelTests(unittest.TestCase):
    def test_named_target_three_keeps_legacy_source_two_and_bare_all_encodings(self):
        named = all_counter_removal_effect_template('Remove all -1/-1 counters from target creature.')
        _, effects, schema, mechanics = named.compiled()
        self.assertEqual(3, effects[0]['schema_version'])
        self.assertIn(NAMED, target_all_counter_variant_capabilities(effects=effects,
            target_schema=schema, mechanic_ids=mechanics))
        legacy = all_named_source_counter_removal_effect_template(
            'Remove all -1/-1 counters from this creature.', card_name='Source')
        self.assertEqual({'op': 'remove_all_counters', 'schema_version': 2, 'counter': '-1/-1',
            'card': '$source.zone_object', 'source': '$source'}, legacy[1][0])
        ordinary = all_counter_removal_effect_template('Remove all counters from target permanent.')
        self.assertEqual({'op': 'remove_all_counters', 'card': '$target.0', 'source': '$source'}, ordinary.effects[0])
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Counter selection',
            seats=tuple('ABCD'), active_seats=tuple('ABCD'), apnap_order=tuple('ABCD'))
        for version in (2, 3):
            plan = AllCounterRemovalHandler().lower({**effects[0], 'schema_version': version,
                'card': 'recipient', 'source': 'source'}, context)
            intent, = plan.intents
            self.assertEqual(('-1/-1', 'recipient'), (intent.counter_name, intent.object_ref))
        for changed in ({'schema_version': True}, {'schema_version': 4}, {'counter': 'Charge'},
                        {'counter': ''}, {'extra': True}):
            with self.subTest(changed=changed), self.assertRaises(SemanticNodeError):
                AllCounterRemovalHandler().lower({**effects[0], 'card': 'recipient', 'source': 'source', **changed}, context)

    def test_optional_target_and_named_payload_cannot_borrow_unsupported_scope(self):
        for text in ('Remove all counters from up to one target creature.',
                     'Remove all charge counters from target artifact you control.'):
            _, effects, schema, mechanics = all_counter_removal_effect_template(text).compiled()
            self.assertTrue(target_all_counter_variant_capabilities(effects=effects,
                target_schema=schema, mechanic_ids=mechanics))
            for invalid in ({**schema, 'count': True}, {**schema, 'count': 2},
                            {**schema, 'up_to': True}, {**schema, 'up_to': 2}, {**schema, 'unknown': True}):
                self.assertFalse(target_all_counter_variant_capabilities(effects=effects,
                    target_schema=invalid, mechanic_ids=mechanics))
        named = all_counter_removal_effect_template('Remove all charge counters from target artifact.').effects[0]
        for changed in ({'schema_version': 2}, {'card': '$source.zone_object'}, {'counter': 'Charge'}, {'amount': 1000}):
            with self.assertRaises(ValueError):named_target_all_counter_payload_dependencies({**named, **changed})
        for text in ('Remove all counters from target player.', 'Remove all counters from each creature.',
                     'Remove all counters from up to two target creatures.',
                     'Remove all charge counters from up to one target artifact.'):
            self.assertIsNone(all_counter_removal_effect_template(text))

    def test_payload_and_optional_target_drive_runtime_minima_without_coverage(self):
        for text, capability in (('Remove all -1/-1 counters from target creature.', NAMED),
                                 ('Remove all counters from up to one target creature.', OPTIONAL)):
            _, effects, schema, _ = all_counter_removal_effect_template(text).compiled()
            program = SimpleNamespace(effects=effects, target_schema=schema, coverage=(), handlers=())
            self.assertIn(capability, target_all_counter_variant_runtime_dependencies(program))
            with self.assertRaises(ValueError):
                target_all_counter_variant_runtime_dependencies(SimpleNamespace(effects=effects,
                    target_schema={**schema, 'count': 2}, coverage=(), handlers=()))
            if capability == NAMED:
                for version in (True, 1, 2, '3'):
                    with self.assertRaises(ValueError):
                        target_all_counter_variant_runtime_dependencies(SimpleNamespace(
                            effects=({**effects[0], 'schema_version': version},), target_schema=schema, coverage=(), handlers=()))


class TargetAllCounterCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry = load_default_capability_registry()

    def test_exact_linked_and_optional_source_spans_require_both_new_authorities(self):
        texts = ("Target creature you control gains hexproof until end of turn. Remove all -1/-1 counters from it.",
                 'Remove all counters from up to one target creature.')
        for text, required in zip(texts, (NAMED, OPTIONAL)):
            card = compiler_controls.record(text)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text[node.span.start:node.span.end], node.text)
            self.assertIn(required, node.capability_dependencies)
            for missing in (required, 'counter.removal.all_effect', 'target.revalidate_resolution'):
                raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(r for r in raw['capabilities'] if r['id'] == missing).update(status='blocked', blockers=['all-counter authority withdrawn'])
                registry = CapabilityRegistry(raw)
                registry.mark_evidence_verified(self.registry.evidence_fingerprint)
                self.assertNotEqual('exact', compile_oracle_card(card,
                    capability_registry=registry, capability_profile='commander_review').status)

    def test_complete_originals_admit_both_faces_and_keep_material_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target-all-originals.sqlite3'
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                semantics = SemanticRegistry()
                for name in (*CANDIDATES, 'Blitz Leech', 'Vampire Hexmage', 'Give // Take', 'Mutated Cultist'):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level='provisional',
                            capability_registry=self.registry, capability_profile='commander_review',
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=self.registry, capability_profile='commander_review')
                        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                        self.assertEqual(name in (*CANDIDATES, 'Blitz Leech', 'Vampire Hexmage'),
                            bool(binding['strict_capability_ready'] and binding['trust_basis'] == 'capability_closed'), binding['blockers'])
                        if name in CANDIDATES:
                            required = NAMED if name == CANDIDATES[0] else OPTIONAL
                            ability = next(p for p in semantics.programs_for_oracle(card.oracle_id)
                                if 'remove_all_counters' in str(p.effects))
                            deps = tuple(c for c in ability.capability_dependencies if c != required)
                            changed = replace(ability, coverage=(), capability_dependencies=deps,
                                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                profile='commander_review')
                            self.assertFalse(bound['strict'])
                            self.assertTrue(any(required in blocker for blocker in bound['blockers']), bound['blockers'])
                            with patch('quorune.compiler.counter_removal_templates.target_all_counter_variant_template', return_value=None):
                                self.assertNotEqual('exact', compile_oracle_card(card,
                                    capability_registry=self.registry, capability_profile='commander_review').status)


class TargetAllCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.AttachmentFollowupRuntimeTests.session
    add = controls.AttachmentFollowupRuntimeTests.add
    prepare = controls.AttachmentFollowupRuntimeTests.prepare
    closed = controls.AttachmentFollowupRuntimeTests.closed
    actions = controls.AttachmentFollowupRuntimeTests.actions
    checkpoint = controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = controls.AttachmentFollowupRuntimeTests.resolve
    replay = controls.AttachmentFollowupRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'target-all-native.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('All-counter target variants', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self, seed, name=CANDIDATES[0], *, counters=None, extra=(), target=True):
        session = self.session(seed)
        source = self.add(session.engine, name, zone='hand')
        recipient = self.add(session.engine, 'Generic Bound Body', seat='B', ref='all-counter-recipient') if target else None
        if recipient is not None:
            session.engine.change_control(recipient.object_id, 'A')
            recipient.counters.update({'-1/-1': 3, 'charge': 2} if counters is None else counters)
        opponent = self.add(session.engine, 'Generic Bound Body', seat='B', ref='all-counter-opponent') if target else None
        private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='all-counter-private')
        responses = [self.add(session.engine, n, zone='hand', ref='all-counter-response-' + str(i)) for i, n in enumerate(extra)]
        self.prepare(session, source, {color: 20 for color in 'CWUBRG'})
        self.checkpoint(session)
        return session, source, recipient, opponent, private, responses

    def cast(self, session, card, *, targets=(), face=None, seat='A'):
        self.closed(session, card)
        offered = next(a for a in self.actions(session, seat) if a.get('card') == card.ref
            and (face is None or a.get('face') == face))
        command = {'action_id': offered['id'], 'pay': 'auto'}
        if targets:command['targets'] = list(targets)
        if face is not None:command['face'] = face
        if offered.get('cost_options'):command['cost_option'] = offered['cost_options'][0]['id']
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, session, command, *, seat='A'):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def select(self, session, targets=()):
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': list(targets)})
        self.assertTrue(result.ok, result.summary)

    def private_replay(self, session, private):
        for seat in 'ABC':self.assertNotIn(private.ref, str(session.packet('pilot:' + seat, full=True)))
        return self.replay(session, load=True)

    def test_actual_hapatra_grants_hexproof_and_removes_only_negative_counters_or_none(self):
        for index, negative in enumerate((3, 0)):
            session, source, target, opponent, private, _ = self.world(302100 + index,
                counters={'-1/-1': negative, 'charge': 2} if negative else {'charge': 2})
            offered = next(a for a in self.actions(session) if a.get('card') == source.ref)
            self.assertIn(target.ref, offered['target_schema']['legal_refs'])
            self.assertNotIn(opponent.ref, offered['target_schema']['legal_refs'])
            self.reject(session, {'action_id': offered['id'], 'targets': [opponent.ref], 'pay': 'auto'})
            self.cast(session, source, targets=(target.ref,))
            session = self.private_replay(session, private)
            self.assertIsNone(self.resolve(session))
            current = session.state.cards[target.object_id]
            self.assertEqual(('B', 'A'), (current.owner, current.controller))
            self.assertEqual({'charge': 2}, dict(current.counters))
            self.assertIn('hexproof', session.engine._combat_keywords(current))
            self.assertEqual(2, session.engine._numeric_stat(current.object_id, 'power'))
            self.private_replay(session, private)

    def test_named_kind_filter_mutation_is_killed_by_a_fresh_original_cast(self):
        original = AllCounterRemovalHandler.lower
        def omit_kind(handler, effect, context):
            plan = original(handler, effect, context)
            return replace(plan, intents=tuple(replace(intent, counter_name=None) for intent in plan.intents))
        with patch.object(AllCounterRemovalHandler, 'lower', omit_kind):
            with self.assertRaises(AssertionError):
                self.test_actual_hapatra_grants_hexproof_and_removes_only_negative_counters_or_none()

    def test_optional_zero_target_mutation_is_killed_by_a_fresh_original_cast(self):
        import quorune.targets as owner
        original = owner._target_count_bounds
        def require_one(raw):
            bounds = original(raw)
            return (1, bounds[1]) if raw.get('up_to') == 1 else bounds
        with patch.object(owner, '_target_count_bounds', require_one):
            with self.assertRaises(AssertionError):
                self.test_actual_purging_uses_zero_targets_or_removes_every_current_counter_kind()

    def test_actual_purging_uses_zero_targets_or_removes_every_current_counter_kind(self):
        for index, empty in enumerate((True, False)):
            session, source, target, _, private, _ = self.world(302110 + index, CANDIDATES[1], target=not empty)
            self.cast(session, source)
            self.assertEqual('semantic.target', self.resolve(session).kind)
            self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
            self.assertIn('flying', session.engine._combat_keywords(session.state.cards[source.object_id]))
            if empty:
                self.reject(session, {'action_id': 'choose', 'targets': [source.ref, source.ref]})
            session = self.private_replay(session, private)
            self.select(session, () if empty else (target.ref,))
            self.assertIsNone(self.resolve(session))
            if target is not None:self.assertFalse(session.state.cards[target.object_id].counters)
            self.private_replay(session, private)

    def test_hapatra_blinked_target_is_not_redirected_to_a_new_incarnation(self):
        session, source, target, _, private, responses = self.world(302120, extra=('Ephemerate',))
        old = session.state.cards[target.object_id].logical_object_id
        self.cast(session, source, targets=(target.ref,))
        self.cast(session, responses[0], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertNotEqual(old, current.logical_object_id)
        self.assertNotIn('hexproof', session.engine._combat_keywords(current))
        self.private_replay(session, private)

    def test_purging_source_departure_preserves_its_counter_removal_trigger(self):
        session, source, target, _, private, responses = self.world(302130, CANDIDATES[1], extra=('Unsummon',))
        self.cast(session, source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.select(session, (target.ref,))
        self.cast(session, responses[0], targets=(source.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.cards[target.object_id].counters)
        self.private_replay(session, private)

    def test_actual_absorb_essence_omen_grants_all_results_and_shuffles_only_on_resolution(self):
        for index, response in enumerate((None, 'Unsummon', 'Counterspell')):
            session, source, target, _, private, responses = self.world(302140 + index, CANDIDATES[1],
                counters={}, extra=() if response is None else (response,))
            before = session.state.players['A'].stats.get('shuffle_count', 0)
            self.cast(session, source, targets=(target.ref,), face='Absorb Essence')
            self.assertEqual('Absorb Essence', session.state.stack[-1].chosen_face)
            if response is not None:
                ref = target.ref if response == 'Unsummon' else session.state.stack[-1].ref
                self.cast(session, responses[0], targets=(ref,))
            self.assertIsNone(self.resolve(session))
            current = session.state.cards[source.object_id]
            self.assertEqual('library' if response is None else 'graveyard', current.zone)
            self.assertEqual(before + int(response is None), session.state.players['A'].stats.get('shuffle_count', 0))
            if response is None:
                self.assertEqual((4, 8), tuple(session.engine._numeric_stat(target.object_id, stat) for stat in ('power', 'toughness')))
                self.assertTrue({'hexproof', 'lifelink'} <= session.engine._combat_keywords(session.state.cards[target.object_id]))
                self.assertEqual(([], []), (current.known_to, current.revealed_to))
            self.private_replay(session, private)

    def test_actual_purging_ward_charges_the_opponent_two_life_or_counters_the_spell(self):
        for index, pay in enumerate((True, False)):
            session = self.session(302150 + index)
            source = self.add(session.engine, CANDIDATES[1])
            response = self.add(session.engine, 'Shock', seat='B', zone='hand')
            private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='ward-private')
            session.state.players['B'].mana_pool['R'] = 1
            self.prepare(session, source, {})
            self.checkpoint(session)
            while session.pending_principals()[0] != 'pilot:B':
                result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
                self.assertTrue(result.ok, result.summary)
            self.cast(session, response, targets=(source.ref,), seat='B')
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:B'], list(session.pending_principals()))
            self.reject(session, {'action_id': 'choose', 'pay': pay}, seat='A')
            session = self.private_replay(session, private)
            result = session.act('pilot:B', {'action_id': 'choose', 'pay': pay})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(38 if pay else 40, session.state.players['B'].life)
            self.assertEqual(2 if pay else 0, session.state.cards[source.object_id].marked_damage)
            self.assertEqual('graveyard', session.state.cards[response.object_id].zone)
            self.private_replay(session, private)


class HexingSquelcherNativeTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Hexing Squelcher"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        work = Path(cls.temporary.name)
        files = ROOT / "tests/fixtures"
        support = {
            "target-all-counter-variants-cards.json": {cls.source_name},
            "event-spell-copy-cards.json": {"Grizzly Bears"},
            "activation-restriction-material-cards.json": {"Unsummon"},
            "ability-source-counter-cards.json": {"Counterspell"},
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains"},
        }
        fixtures = []
        for name, names in support.items():
            payload = json.loads((files / name).read_bytes())
            payload["cards"] = [row for row in payload["cards"] if row["name"] in names]
            assert names == {row["name"] for row in payload["cards"]}, name
            ids = {row["oracle_id"] for row in payload["cards"]}
            payload["rulings"] = [row for row in payload.get("rulings", ()) if row["oracle_id"] in ids]
            source = work / name
            source.write_text(json.dumps(payload), encoding="utf-8")
            fixtures.append(source)
        path = work / "hexing.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Quoted static life Ward", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def act(self, game, seat, command, *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def record(self, game, comment):
        from hashlib import sha256
        from quorune.record import replay_record

        folder = os.environ.get("QUORUNE_HEXING_RECORD_PARENT")
        if folder is None:
            self.replay(game, load=True)
            return
        path = Path(folder) / comment
        game.save(path)
        result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, result["final_state_hash"])
        path.with_suffix(".receipt.json").write_text(json.dumps({
            "manifest_sha256": sha256((path / "manifest.json").read_bytes()).hexdigest(),
            "final_state_sha256": state_hash, "commands": len(game.commands),
        }, indent=2) + "\n", encoding="utf-8")

    def life_wards(self, game, card):
        from quorune.ability_fragments import canonical_ability_fragments
        from quorune.trigger_participation import WardSpec

        data = game.engine._effective_card_data(game.state.cards[card.object_id])
        fragments = canonical_ability_fragments(data.get("ability_fragments", ()))
        return [ward.life_payment for ward in fragments if isinstance(ward, WardSpec)]

    def pay_ward(self, game, *, pay):
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["pilot:B"], list(game.pending_principals()))
        for actor in ("A", "C", "D"):
            self.assertIsNone(game.packet("pilot:" + actor, full=True)["decision"])
        self.act(game, "A", {"action_id": "choose", "pay": pay}, accepted=False)
        self.act(game, "B", {"action_id": "choose", "pay": pay})

    def test_hexing_grants_independent_wards_to_other_current_controlled_creatures(self):
        for pay_second in (True, False):
            with self.subTest(pay_second=pay_second):
                game = self.session(7022101 + int(pay_second))
                sources = [self.add(game.engine, self.source_name, ref=f"hexing-grant-{i}") for i in range(2)]
                own = self.add(game.engine, "Grizzly Bears", ref="hexing-own")
                other = self.add(game.engine, "Grizzly Bears", seat="B", ref="hexing-other")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                for card in sources:
                    self.assert_closed(game, card)
                self.prepare(game)
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                for card in (*sources, own):
                    self.assertEqual([2, 2], self.life_wards(game, card))
                self.assertEqual([], self.life_wards(game, other))
                self.cast_for(game, "B", bounce, [own.ref])
                if game.state.pending_decision.kind == "trigger.order":
                    rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                    self.assertEqual(2, len(rows))
                    self.act(game, "A", {"action_id": "order", "triggers": [row["id"] for row in rows]})
                self.record(game, f"hexing-two-wards-pending-{int(pay_second)}-game-record")
                self.pay_ward(game, pay=True)
                self.pay_ward(game, pay=pay_second)
                self.settle(game)
                self.assertEqual(36 if pay_second else 38, game.state.players["B"].life)
                self.assertEqual("hand" if pay_second else "battlefield", game.state.cards[own.object_id].zone)
                self.record(game, f"hexing-two-wards-settled-{int(pay_second)}-game-record")

    def test_hexing_printed_ward_remains_and_grant_ends_when_source_leaves(self):
        game = self.session(7022103)
        source = self.add(game.engine, self.source_name)
        own = self.add(game.engine, "Grizzly Bears")
        spells = [self.add(game.engine, "Unsummon", seat="B", zone="hand", ref=f"hexing-bounce-{i}") for i in range(3)]
        self.assert_closed(game, source)
        self.prepare(game)
        game.state.players["B"].mana_pool["U"] = 3
        self.checkpoint(game)
        self.assertEqual([2], self.life_wards(game, source))
        self.assertEqual([2], self.life_wards(game, own))
        self.cast_for(game, "B", spells[0], [own.ref])
        self.pay_ward(game, pay=False)
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.cast_for(game, "B", spells[1], [source.ref])
        self.pay_ward(game, pay=True)
        self.settle(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual([], self.life_wards(game, own))
        self.cast_for(game, "B", spells[2], [own.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[own.object_id].zone)
        self.assertEqual(38, game.state.players["B"].life)
        self.record(game, "hexing-intrinsic-ward-and-departure-game-record")

    def test_hexing_keeps_its_own_and_its_controllers_spells_uncounterable(self):
        game = self.session(7022104)
        source = self.add(game.engine, self.source_name, zone="hand")
        followup = self.add(game.engine, "Grizzly Bears", zone="hand")
        counters = [self.add(game.engine, "Counterspell", seat="B", zone="hand", ref=f"hexing-counter-{i}") for i in range(2)]
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "R": 1, "G": 2})
        game.state.players["B"].mana_pool["U"] = 4
        self.checkpoint(game)
        for card, counter in zip((source, followup), counters):
            self.cast_for(game, "A", card, reserve={"G": 2} if card is source else None)
            target = next(item for item in game.state.stack if item.card_object_id == card.object_id)
            self.cast_for(game, "B", counter, [target.ref])
            self.settle(game)
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
            self.assertEqual("graveyard", game.state.cards[counter.object_id].zone)
        self.record(game, "hexing-intrinsic-and-controlled-uncounterability-game-record")

    def test_hexing_layer_six_requires_the_granted_life_ward_capability(self):
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(self.source_name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, self.source_name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = {program.ability_id: program for program in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({f"static:front:n{index}" for index in range(1, 5)}, set(programs))
        grant = programs["static:front:n4"]
        owner = "trigger.keyword.ward.fixed_nonmana"
        dependencies = tuple(cap for cap in grant.capability_dependencies if cap != owner)
        original = replace(grant, capability_dependencies=dependencies,
                           capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(original, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{owner}", binding["blockers"])
