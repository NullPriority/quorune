"""Real Monstrosity activations produce one independently owned public result."""
import json
import unittest
from dataclasses import replace
import designation_event_fixture as fixtures
from quorune.designation_event_model import DESIGNATION_EVENT_CAPABILITY
from quorune.record import authoritative_state_hash


class MonstrousEventNativeTests(fixtures.DesignationEventFixture, unittest.TestCase):
    def test_actual_wildfire_designation_damage_uses_departed_source_and_only_opponents_and_their_creatures(self):
        s = self.session(613657160)
        source = self.add(s.engine, 'Wildfire Cerberus', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        own = self.add(s.engine, 'Grizzly Bears', ref='wildfire-friendly')
        foreign = [self.add(s.engine, 'Grizzly Bears', seat=seat, ref='wildfire-foreign-' + seat) for seat in 'BC']
        indestructible = self.add(s.engine, 'Darksteel Myr', seat='D')
        self.assert_closed(s, source)
        self.ready(s, {'C': 9, 'R': 3, 'U': 1})
        self.cast_for(s, 'A', source, reserve={'C': 5, 'R': 2, 'U': 1})
        self.settle(s)
        self.activate(s, 'A', source, reserve={'U': 1})
        self.resolve_top(s)
        self.assertEqual(1, s.state.cards[source.object_id].monstrous_value)
        self.assertEqual(1, self.trigger_count(s, source))
        self.cast(s, 'A', bounce, [source.ref])
        self.resolve_top(s)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        life = {seat: s.state.players[seat].life for seat in 'ABCD'}
        s = self.replay(s, load=True)
        self.settle(s)
        for seat in 'ABCD':
            self.assertEqual(life[seat] - (0 if seat == 'A' else 2), s.state.players[seat].life)
        self.assertEqual('battlefield', s.state.cards[own.object_id].zone)
        self.assertEqual(0, s.state.cards[own.object_id].marked_damage)
        for card in foreign:
            self.assertEqual('graveyard', s.state.cards[card.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[indestructible.object_id].zone)
        self.assertEqual(2, s.state.cards[indestructible.object_id].marked_damage)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_designation_minima_cannot_be_removed_with_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s = self.session(613657100)
        checked = 0
        for name in ('Alpha Deathclaw', 'Relic Seeker', 'Valeron Wardens'):
            source = self.add(s.engine, name)
            for program in s.engine.semantics.programs_for_oracle(source.oracle_id):
                if DESIGNATION_EVENT_CAPABILITY not in program.capability_dependencies:
                    continue
                deps = [c for c in program.capability_dependencies if c != DESIGNATION_EVENT_CAPABILITY]
                changed = replace(program, coverage=[], capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(result['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:' + DESIGNATION_EVENT_CAPABILITY, result['blockers'])
                checked += 1
        self.assertEqual(3, checked)

    def test_actual_alpha_entry_and_monstrous_branches_work_once_and_legacy_subscription_replays(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                s = self.session(613657101 + legacy)
                source = self.add(s.engine, 'Alpha Deathclaw', zone='hand')
                first = self.add(s.engine, 'Grizzly Bears', seat='B', ref='alpha-entry-target')
                second = self.add(s.engine, 'Grizzly Bears', seat='B', ref='alpha-designation-target')
                self.assert_closed(s, source)
                if legacy:
                    from quorune.semantics import SemanticProgram
                    from quorune.card_programs.binding import bind_semantic_program_runtime
                    current = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                                   if p.event == 'permanent.enter')
                    # Independently captured, published v656 subscription shape.
                    condition = {'event_subscriptions': [
                        {'event': 'permanent.enter', 'condition': {'field': 'card', 'op': 'eq', 'value': '$source.ref'}},
                        {'event': 'permanent.becomes_monstrous', 'condition': {'field': 'card', 'op': 'eq', 'value': '$source.ref'}},
                    ]}
                    deps = [c for c in current.capability_dependencies if c != DESIGNATION_EVENT_CAPABILITY]
                    old = replace(current, event_condition=condition, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile='commander_review').to_dict(),
                                  provenance={**current.provenance, 'authored_by': 'oracle-ir-v656'})
                    old = SemanticProgram.from_dict(json.loads(json.dumps(old.to_dict())))
                    self.assertTrue(bind_semantic_program_runtime(old, capability_registry=self.registry,
                                                                 profile='commander_review')['strict'])
                    s.engine.semantics.put(old)
                    self.assertTrue(s.engine.semantic_program_is_current_trusted(old))
                self.ready(s, {'C': 14, 'B': 3, 'G': 3})
                self.cast_for(s, 'A', source, reserve={'C': 10, 'B': 2, 'G': 2})
                self.target(s, 'A', [first.ref])
                self.settle(s)
                self.assertEqual('graveyard', s.state.cards[first.object_id].zone)
                self.activate(s, 'A', source)
                self.resolve_top(s)
                self.assertEqual(4, s.state.cards[source.object_id].monstrous_value)
                self.assertEqual(4, s.state.cards[source.object_id].counters.get('+1/+1'))
                self.assertEqual(0 if legacy else 1, self.trigger_count(s, source))
                if not legacy:
                    self.target(s, 'A', [second.ref])
                s = self.replay(s, load=True)
                self.settle(s)
                self.assertEqual('battlefield' if legacy else 'graveyard', s.state.cards[second.object_id].zone)
                self.activate(s, 'A', source)
                self.settle(s)
                self.assertEqual(4, s.state.cards[source.object_id].counters.get('+1/+1'))
                self.assertEqual(0, self.trigger_count(s, source))
                self.private(s)
                self.replay(s, load=True)

    def test_actual_arbor_and_keepsake_select_qualified_target_and_queued_result_survives_source_bounce(self):
        for name in ('Arbor Colossus', 'Keepsake Gorgon'):
            with self.subTest(name=name):
                s = self.session(613657120 + len(name))
                source = self.add(s.engine, name, zone='hand')
                legal = self.add(s.engine, 'Concordia Pegasus', seat='B')
                illegal = self.add(s.engine, 'Grizzly Bears' if name == 'Arbor Colossus' else 'Keepsake Gorgon', seat='B', ref='designation-illegal-target')
                bounce = self.add(s.engine, 'Unsummon', zone='hand')
                self.assert_closed(s, source)
                self.ready(s, {'C': 9, 'G': 6, 'B': 4, 'U': 1})
                self.cast_for(s, 'A', source, reserve={'U': 1})
                self.settle(s)
                self.activate(s, 'A', source, reserve={'U': 1})
                self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
                before = authoritative_state_hash(s.state)
                r = s.act('pilot:A', {'action_id': 'choose', 'targets': [illegal.ref]})
                self.assertFalse(r.ok)
                self.assertEqual(before, authoritative_state_hash(s.state))
                self.target(s, 'A', [legal.ref])
                self.cast(s, 'A', bounce, [source.ref])
                self.resolve_top(s)
                self.assertEqual('hand', s.state.cards[source.object_id].zone)
                self.assertIsNone(s.state.cards[source.object_id].monstrous_value)
                s = self.replay(s, load=True)
                self.settle(s)
                self.assertEqual('graveyard', s.state.cards[legal.object_id].zone)
                self.assertEqual('battlefield', s.state.cards[illegal.object_id].zone)
                self.replay(s, load=True)

    def test_actual_splitting_slime_copies_lki_without_counters_or_designation_and_token_retriggers(self):
        s = self.session(613657140)
        source = self.add(s.engine, 'Splitting Slime', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        self.assert_closed(s, source)
        self.ready(s, {'C': 11, 'G': 6, 'U': 1})
        self.cast_for(s, 'A', source, reserve={'C': 8, 'G': 4, 'U': 1})
        self.settle(s)
        self.activate(s, 'A', source, reserve={'C': 4, 'G': 2, 'U': 1})
        self.resolve_top(s)
        self.assertEqual(3, s.state.cards[source.object_id].monstrous_value)
        self.assertEqual(6, s.engine._numeric_stat(source.object_id, 'power'))
        self.cast(s, 'A', bounce, [source.ref])
        self.resolve_top(s)
        s = self.replay(s, load=True)
        self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        first = tokens[0]
        self.assertEqual((3, 3), tuple(s.engine._numeric_stat(first.object_id, k) for k in ('power', 'toughness')))
        self.assertEqual({}, first.counters)
        self.assertIsNone(first.monstrous_value)
        self.activate(s, 'A', first)
        self.settle(s)
        self.assertEqual(3, s.state.cards[first.object_id].monstrous_value)
        self.assertEqual(6, s.engine._numeric_stat(first.object_id, 'power'))
        self.assertEqual(2, sum(c.is_token and c.zone == 'battlefield' for c in s.state.cards.values()))
        self.replay(s, load=True)

    def test_actual_kalemne_designation_exiles_indestructible_artifacts_and_enchantments_without_targets(self):
        s = self.session(613657150)
        source = self.add(s.engine, "Kalemne's Captain", zone='hand')
        artifact = self.add(s.engine, 'Sol Ring', seat='B')
        indestructible = self.add(s.engine, 'Darksteel Myr', seat='C')
        enchantment = self.add(s.engine, 'Fervor')
        creature = self.add(s.engine, 'Grizzly Bears', seat='B')
        land = self.add(s.engine, 'Forest', seat='D')
        self.assert_closed(s, source)
        self.ready(s, {'C': 8, 'W': 4})
        self.cast_for(s, 'A', source, reserve={'C': 5, 'W': 2})
        self.settle(s)
        self.activate(s, 'A', source)
        self.resolve_top(s)
        self.assertEqual(1, self.trigger_count(s, source))
        s = self.replay(s, load=True)
        self.settle(s)
        for card in (artifact, indestructible, enchantment):
            self.assertEqual('exile', s.state.cards[card.object_id].zone)
        for card in (source, creature, land):
            self.assertEqual('battlefield', s.state.cards[card.object_id].zone)
        self.replay(s, load=True)
