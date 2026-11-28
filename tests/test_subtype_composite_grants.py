"""Pinned CR205.3d and 613.6: subtype additions coupled to exact quote children."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_restricted_mana_grants as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.subtype_composite_grants import (
    FixedSubtypeCompositeGrantHandler, SUBTYPE_COMPOSITE_GRANT_CAPABILITY as CAPABILITY,
)
from scripts.build_test_database import build_fixture_database


DISMISS = 'Each creature your opponents control is an Illusion in addition to its other types and has "When this creature becomes the target of a spell or ability, sacrifice it."'
PEACOCK = 'Artifacts you control are Clues in addition to their other types and have "{2}, Sacrifice this artifact: Draw a card."'


class SubtypeCompositeGrantCompilerTests(unittest.TestCase):
    def test_correlated_additions_use_one_versioned_layer_plan_and_keep_old_versions_closed(self):
        from quorune.continuous_effect_model import Layer
        from quorune.semantic_runtime.composite_grants import CompositeAbilityGrantHandler, FixedTypeReplacementGrantHandler
        registry = load_default_capability_registry()
        for text, subtype in ((DISMISS, 'Illusion'), (PEACOCK, 'Clue')):
            ir = compile_oracle_card(query_record(text), capability_registry=registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            parent, child = ir.faces[0].nodes
            self.assertIn(CAPABILITY, parent.capability_dependencies)
            self.assertEqual(text, text[parent.span.start:parent.span.end])
            descriptor = parent.handlers[0]
            self.assertEqual([subtype], descriptor['modifier']['type_operations'][0]['values'])
            context = SimpleNamespace(source_object_id='giver', source_ref='giver-ref', source_controller='A',
                source_timestamp=7, component_id='component')
            effects = FixedSubtypeCompositeGrantHandler().lower(descriptor, context)
            self.assertEqual((Layer.TYPE, Layer.ABILITY), tuple(effect.layer for effect in effects))
            self.assertEqual({7}, {effect.timestamp for effect in effects})
            self.assertEqual('add_ability_fragment', effects[-1].operations[0].op)
            for old in (CompositeAbilityGrantHandler(), FixedTypeReplacementGrantHandler()):
                changed = deepcopy(descriptor); changed.update(handler_id=old.handler_id, schema_version=old.schema_version)
                with self.assertRaises(ValueError): old.validate(changed)
            for mutate in (
                lambda v: v.update(schema_version=True),
                lambda v: v.update(extra=None),
                lambda v: v['modifier'].update(power=1),
                lambda v: v['modifier'].update(remove_all_abilities=True),
                lambda v: v['modifier'].update(add_ability_fragments=[]),
                lambda v: v['modifier']['type_operations'][0].update(op='set_types'),
                lambda v: v['modifier']['type_operations'][0].update(values=['Clue' if subtype == 'Illusion' else 'Illusion']),
                lambda v: v['target']['predicate'].update(include_phased_out=True),
                lambda v: v['target']['predicate'].update(excluded_subtypes=['illusion']),
                lambda v: v['target'].update(controller='A'),
            ):
                changed = deepcopy(descriptor); mutate(changed)
                with self.assertRaises(ValueError): FixedSubtypeCompositeGrantHandler().validate(changed)

    def test_unknown_children_unmatched_types_and_trailing_text_remain_material(self):
        registry = load_default_capability_registry()
        for text in (DISMISS.replace('Illusion', 'Clue'), PEACOCK.replace('Artifacts', 'Creatures'),
                PEACOCK.replace('Clues', 'chosen types'), PEACOCK.replace('Draw a card', 'Rewrite history'),
                PEACOCK + ' Draw a card.', DISMISS.replace('other types', 'other colors'),
                PEACOCK.replace('other types', 'other creature types')):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status, text)

    def test_verified_composite_and_child_withdrawal_fail_closed_with_flying_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for owner in (CAPABILITY, 'continuous.ability.fixed_composite_grant', 'continuous.ability.fixed_query_grant'):
            value = deepcopy(raw)
            next(r for r in value['capabilities'] if r['id'] == owner).update(status='blocked', blockers=['subtype grant owner withdrawn'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual('exact', compile_oracle_card(query_record(PEACOCK), capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(query_record('Flying'), capability_registry=registry).status)

    def test_omitted_subtype_composition_mutant_is_killed(self):
        registry = load_default_capability_registry()
        def witness():
            self.assertEqual('exact', compile_oracle_card(query_record(PEACOCK), capability_registry=registry).status)
        witness()
        with patch('quorune.compiler.composite_grant_nodes.subtype_composite_shell', return_value=None), self.assertRaises(AssertionError):
            witness()


class SubtypeCompositeGrantNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.RestrictedManaGrantNativeTests.session
    add = native.RestrictedManaGrantNativeTests.add
    prepare = native.RestrictedManaGrantNativeTests.prepare
    cast = native.RestrictedManaGrantNativeTests.cast
    cast_for = native.RestrictedManaGrantNativeTests.cast_for
    checkpoint = native.RestrictedManaGrantNativeTests.checkpoint
    resolve = native.RestrictedManaGrantNativeTests.resolve
    replay = native.RestrictedManaGrantNativeTests.replay
    assert_closed = native.RestrictedManaGrantNativeTests.assert_closed
    priority = native.RestrictedManaGrantNativeTests.priority
    action = native.RestrictedManaGrantNativeTests.action
    granted_action = native.RestrictedManaGrantNativeTests.granted_action

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'subtype-composite.sqlite3'
        extra = {"schema_version": 1, "cards": [], "rulings": []}
        for filename, names in (
            ("external-entry-tapped-cards.json", {"Root Maze"}),
            ("conditional-copy-value-cards.json", {"Clone"}),
            ("ward-sacrifice-cards.json", {"Trial Cooked Meal", "Trial Ward Mark"}),
        ):
            source = json.loads((ROOT / "tests/fixtures" / filename).read_text(encoding="utf-8"))
            rows = [row for row in source["cards"] if row["name"] in names]
            assert {row["name"] for row in rows} == names
            extra["cards"].extend(rows)
            oracle_ids = {row["oracle_id"] for row in rows}
            extra["rulings"].extend(row for row in source.get("rulings", ()) if row["oracle_id"] in oracle_ids)
        supplement = path.with_name("food-grant-support.json")
        supplement.write_text(json.dumps(extra), encoding="utf-8")
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/subtype-composite-grant-cards.json', supplement], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Subtype grants', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def parts(self, session, card):
        return tuple(set(part) for part in session.engine._type_parts(session.engine._effective_card_data(session.state.cards[card.object_id])['type_line']))

    def grants(self, session, card):
        return [a for a in session.engine._activated_abilities(session.state.cards[card.object_id]) if a.ability_id.startswith('ability:granted:')]


    def test_ygra_other_creature_food_drives_entry_and_granted_payment(self):
        session = self.session(205628098)
        engine = session.engine
        source = self.add(engine, "Ygra, Eater of All", zone="hand")
        root_maze = self.add(engine, "Root Maze", seat="B")
        old = self.add(engine, "Grizzly Bears", ref="FOOD-OLD")
        entrant = self.add(engine, "Grizzly Bears", zone="hand", ref="FOOD-ENTRANT")
        opposing = self.add(engine, "Memnite", seat="C", ref="FOOD-OPPOSING")
        opposing.acquired_control_turn_count = -1
        other_artifact = self.add(engine, "Sol Ring", seat="B")
        private = self.add(engine, "Twiddle", seat="D", zone="hand", ref="FOOD-PRIVATE")
        self.assert_closed(session, source)
        self.assert_closed(session, root_maze)
        self.prepare(session, mana={"B": 1, "G": 2, "C": 4})
        session.state.players["C"].mana_pool.update({"C": 2})
        self.checkpoint(session)

        self.cast_for(session, "A", source, reserve={"G": 1, "C": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"creature"}, self.parts(session, source)[0])
        self.assertNotIn("food", self.parts(session, source)[1])
        self.assertFalse(self.grants(session, source))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual(({"artifact", "creature"}, {"bear", "food"}), self.parts(session, old)[:2])
        self.assertEqual(({"artifact", "creature"}, {"construct", "food"}), self.parts(session, opposing)[:2])
        ability, = self.grants(session, opposing)
        self.assertTrue(ability.tap_source and ability.sacrifice_source)
        self.assertFalse(self.grants(session, other_artifact))
        self.assertNotIn("food", self.parts(session, other_artifact)[1])

        self.cast(session, "A", entrant)
        self.assertEqual({"creature"}, self.parts(session, entrant)[0])
        self.assertFalse(self.grants(session, entrant))
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[entrant.object_id].tapped)
        self.assertEqual(({"artifact", "creature"}, {"bear", "food"}), self.parts(session, entrant)[:2])

        self.priority(session, "C")
        offer = self.granted_action(session, "C", opposing)
        before = authoritative_state_hash(session.state)
        command = {"action_id": offer["id"], "pay": "auto"}
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        initial_life = session.state.players["C"].life
        paid = session.act("pilot:C", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", session.state.cards[opposing.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_life + 3, session.state.players["C"].life)
        self.assertEqual(0, session.state.players["C"].mana_pool["C"])
        self.assertEqual(2, session.state.cards[source.object_id].counters["+1/+1"])
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        self.replay(session, load=True)

    def test_ygra_food_type_is_present_after_an_entry_copy_replacement(self):
        session = self.session(205628099)
        engine = session.engine
        source = self.add(engine, "Ygra, Eater of All", seat="B")
        root_maze = self.add(engine, "Root Maze", seat="B")
        model = self.add(engine, "Grizzly Bears", ref="FOOD-COPY-MODEL", seat="C")
        clone = self.add(engine, "Clone", zone="hand", ref="FOOD-COPY")
        for card in (source, root_maze, clone):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "U": 1})
        self.checkpoint(session)

        self.cast(session, "A", clone)
        decision = self.resolve(session)
        self.assertEqual("replacement.order", decision.kind)
        session = self.replay(session, load=True)
        choices = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copy_option, = (row for row in choices if model.ref in row["id"] and not row["id"].startswith("decline:"))
        accepted = session.act("pilot:A", {"action_id": "choose", "replacement": copy_option["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        entered = session.state.cards[clone.object_id]
        self.assertEqual("battlefield", entered.zone)
        self.assertTrue(entered.tapped)
        self.assertEqual(({"artifact", "creature"}, {"bear", "food"}), self.parts(session, entered)[:2])
        self.replay(session, load=True)

    def test_ygra_food_ward_and_current_last_known_food_track_both_artifact_kinds(self):
        for converted in (False, True):
            with self.subTest(converted=converted):
                session = self.session(205628100 + int(converted))
                engine = session.engine
                source = self.add(engine, "Ygra, Eater of All", seat="B", ref="FOOD-WARD-YGRA")
                creature = self.add(engine, "Grizzly Bears", ref="FOOD-WARD-CREATURE")
                food = self.add(engine, "Trial Cooked Meal", ref="FOOD-WARD-ARTIFACT")
                enemy = self.add(engine, "Grizzly Bears", seat="C", ref="FOOD-WARD-ENEMY")
                spell = self.add(engine, "Trial Ward Mark", zone="hand", ref="FOOD-WARD-SPELL")
                self.assert_closed(session, source)
                self.prepare(session, mana={"G": 1})
                self.checkpoint(session)

                self.cast(session, "A", spell, [source.ref])
                self.assertIsNotNone(self.resolve(session))
                pending = session.packet("pilot:A", full=True)["decision"]
                self.assertEqual("sacrifice", pending["ctx"]["payment_kind"])
                offer, = (row for row in pending["ctx"]["legal_actions"] if row["id"] == "choose")
                self.assertEqual({creature.ref, food.ref}, set(offer["choice_schema"]["legal_refs"]))
                before = authoritative_state_hash(session.state)
                bad = session.act("pilot:A", {"action_id": "choose", "cards": [enemy.ref]})
                self.assertFalse(bad.ok, bad.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                payment = creature if converted else food
                accepted = session.act("pilot:A", {"action_id": "choose", "cards": [payment.ref]})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual("graveyard", session.state.cards[payment.object_id].zone)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(2, session.state.cards[source.object_id].counters["+1/+1"])
                self.assertEqual(11, session.engine._numeric_stat(source.object_id, "power"))
                self.replay(session, load=True)

    def test_ygra_requires_the_type_entry_scope_and_the_exact_granted_cost(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantic_runtime.subtype_composite_grants import OTHER_CREATURES_FOOD_GRANT_CAPABILITY

        record = self.db.lookup("Ygra, Eater of All")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (node for node in ir.faces[0].nodes if node.event == "characteristics.evaluate"
                 and OTHER_CREATURES_FOOD_GRANT_CAPABILITY in node.capability_dependencies)
        descriptor = node.handlers[0]
        for mutation in (
            lambda data: data["target"].update(exclude_source=False),
            lambda data: data["target"].update(controller="source_controller"),
            lambda data: data["modifier"]["type_operations"].pop(0),
            lambda data: data["modifier"]["add_ability_fragments"][0]["value"].update(sacrifice_source=False),
        ):
            changed = deepcopy(descriptor)
            mutation(changed)
            with self.assertRaises(ValueError):
                FixedSubtypeCompositeGrantHandler().validate(changed)
        for text in (
            record.oracle_text.replace("Other creatures", "Creatures"),
            record.oracle_text.replace("Food artifacts", "Treasure artifacts"),
            record.oracle_text.replace("gain 3 life", "gain 2 life"),
        ):
            with self.subTest(text=text):
                candidate = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                                capability_profile="commander_review")
                self.assertNotEqual("exact", candidate.status)
        game = self.session(205628102)
        source = self.add(game.engine, "Ygra, Eater of All")
        self.assert_closed(game, source)
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if program.event == "characteristics.evaluate")
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != OTHER_CREATURES_FOOD_GRANT_CAPABILITY)
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + OTHER_CREATURES_FOOD_GRANT_CAPABILITY, result["blockers"])

        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for cap in (OTHER_CREATURES_FOOD_GRANT_CAPABILITY, CAPABILITY, "continuous.ability.fixed_query_grant"):
            with self.subTest(capability=cap):
                blocked = deepcopy(raw)
                authority = next(row for row in blocked["capabilities"] if row["id"] == cap)
                authority.update(status="blocked", blockers=["Required Food creature graph authority withdrawn"])
                candidate = compile_oracle_card(record, capability_registry=CapabilityRegistry(blocked),
                                                capability_profile="commander_review")
                self.assertNotEqual("exact", candidate.status)

    def test_actual_dismiss_binds_trigger_to_receiver_and_pending_trigger_survives_giver_departure(self):
        session = self.session(205628001)
        source = self.add(session.engine, 'Dismiss into Dream', zone='hand')
        own = self.add(session.engine, 'Grizzly Bears'); body = self.add(session.engine, 'Grizzly Bears', seat='B', ref='illusion-receiver')
        spell = self.add(session.engine, 'Twiddle', seat='C', zone='hand')
        removal = self.add(session.engine, 'Disenchant', zone='hand')
        private = self.add(session.engine, 'Twiddle', seat='D', zone='hand', ref='illusion-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C':7, 'U':1, 'W':1}); session.state.players['C'].mana_pool['U'] = 1; self.checkpoint(session)
        self.cast_for(session, 'A', source, reserve={'C':1, 'W':1}); self.assertIsNone(self.resolve(session))
        self.assertIn('illusion', self.parts(session, body)[1]); self.assertIn('bear', self.parts(session, body)[1])
        self.assertNotIn('illusion', self.parts(session, own)[1])
        self.cast(session, 'C', spell, [body.ref])
        self.assertTrue(any(s.kind == 'triggered_ability' and s.controller == 'B' for s in session.state.stack))
        self.cast(session, 'A', removal, [source.ref])
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[body.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[own.object_id].zone)
        for seat in 'ABC': self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.replay(session, load=True)

    def test_actual_peacock_nontoken_clue_sacrifice_uses_effective_subtype_and_private_draw(self):
        session = self.session(205628010)
        source = self.add(session.engine, 'Senator Peacock', zone='hand')
        body = self.add(session.engine, 'Memnite'); equipment = self.add(session.engine, 'Short Sword')
        opponent = self.add(session.engine, 'Sol Ring', seat='B')
        removal = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B')
        self.assert_closed(session, source); self.prepare(session, mana={'C':5, 'U':2}); self.checkpoint(session)
        self.cast_for(session, 'A', source, reserve={'C':2}); self.assertIsNone(self.resolve(session))
        self.assertTrue({'artifact', 'creature'} <= self.parts(session, body)[0])
        self.assertTrue({'construct', 'clue'} <= self.parts(session, body)[1])
        self.assertTrue({'equipment', 'clue'} <= self.parts(session, equipment)[1])
        self.assertNotIn('clue', self.parts(session, opponent)[1]); self.assertFalse(self.grants(session, opponent))
        self.assertFalse(session.state.cards[body.object_id].is_token)
        self.priority(session, 'A'); offered = self.granted_action(session, 'A', body)
        before = authoritative_state_hash(session.state); hand = len(session.state.players['A'].zones['hand'])
        command = {'action_id':offered['id'], 'pay':'auto'}
        self.assertFalse(session.act('pilot:B', command).ok); self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[body.object_id].zone)
        pending = self.resolve(session)
        self.assertIsNotNone(pending, 'A nontoken artifact was a Clue immediately before sacrifice')
        result = session.act('pilot:A', {'action_id':'choose', 'targets':[source.ref]}); self.assertTrue(result.ok, result.summary)
        self.cast(session, 'B', removal, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        # The bounced giver and the independently resolving draw both enter this hand.
        self.assertEqual(hand+2, len(session.state.players['A'].zones['hand']))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertNotIn('clue', self.parts(session, equipment)[1]); self.assertFalse(self.grants(session, equipment))
        drawn = session.state.cards[session.state.players['A'].zones['hand'][-1]].ref
        for seat in 'BCD': self.assertNotIn(drawn, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.replay(session, load=True)

    def test_actual_earlier_and_later_humility_preserve_started_type_effect_and_order_receiver_loss(self):
        for early in (True, False):
            session = self.session(205628030 + int(early))
            source = self.add(session.engine, 'Senator Peacock', zone='hand')
            humility = self.add(session.engine, 'Humility', zone='battlefield' if early else 'hand')
            body = self.add(session.engine, 'Memnite'); token = self.add(session.engine, 'Memnite', ref='token-artifact')
            token.is_token = True; token.object_kind = 'token'
            ring = self.add(session.engine, 'Sol Ring')
            self.prepare(session, mana={'C':7, 'U':2, 'W':2}); self.checkpoint(session)
            self.cast_for(session, 'A', source, reserve={} if early else {'C':2, 'W':2}); self.assertIsNone(self.resolve(session))
            if not early:
                self.cast(session, 'A', humility); self.assertIsNone(self.resolve(session))
            self.assertEqual(('1', '1'), tuple(session.engine._effective_card_data(session.state.cards[source.object_id])[k] for k in ('power', 'toughness')))
            for receiver in (body, token):
                self.assertTrue({'construct', 'clue'} <= self.parts(session, receiver)[1])
                self.assertEqual(early, bool(self.grants(session, receiver)))
            self.assertTrue(session.state.cards[token.object_id].is_token)
            self.assertIn('clue', self.parts(session, ring)[1]); self.assertTrue(self.grants(session, ring))
            self.replay(session, load=True)

    def test_runtime_subtype_composite_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(205628020); source = self.add(session.engine, 'Senator Peacock'); checked = 0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies: continue
            checked += 1; deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
            changed = replace(program, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict']); self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])
        self.assertGreater(checked, 0)
