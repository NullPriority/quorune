from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_card_return import event_card_return_effect, event_card_return_intent
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class EventCardReturnCompilerTests(unittest.TestCase):
    def test_event_battlefield_return_lowers_owner_incarnation_tap_and_entry_counter(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for suffix, tapped, counter in (
            ("under its owner's control", False, None),
            ("tapped under its owner's control", True, None),
            ("tapped under its owner's control with a +1/+1 counter on it", True, '+1/+1'),
        ):
            text = 'When this creature dies, return it to the battlefield ' + suffix + '.'
            ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node = next(n for n in ir.faces[0].nodes if n.kind == 'triggered_ability')
            effect = node.effects[0]
            self.assertEqual('reanimate', effect['op'])
            self.assertEqual('$context.card', effect['card'])
            self.assertEqual('$context.card_zone_change_counter', effect['expected_zone_change_counter'])
            self.assertEqual('$context.owner', effect['controller'])
            self.assertEqual(tapped, effect['tapped'])
            self.assertEqual(counter, effect['entry_counter'])
            self.assertIn('zone.return.fixed_event_battlefield', node.capability_dependencies)

    def test_event_battlefield_intent_rejects_malformed_identity_and_entry_fields(self):
        from quorune.event_card_return import event_battlefield_return_effect, event_battlefield_return_intent
        effect = {**event_battlefield_return_effect(tapped=True, entry_counter='+1/+1'),
            'card':'DEPARTED', 'expected_zone_change_counter':2, 'controller':'B', 'source':'S1'}
        intent = event_battlefield_return_intent(effect, actor='A', reason='Closed witness')
        self.assertEqual('B', intent.new_controller)
        self.assertEqual(2, intent.expected_zone_change_counter)
        self.assertEqual('tapped', intent.tapped_policy)
        self.assertEqual(1, intent.effect_entry_counters[0].amount)
        self.assertEqual('B', intent.effect_entry_counters[0].placing_player)
        for change in ({'expected_zone_change_counter':True}, {'tapped':1}, {'entry_counter':'poison'}, {'unknown':True}, {'card':None}, {'_replacement_selections':'invalid'}):
            with self.assertRaises(ValueError):
                event_battlefield_return_intent({**effect, **change}, actor='A', reason='Malformed witness')
        from quorune.semantic_runtime import default_semantic_handler_registry, ReadOnlyHandlerContext
        from quorune.semantic_runtime.context import SemanticNodeError
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Closed handler witness', seats='ABCD', active_seats='ABCD', apnap_order='ABCD')
        plan = default_semantic_handler_registry().lower(effect, context)
        self.assertEqual(event_battlefield_return_intent(effect, actor='A', reason=context.default_reason), plan.intents[0])
        with self.assertRaises(SemanticNodeError):
            default_semantic_handler_registry().lower({**effect, 'controller':'UNKNOWN'}, context)

    def test_event_battlefield_dependencies_and_unrepresented_riders_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        registry = load_default_capability_registry()
        text = "When this creature dies, return it to the battlefield tapped under its owner's control with a +1/+1 counter on it."
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in ('zone.return.fixed_event_battlefield', 'counter.producer.effect_entry', 'zone.change.destination_replacement'):
            value = deepcopy(raw)
            row = next(c for c in value['capabilities'] if c['id'] == dependency)
            row.update(status='blocked', blockers=['Independent event battlefield dependency mutation'])
            altered = CapabilityRegistry(value)
            altered.mark_evidence_verified(registry.evidence_fingerprint)
            self.assertNotEqual('exact', compile_oracle_card(payment_record(text), capability_registry=altered).status)
        for body in (
            "return it to the battlefield tapped under target player's control.",
            "return it to the battlefield under its owner's control with X +1/+1 counters on it.",
            "return it to the battlefield under its owner's control and rewrite history.",
        ):
            self.assertNotEqual('exact', compile_oracle_card(payment_record('When this creature dies, ' + body), capability_registry=registry).status)

    def test_event_return_missing_dependency_and_incarnation_mutants_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        record = payment_record("When this creature dies, return it to its owner's hand.")
        for id in ('zone.return.fixed_event_card', 'trigger.event.normalized_zone_change', 'zone.change.destination_replacement'):
            value = deepcopy(raw); cap = next(r for r in value['capabilities'] if r['id'] == id)
            cap.update(status='blocked', blockers=['Independent event return mutation'])
            with self.subTest(id=id): self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(value)).status)
    def test_attached_counter_and_event_return_siblings_bind_all_runtime_dependencies(self):
        from dataclasses import replace
        from test_fixed_optional_mana_payment_triggers import payment_record
        record = replace(payment_record(
            "Enchant creature\nEnchanted creature has infect.\n"
            "At the beginning of your upkeep, put a -1/-1 counter on enchanted creature.\n"
            "When this Aura is put into a graveyard from the battlefield, return it to its owner's hand.",
            type_line="Enchantment — Aura",
        ), keywords=("Enchant",))
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        counter = next(node for node in ir.faces[0].nodes if any(effect.get("op") == "place_counters" for effect in node.effects))
        self.assertIn("counter.producer.fixed_attached_effect", counter.capability_dependencies)
        self.assertIn("counter.producer.fixed_effect", counter.capability_dependencies)
        from quorune.card_programs.adapters import compile_card_program
        from quorune.card_programs import bind_card_program_runtime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dependency-binding.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/event-card-return.json"], path)
            with CardDatabase(path) as db:
                program = compile_card_program(db, record, capability_registry=registry, capability_profile="commander_review", trust_level="trusted")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_event_return_with_attached_regeneration_closes_actual_handler_dependency(self):
        from dataclasses import replace
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.card_programs.adapters import compile_card_program
        from quorune.card_programs import bind_card_program_runtime
        record = replace(payment_record(
            "Enchant creature\nSacrifice a Forest: Regenerate enchanted creature.\n"
            "When this Aura is put into a graveyard from the battlefield, return it to its owner's hand.",
            type_line="Enchantment — Aura",
        ), keywords=("Enchant",))
        registry = load_default_capability_registry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regeneration-binding.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/event-card-return.json"], path)
            with CardDatabase(path) as db:
                program = compile_card_program(db, record, capability_registry=registry, capability_profile="commander_review", trust_level="trusted")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_source_less_probe_keeps_literal_self_return_closed(self):
        from quorune.compiler.event_card_return_templates import self_death_return_binding
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        text = "When this creature dies, return it to its owner's hand."
        self.assertIsNotNone(self_death_return_binding(text))
        self.assertIsNotNone(fixed_counter_trigger_binding(text))
        self.assertIsNone(self_death_return_binding("When Mortus Strider dies, return it to its owner's hand."))
        self.assertIsNone(self_death_return_binding("When this creature dies, return another card to its owner's hand."))

    def test_batch_measurement_probes_reach_the_registered_dispatcher(self):
        from scripts.work_selection_cohort_measurements import _matches_probe
        cases = (
            ("fixed-kicked-spell-condition-existing-owner-v1", "If this spell was kicked, draw a card."),
            ("fixed-event-card-return-existing-owner-v1", "When this creature dies, return it to its owner's hand."),
            ("fixed-target-announcement-existing-owner-v1", "Whenever this creature becomes the target of a spell, draw a card."),
            ("fixed-kicked-entry-result-existing-owner-v1", "When this creature enters, if it was kicked, draw a card."),
            ("fixed-counted-activation-zone-cost-existing-owner-v1", "Discard two cards: Draw a card."),
            ("batched-card-support-existing-owner-v1", "When this creature dies, return it to its owner's hand."),
        )
        for probe, text in cases:
            with self.subTest(probe=probe):
                self.assertTrue(_matches_probe(probe, text))
                self.assertFalse(_matches_probe(probe, "Draw a card."))

    def test_new_batch_probes_measure_whole_programs_through_registered_dispatch(self):
        from dataclasses import replace
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from scripts.work_selection_cohort_measurements import _measurement
        import json
        cases = (
            ("fixed-event-card-return-existing-owner-v1", "zone.return.fixed_event_card",
             payment_record("When this creature dies, return it to its owner's hand.")),
            ("fixed-kicked-spell-condition-existing-owner-v1", "resolution.effect.fixed_cast_fact",
             replace(payment_record("Kicker {1}{U}\nDraw two cards. If this spell was kicked, you gain 3 life.", type_line="Sorcery"), keywords=("Kicker",))),
        )
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        registry = load_default_capability_registry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe-binding.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/event-card-return.json"], path)
            with CardDatabase(path) as db:
                for probe, capability, record in cases:
                    value = deepcopy(raw)
                    row = next(r for r in value["capabilities"] if r["id"] == capability)
                    row.update(status="blocked", blockers=["Independent pre-support baseline"])
                    baseline_ir = compile_oracle_card(record, capability_registry=CapabilityRegistry(value), capability_profile="commander_review")
                    baseline = analyze_card_unlocks(baseline_ir, program=None, program_error=None, capabilities=registry, profile="commander_review")
                    measured = _measurement(frontier={"cards": [baseline]},
                        bundle={"bundle_id": "bundle:" + probe, "measurement_probe_id": probe},
                        cards_by_oracle_id={record.oracle_id: record},
                        coverage={"minimum_complete_card_gain": 50, "minimum_exact_ability_gain": 100, "minimum_material_residual_reduction": 100},
                        cohort_fingerprint="bounded-registered-probe", database=db)
                    with self.subTest(probe=probe):
                        self.assertEqual(1, measured["complete_card_gain"])
                        self.assertFalse(measured["grants_gameplay_trust"])

    def test_event_return_overlay_requires_identical_self_event_and_bound_sources(self):
        from dataclasses import replace
        from quorune.card_programs.reviewed_overlay import shadowed_reviewed_event_return_keys
        from quorune.semantics import SemanticProgram
        from test_fixed_optional_mana_payment_triggers import payment_record
        record = payment_record("When this artifact is put into a graveyard from the battlefield, return it to its owner's hand.", type_line="Artifact")
        common = dict(oracle_id=record.oracle_id, version=1, tests=("test_event_return_overlay_requires_identical_self_event_and_bound_sources",),
            active_zone="battlefield", trust_level="trusted", target_schema=None,
            provenance={"source_oracle_hash": "same-oracle", "source_rulings_hash": "same-rulings", "authored_by": "overlay-contract", "review_status": "reviewed"})
        old = SemanticProgram(key="legacy:return", label="Legacy return", ability_id="legacy:return", event="artifact.graveyard.self",
            effects=({"op": "move", "card": "$source", "destination": "hand", "reason": "Printed label"},), **common)
        new = SemanticProgram(key="typed:return", label="Typed return", ability_id="typed:return", event="permanent.graveyard.self",
            effects=(event_card_return_effect(),), capability_dependencies=("zone.return.fixed_event_card",),
            capability_closure=load_default_capability_registry().closure(("zone.return.fixed_event_card",), profile="commander_review").to_dict(), **common)
        self.assertEqual({old.key}, shadowed_reviewed_event_return_keys(record, (new,), (old,)))
        for mutant in (replace(new, trust_level="provisional"), replace(new, event="permanent.enter.self"),
                       replace(new, provenance={**new.provenance, "source_oracle_hash": "different"}),
                       replace(new, trust_level="provisional", capability_closure={**new.capability_closure, "trusted": False})):
            self.assertEqual(set(), shadowed_reviewed_event_return_keys(record, (mutant,), (old,)))
        other = replace(old, key="legacy:other", ability_id="legacy:other", effects=({"op": "draw", "player": "$controller", "count": 1},))
        self.assertEqual({old.key}, shadowed_reviewed_event_return_keys(record, (new,), (old, other)))

    def test_combined_batch_measurement_deduplicates_actual_whole_card_closure(self):
        from dataclasses import replace
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.rules.capabilities import CapabilityRegistry
        from scripts.work_selection_cohort_measurements import _measurement
        import json
        registry = load_default_capability_registry()
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for capability in ('trigger.source.fixed_maintenance', 'zone.return.fixed_event_card'):
            row = next(r for r in raw['capabilities'] if r['id'] == capability)
            row.update(status='blocked', blockers=['Independent batch baseline'])
        records = [replace(payment_record(text), oracle_id='fixture:batch:' + str(index)) for index, text in enumerate((
            "At the beginning of the end step, sacrifice this creature.\nWhen this creature dies, return it to its owner's hand.",
            "When this creature dies, return it to its owner's hand.\nWhenever a player sneezes, draw a card.",
        ))]
        baseline = [analyze_card_unlocks(compile_oracle_card(r, capability_registry=CapabilityRegistry(raw), capability_profile='commander_review'),
            program=None, program_error=None, capabilities=registry, profile='commander_review') for r in records]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'aggregate-binding.sqlite3'
            build_fixture_database([ROOT / 'tests/fixtures/event-card-return.json'], path)
            with CardDatabase(path) as db:
                measured = _measurement(frontier={'cards': baseline}, bundle={
                    'bundle_id': 'bundle:batched-card-support', 'measurement_probe_id': 'batched-card-support-existing-owner-v1'},
                    cards_by_oracle_id={r.oracle_id: r for r in records},
                    coverage={'minimum_complete_card_gain': 50, 'minimum_exact_ability_gain': 100, 'minimum_material_residual_reduction': 100},
                    cohort_fingerprint='independent-two-card-batch', database=db)
        self.assertEqual(1, measured['complete_card_gain'])
        self.assertGreaterEqual(measured['exact_ability_gain'], 2)
        self.assertFalse(measured['grants_gameplay_trust'])

    def test_departed_return_effect_requires_exact_event_card_and_counter(self):
        intent = event_card_return_intent({**event_card_return_effect(), 'card': 'EVENT-CARD', 'expected_zone_change_counter': 2}, actor='A', reason='Witness')
        self.assertEqual('EVENT-CARD', intent.object_ref)
        self.assertEqual(2, intent.expected_zone_change_counter)
        self.assertEqual(('graveyard',), intent.expected_zones)
        self.assertTrue(intent.optional_if_missing); self.assertFalse(intent.owned_only)
        for mutant in ({'card': None}, {'expected_zone_change_counter': True}, {'expected_zone_change_counter': 0}, {'unknown': True}):
            with self.subTest(mutant=mutant), self.assertRaises(ValueError):
                event_card_return_intent({**event_card_return_effect(), 'card': 'EVENT-CARD', 'expected_zone_change_counter': 2, **mutant}, actor='A', reason='Witness')

    def test_event_return_source_and_attached_grammar_preserve_unrelated_siblings(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for text in ("When this creature dies, return it to its owner's hand.", "When this artifact is put into a graveyard from the battlefield, return it to its owner's hand.", "When enchanted creature dies, return that card to its owner's hand."):
            ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
            node = ir.faces[0].nodes[0]
            self.assertEqual(event_card_return_effect(), dict(node.effects[0]))
            self.assertIn('zone.return.fixed_event_card', node.capability_dependencies)
        ir = compile_oracle_card(payment_record("When this creature dies, return it to its owner's hand.\nWhenever a player sneezes, draw a card."), capability_registry=registry)
        self.assertNotEqual('exact', ir.status)


class EventCardReturnActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'event-return.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json', ROOT / 'tests/fixtures/typed-grant-carrier-cards.json', ROOT / 'tests/fixtures/event-card-return.json', ROOT / 'tests/fixtures/scryfall-exact-lists.json', ROOT / 'tests/fixtures/damage-linked-graveyard-outcome-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Event return deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed, *, seats='ABCD'):
        config = GameConfig(seed=seed, auto_pass_empty_priority=False)
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in seats},
                                          first_player='A', seed=seed, config=config)
        keep_all(session); engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        names = {'Abduction', 'Atog', 'Aspect of Mongoose', 'Generic Swamp Return Aura', 'Spreading Algae', 'Spine of Ish Sah', 'Sol Ring', 'Mortus Strider', 'Demonic Vigor', "Squee's Embrace", "Altar's Reap", 'Naturalize', 'Murder', 'Generic Payment Commander', 'Generic Payment Plains'}
        records = tuple(record for record in self.db.iter_cards() if record.name in names and compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review').status == 'exact')
        register_generated_programs(self.db, engine.semantics, records, trust_level='trusted', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='battlefield', owner='A', controller=None, is_token=False):
        record = self.db.lookup(name); controller = controller or owner
        card = CardInstance(object_id='event-return:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=owner, controller=controller, zone=zone, is_token=is_token, zone_timestamp=engine._next_zone_timestamp(), known_to=[owner] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card; engine.state.players[controller if zone == 'battlefield' else owner].zones[zone].append(card.object_id)
        return card

    def ready(self, session, spell, mana):
        engine = session.engine; engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('A'); engine.pump()
        return next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + spell.ref)

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state); session.commands.clear(); session.decisions.clear()

    def resolve(self, session):
        for _ in range(24):
            if not session.state.stack: return
            if session.state.pending_decision and session.state.pending_decision.kind != 'priority': return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Event return did not finish')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'event-return-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_cauldron_controlled_death_moves_the_original_to_its_owner_bottom_and_replays(self):
        session = self.session(4000711)
        engine = session.engine
        cauldron = self.add(engine, 'The Cauldron of Eternity', ref='CAULDRON')
        record = self.db.by_oracle_id(cauldron.oracle_id)
        register_generated_programs(
            self.db, engine.semantics, (record,), trust_level='trusted',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        stolen = self.add(engine, 'Atog', ref='STOLEN', owner='B', controller='A')
        opponent_controlled = self.add(engine, 'Atog', ref='OPPONENT-CONTROLLED', owner='A', controller='B')
        copy_token = self.add(engine, 'Atog', ref='COPY-TOKEN', is_token=True)
        murder = self.add(engine, 'Murder', ref='MURDER', zone='hand')
        first_reap = self.add(engine, "Altar's Reap", ref='FIRST-REAP', zone='hand')
        second_reap = self.add(engine, "Altar's Reap", ref='SECOND-REAP', zone='hand')
        owner_library = list(session.state.players['B'].zones['library'])

        action = self.ready(session, murder, {'B': 4, 'C': 3})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [opponent_controlled.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual('graveyard', opponent_controlled.zone)
        self.assertIn(opponent_controlled.object_id, session.state.players['A'].zones['graveyard'])

        for spell, sacrificed in ((first_reap, stolen), (second_reap, copy_token)):
            while session.pending_principals()[0] != 'pilot:A':
                passed = session.act(session.pending_principals()[0], {'action_id': 'pass'})
                self.assertTrue(passed.ok, passed.summary)
            offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            action = next(row for row in offered if row['id'] == 'cast:' + spell.ref)
            result = session.act('pilot:A', {
                'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [sacrificed.ref],
            })
            self.assertTrue(result.ok, result.summary)
            trigger, = (item for item in session.state.stack if item.source_object_id == cauldron.object_id)
            self.assertEqual('A', trigger.controller)
            self.assertEqual('A', trigger.context['previous_controller'])
            self.assertEqual(sacrificed.owner, trigger.context['owner'])
            self.assertEqual(sacrificed.ref, trigger.context['card'])
            if sacrificed is stolen:
                self.assertEqual(sacrificed.zone_change_counter, trigger.context['card_zone_change_counter'])
                self.assertEqual('graveyard', sacrificed.zone)
            else:
                self.assertNotIn(copy_token.object_id, session.state.players['A'].zones['graveyard'])
            self.resolve(session)

        self.assertEqual('library', stolen.zone)
        self.assertEqual([stolen.object_id, *owner_library], session.state.players['B'].zones['library'])
        self.assertEqual('graveyard', opponent_controlled.zone)
        self.assertNotIn(copy_token.object_id, session.state.players['A'].zones['library'])
        self.assertNotEqual('battlefield', getattr(engine.state.cards.get(copy_token.object_id), 'zone', None))
        self.replay(session)

    def test_original_abduction_retains_attached_death_identity_after_aura_departure_and_replays(self):
        session = self.session(4000710)
        engine = session.engine
        aura = self.add(engine, 'Abduction', ref='ABDUCTION', zone='hand')
        body = self.add(engine, 'Atog', ref='ABDUCTED', owner='B')
        removal = self.add(engine, 'Murder', ref='ABDUCTION-MURDER', zone='hand')
        body.tapped = True
        action = self.ready(session, aura, {'U': 2, 'B': 2, 'C': 3})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield', aura.zone)
        self.assertEqual(body.object_id, aura.attached_to)
        self.assertFalse(body.tapped)
        self.assertEqual('A', body.controller)
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row.get('card') == removal.ref)
        old_identity = body.logical_object_id
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(16):
            if engine.state.cards[body.object_id].zone == 'graveyard':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('graveyard', engine.state.cards[body.object_id].zone)
        self.assertEqual('graveyard', engine.state.cards[aura.object_id].zone)
        self.assertTrue(any(item.kind == 'triggered_ability' for item in engine.state.stack))
        self.replay(session)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'abduction-departed-aura'
            session.save(path)
            session = CommanderSession.load(self.db, path)
        engine = session.engine
        self.resolve(session)
        current = engine.state.cards[body.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertEqual('B', current.controller)
        self.assertNotEqual(old_identity, current.logical_object_id)
        self.assertEqual('graveyard', engine.state.cards[aura.object_id].zone)
        self.assertIsNone(current.attached_to)
        self.replay(session)

    def test_actual_sacrifice_returns_exact_card_to_owner_hand_with_pre_action_replay(self):
        session = self.session(4000701); engine = session.engine
        source = self.add(engine, 'Mortus Strider', ref='STRIDER', owner='B', controller='A')
        other = self.add(engine, 'Mortus Strider', ref='SAME-NAME')
        spell = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        action = self.ready(session, spell, {'B': 1, 'C': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertIn(source.object_id, session.state.players['B'].zones['hand'])
        self.assertEqual('battlefield', session.state.cards[other.object_id].zone)
        self.replay(session)

    def test_stale_graveyard_incarnation_is_not_returned_by_old_trigger(self):
        session = self.session(4000702); engine = session.engine
        source = self.add(engine, 'Mortus Strider', ref='STRIDER')
        spell = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        action = self.ready(session, spell, {'B': 1, 'C': 1})
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        previous_counter = source.zone_change_counter
        engine.move_card(source.object_id, 'exile', reason='Independent old-trigger counterexample')
        engine.move_card(source.object_id, 'graveyard', reason='Independent new graveyard incarnation')
        self.assertGreater(source.zone_change_counter, previous_counter)
        self.checkpoint(session); self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone); self.replay(session)

    def test_attachment_lki_returns_dead_creature_after_aura_leaves(self):
        session = self.session(4000703); engine = session.engine
        creature = self.add(engine, 'Generic Payment Commander', ref='CREATURE', owner='B', controller='A')
        aura = self.add(engine, 'Demonic Vigor', ref='VIGOR')
        aura.attached_to = creature.object_id; creature.attachments.append(aura.object_id)
        spell = self.add(engine, 'Murder', ref='MURDER', zone='hand')
        action = self.ready(session, spell, {'B': 2, 'C': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [creature.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual('hand', session.state.cards[creature.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        self.assertIn(creature.object_id, session.state.players['B'].zones['hand'])
        self.replay(session)

    def test_original_typed_aura_return_closes_tap_sibling_and_unsupported_mana_events_reject(self):
        from dataclasses import replace
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.semantics import SemanticRegistry
        from high_risk_interaction_support import _observed_piece_ids
        record = self.db.lookup('Spreading Algae')
        program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        capabilities = set(program.trust_closure['capability_dependencies'])
        self.assertIn('attachment.aura.typed_restriction', capabilities)
        self.assertIn('zone.return.fixed_event_card', capabilities)
        self.assertFalse(program.residuals)
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        boundary = replace(record, oracle_id='fixture:unknown-mana-tap-return-aura',
            name='Constructed Unknown Mana Tap Return Aura',
            oracle_text=record.oracle_text.replace('becomes tapped,', 'becomes tapped for black mana,'))
        ir = compile_oracle_card(boundary, capability_registry=self.registry, capability_profile='commander_review')
        program = compile_best_available_card_program(self.db, boundary, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        row = analyze_card_unlocks(ir, program=program, program_error=None,
            capabilities=self.registry, profile='commander_review')
        self.assertLessEqual({'capability.zone.return.fixed_event_card',
            'residual.event_binding.normalized-event-binding'}, _observed_piece_ids(row))
        self.assertTrue(program.residuals)
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict_capability_ready'])
        self.assertIn('trust_basis:unresolved', binding['blockers'])

    def test_typed_aura_cast_rejects_non_swamp_and_returns_after_destruction_with_replay(self):
        session = self.session(4000705); engine = session.engine
        swamp = self.add(engine, 'Swamp', ref='SWAMP')
        plains = self.add(engine, 'Generic Payment Plains', ref='PLAINS')
        aura = self.add(engine, 'Generic Swamp Return Aura', ref='ALGAE', zone='hand')
        destroy = self.add(engine, 'Naturalize', ref='NATURALIZE', zone='hand')
        action = self.ready(session, aura, {'G': 2, 'C': 1})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        wrong = session.act('pilot:A', {'action_id': action['id'], 'targets': [plains.ref], 'pay': 'auto'})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [swamp.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
        self.assertEqual(swamp.object_id, session.state.cards[aura.object_id].attached_to)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + destroy.ref)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [aura.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
        self.assertEqual('hand', session.state.cards[aura.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[swamp.object_id].zone)
        self.replay(session)

    def test_shroud_rejects_targeted_removal_but_cost_sacrifice_returns_aura_with_replay(self):
        session = self.session(4000706); engine = session.engine
        creature = self.add(engine, 'Generic Payment Commander', ref='SHROUDED')
        aura = self.add(engine, 'Aspect of Mongoose', ref='MONGOOSE', owner='B', controller='A')
        aura.attached_to = creature.object_id; creature.attachments.append(aura.object_id)
        murder = self.add(engine, 'Murder', ref='MURDER', zone='hand')
        reap = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        self.add(engine, 'Generic Payment Commander', ref='LEGAL-OTHER', owner='C')
        action = self.ready(session, murder, {'B': 3, 'C': 2})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        wrong = session.act('pilot:A', {'action_id': action['id'], 'targets': [creature.ref], 'pay': 'auto'})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'] == 'cast:' + reap.ref)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'sacrifice_cards': [creature.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[creature.object_id].zone)
        self.assertEqual('hand', session.state.cards[aura.object_id].zone)
        self.assertIn(aura.object_id, session.state.players['B'].zones['hand'])
        self.replay(session)

    def test_actual_spine_targeted_entry_and_sacrifice_queue_one_return_with_replay(self):
        session = self.session(4000707); engine = session.engine
        spine = self.add(engine, 'Spine of Ish Sah', ref='SPINE', zone='hand', owner='A')
        target = self.add(engine, 'Sol Ring', ref='TARGET', owner='B')
        atog = self.add(engine, 'Atog', ref='ATOG')
        action = self.ready(session, spine, {'C': 7})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'].startswith('activate:' + atog.ref))
        accepted = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [spine.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertNotEqual('trigger.order', session.state.pending_decision.kind)
        self.assertEqual(1, sum('graveyard' in item.label or "return it to its owner's hand" in item.label for item in session.state.stack))
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[spine.object_id].zone)
        self.replay(session)

    def test_return_incarnation_omission_mutant_is_killed(self):
        from dataclasses import replace
        from quorune import event_card_return as owner
        original = owner.event_card_return_intent
        def omit(effect, **kwargs):
            return replace(original(effect, **kwargs), expected_zone_change_counter=None)
        with mock.patch.object(owner, 'event_card_return_intent', omit):
            with self.assertRaises(AssertionError):
                self.test_stale_graveyard_incarnation_is_not_returned_by_old_trigger()

    def _nim_return_fixture(self, seed, *, seats='ABCD'):
        session = self.session(seed, seats=seats)
        engine = session.engine
        records = tuple(self.db.lookup(name) for name in ('Nim Deathmantle', 'Cremate'))
        register_generated_programs(
            self.db, engine.semantics, records, trust_level='trusted',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_capability_declarations=True,
        )
        equipment = self.add(engine, 'Nim Deathmantle', ref='DEATHMANTLE')
        creature = self.add(engine, 'Atog', ref='ORIGINAL', owner='A', controller='B')
        return session, equipment, creature

    def _cast_in_fresh_priority(self, session, seat, spell, target):
        principal = 'pilot:' + seat
        while session.pending_principals()[0] != principal:
            passed = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(passed.ok, passed.summary)
        actions = session.packet(principal, full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'] == 'cast:' + spell.ref)
        result = session.act(principal, {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)

    def _pass_until_card_zone(self, session, card, zone):
        for _ in range(16):
            if card.zone == zone:
                return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('The card did not reach its expected zone')

    def _accept_nim_payment(self, session):
        self.resolve(session)
        self.assertEqual('semantic.choice', session.state.pending_decision.kind)
        self.assertEqual(['pilot:A'], list(session.pending_principals()))
        player = session.state.players['A']
        before = sum(player.mana_pool.values())
        accepted = session.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(before - 4, sum(player.mana_pool.values()))

    def test_nim_paid_return_requires_attachment_authority_and_exact_compiler_result(self):
        import json
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        record = self.db.lookup("Nim Deathmantle")
        profiles = ("traditional", "commander_duel", "commander_review")
        for profile in profiles:
            self.assertEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=profile,
            ).status)
        with mock.patch("quorune.compiler.event_card_return_templates._paid_death_equipment_return", return_value=None):
            for profile in profiles:
                missing_result = compile_oracle_card(
                    record, capability_registry=self.registry, capability_profile=profile,
                )
                self.assertNotEqual("exact", missing_result.status)

        path = ROOT / "quorune/rules/capability-registry.json"
        raw = json.loads(path.read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for capability in ("zone.return.fixed_paid_death_equipment", "attachment.action.fixed_source"):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                row = next(item for item in changed["capabilities"] if item["id"] == capability)
                row.update(status="blocked", blockers=["Independently withdrawn Equipment return authority"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations,
                                    discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                for profile in profiles:
                    declined = compile_oracle_card(record, capability_registry=registry, capability_profile=profile)
                    self.assertNotEqual("exact", declined.status)

    def test_nim_death_payment_observes_ownership_and_attaches_to_the_returned_card(self):
        from quorune.card_programs.adapters import compile_card_program
        from quorune.card_programs.binding import bind_card_program_runtime

        record = self.db.lookup("Nim Deathmantle")
        plans = {}
        for profile in ('traditional', 'commander_duel', 'commander_review'):
            program = compile_card_program(
                self.db, record, capability_registry=self.registry,
                capability_profile=profile, trust_level="trusted",
            )
            binding = bind_card_program_runtime(program, capability_registry=self.registry, profile=profile)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
            trigger, = (ability for ability in program.abilities if ability.event == "creature.dies")
            plans[profile] = (trigger.event_condition, trigger.effects, trigger.capability_dependencies)
        self.assertEqual(plans["commander_review"], plans["traditional"])
        self.assertEqual(plans["commander_review"], plans["commander_duel"])
        for seats in ('AB', 'ABCD'):
            with self.subTest(seats=seats):
                self._assert_nim_ownership_return(seats)

    def _assert_nim_ownership_return(self, seats):
        session, equipment, creature = self._nim_return_fixture(4000712, seats=seats)
        engine = session.engine
        borrowed = self.add(engine, 'Atog', ref='BORROWED', owner='B', controller='A')
        token = self.add(engine, 'Atog', ref='TOKEN')
        token.is_token, token.object_kind = True, 'token'
        removals = [self.add(engine, 'Murder', ref=f'MURDER-{index}', zone='hand') for index in range(3)]
        self.ready(session, removals[0], {'B': 6, 'C': 7})
        self.checkpoint(session)

        for spell, target in zip(removals[:2], (borrowed, token), strict=True):
            self._cast_in_fresh_priority(session, 'A', spell, target)
            self.resolve(session)
            self.assertFalse(session.state.stack)
            self.assertEqual('priority', session.state.pending_decision.kind)
        self.assertIn(borrowed.object_id, session.state.players['B'].zones['graveyard'])

        self._cast_in_fresh_priority(session, 'A', removals[2], creature)
        self._pass_until_card_zone(session, creature, 'graveyard')
        trigger, = (item for item in session.state.stack if item.source_object_id == equipment.object_id)
        self.assertEqual('A', trigger.controller)
        self.assertEqual('B', trigger.context['previous_controller'])
        self.assertEqual('A', trigger.context['owner'])
        self.assertEqual(creature.zone_change_counter, trigger.context['death_equipment_arrivals'][0]['zone_change_counter'])
        self._accept_nim_payment(session)

        self.assertEqual(('battlefield', 'A'), (creature.zone, creature.controller))
        self.assertEqual(creature.object_id, equipment.attached_to)
        self.assertIn(equipment.object_id, creature.attachments)
        data = engine._effective_card_data(creature)
        self.assertIn('Zombie', data['type_line'])
        self.assertEqual(3, engine._numeric_stat(creature.object_id, 'power'))
        self.replay(session)

    def test_nim_can_pay_after_equipment_departure_or_the_original_arrival_becomes_stale(self):
        for index, (name, response_target, zone) in enumerate((
            ('Naturalize', 'equipment', 'graveyard'), ('Cremate', 'creature', 'exile'),
        )):
            with self.subTest(response=name):
                session, equipment, creature = self._nim_return_fixture(4000713 + index)
                engine = session.engine
                murder = self.add(engine, 'Murder', ref='MURDER', zone='hand')
                response = self.add(engine, name, ref='RESPONSE', zone='hand', owner='B')
                engine.state.players['B'].mana_pool.update({'B': 1, 'G': 1, 'C': 1})
                self.ready(session, murder, {'B': 2, 'C': 5})
                self.checkpoint(session)
                self._cast_in_fresh_priority(session, 'A', murder, creature)
                self._pass_until_card_zone(session, creature, 'graveyard')
                target = equipment if response_target == 'equipment' else creature
                self._cast_in_fresh_priority(session, 'B', response, target)
                self._pass_until_card_zone(session, target, zone)
                self._accept_nim_payment(session)
                self.assertEqual('battlefield' if name == 'Naturalize' else 'exile', creature.zone)
                self.assertIsNone(equipment.attached_to)
                self.replay(session)

    def test_commander_command_choice_removes_card_before_old_return_trigger(self):
        session = self.session(4000704); engine = session.engine
        source = self.add(engine, 'Mortus Strider', ref='COMMANDER')
        source.is_commander = True
        source.commander_designation_id = 'event-return-commander-physical'
        spell = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        action = self.ready(session, spell, {'B': 1, 'C': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn('commander', session.state.pending_decision.kind)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-commander-return'; session.save(path)
            session = CommanderSession.load(self.db, path)
        result = session.act('pilot:A', {'action_id': 'command'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual('command', session.state.cards[source.object_id].zone)
        self.replay(session)
