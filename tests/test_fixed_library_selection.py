from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import ROOT, keep_all, load_assets, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardRecord
from quorune.compiler.fixed_library_selection_templates import (
    FIXED_LIBRARY_SELECTION_CAPABILITY,
    FIXED_LIBRARY_SELECTION_MECHANIC,
    fixed_library_selection_effect_template,
)
from quorune.compiler.program_generation import register_generated_programs
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.library_selection_capability_shapes import (
    fixed_library_selection_node_capabilities,
)
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_witnesses
from quorune.semantic_runtime import LibrarySelectionIntent
from quorune.semantics import SemanticProgram


REGISTRY_PATH = (
    Path(__file__).parents[1]
    / "quorune"
    / "rules"
    / "capability-registry.json"
)


def base_record() -> CardRecord:
    return CardRecord(
        oracle_id="fixture:fixed-library-selection",
        name="Fixed Library Selection",
        mana_cost="{2}{U}",
        mana_value=3.0,
        type_line="Sorcery",
        oracle_text=(
            "Look at the top three cards of your library. Put one of them "
            "into your hand and the rest into your graveyard."
        ),
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def nested_effects(value):
    found = []
    if isinstance(value, dict):
        if value.get("op"):
            found.append(value)
        for child in value.values():
            found.extend(nested_effects(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(nested_effects(child))
    return found


class LibrarySelectionPredicateNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    add = bound_witnesses.BoundEffectProgramRuntimeTests.add
    ready = bound_witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = bound_witnesses.BoundEffectProgramRuntimeTests.replay
    session = bound_witnesses.BoundEffectProgramRuntimeTests.session

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'library-predicate.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/library-selection-predicate-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Library predicate witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 10)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_relentless_pursuit_offers_and_commits_both_independent_slots(self):
        session = self.session(3640701)
        engine = session.engine
        for oid in tuple(session.state.players['A'].zones['library']):
            engine.move_card(oid, 'outside', log=False)
        creature = self.add(engine, 'Generic Bound Body', zone='library', ref='slot-creature')
        land = self.add(engine, 'Generic Bound Plains', zone='library', ref='slot-land')
        other_creature = self.add(engine, 'Generic Bound Body', zone='library', ref='slot-other-creature')
        instant = self.add(engine, 'Generic Bound Growth', zone='library', ref='slot-instant')
        spell = self.add(engine, 'Relentless Pursuit', zone='hand', ref='original-slot-pursuit')
        program = compile_best_available_card_program(self.db, self.db.lookup(spell.printed_name),
            semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        action = self.ready(session, spell, {'G': 3, 'C': 3})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.replay(session, load=True)
        before = session.state.to_dict()
        for principal, selected, remainder in (
            ('pilot:B', [creature.ref, land.ref], [other_creature.ref, instant.ref]),
            ('pilot:A', [creature.ref, other_creature.ref], [land.ref, instant.ref]),
            ('pilot:A', [creature.ref, creature.ref], [land.ref, other_creature.ref, instant.ref]),
        ):
            self.assertFalse(session.act(principal, {'action_id': 'choose',
                'cards': {'hand': selected, 'graveyard': remainder}}).ok)
            self.assertEqual(before, session.state.to_dict())
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose',
            'cards': {'hand': [creature.ref, land.ref], 'graveyard': [other_creature.ref, instant.ref]}}).ok)
        self.assertIsNone(self.resolve(session))
        for card in (creature, land):
            self.assertEqual('hand', session.state.cards[card.object_id].zone)
        for card in (other_creature, instant):
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_original_frontier_seeker_selects_mount_creature_and_keeps_private_remainder(self):
        session = self.session(3640702)
        engine = session.engine
        for oid in tuple(session.state.players['A'].zones['library']):
            engine.move_card(oid, 'outside', log=False)
        mount = self.add(engine, 'Trained Arynx', zone='library', ref='qualified-library-mount')
        creature = self.add(engine, 'Generic Bound Body', zone='library', ref='qualified-library-nonmount')
        land = self.add(engine, 'Generic Bound Plains', zone='library', ref='qualified-library-plains')
        instant = self.add(engine, 'Generic Bound Growth', zone='library', ref='qualified-library-instant')
        other = self.add(engine, 'Generic Bound Body', zone='library', ref='qualified-library-other')
        source = self.add(engine, 'Frontier Seeker', zone='hand', ref='original-frontier-seeker')
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
            semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        action = self.ready(session, source, {'W': 3, 'C': 3})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        for seat in 'BCD':
            packet = json.dumps(session.packet('pilot:'+seat, full=True))
            for card in (mount, creature, land, instant, other):
                self.assertNotIn(card.ref, packet)
        self.replay(session, load=True)
        before = session.state.to_dict()
        for principal, selected in (('pilot:B', mount.ref), ('pilot:A', creature.ref), ('pilot:A', instant.ref)):
            self.assertFalse(session.act(principal, {'action_id': 'choose', 'cards': [selected]}).ok)
            self.assertEqual(before, session.state.to_dict())
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'cards': [mount.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[mount.object_id].zone)
        self.assertEqual({card.object_id for card in (creature, land, instant, other)},
            set(session.state.players['A'].zones['library']))
        self.replay(session, load=True)


class FixedLibrarySelectionCompilerTests(unittest.TestCase):
    def test_card_qualifiers_and_optional_slots_preserve_independent_meaning(self):
        for phrase, expected in (
            ('nonland', {'excluded_types': ['land']}),
            ('artifact creature', {'types_all': ['artifact', 'creature']}),
            ('Mount creature', {'types_any': ['creature'], 'subtypes_any': ['mount']}),
            ('non-Saga permanent', {'excluded_subtypes': ['saga']}),
        ):
            with self.subTest(phrase=phrase):
                template = fixed_library_selection_effect_template(
                    'Look at the top three cards of your library. You may reveal a '+phrase+
                    ' card from among them and put it into your hand. Put the rest on the bottom of your library in any order.')
                self.assertIsNotNone(template)
                predicate = template.predicate_groups[0][0]
                for field, values in expected.items():
                    self.assertEqual(values, predicate[field])
                self.assertTrue(fixed_library_selection_node_capabilities(
                    effects=template.compiled()[1], target_schema=None, mechanic_ids=template.compiled()[3]))
        template = fixed_library_selection_effect_template(
            'Reveal the top four cards of your library. You may put a creature card and/or a land card '
            'from among them into your hand. Put the rest into your graveyard.')
        self.assertEqual('optional_slots', template.selection_policy)
        self.assertEqual(2, template.maximum)
        self.assertEqual(2, len(template.predicate_groups))
        self.assertEqual(['creature'], template.predicate_groups[0][0]['types_any'])
        self.assertEqual(['land'], template.predicate_groups[1][0]['types_any'])

    def test_unknown_library_qualifiers_fail_closed_and_real_subtypes_remain_literal(self):
        for phrase in ('double-faced', 'creature with flying', 'card with the chosen name', 'invented magic qualifier'):
            with self.subTest(phrase=phrase):
                self.assertIsNone(fixed_library_selection_effect_template(
                    'Look at the top three cards of your library. You may reveal a '+phrase+
                    ' card from among them and put it into your hand. Put the rest on the bottom of your library in any order.'))
        for phrase, subtype in (('Elf', 'elf'), ('Tyvar', 'tyvar'), ('Lesson', 'lesson'), ('Time Lord', 'time lord')):
            template = fixed_library_selection_effect_template(
                'Look at the top three cards of your library. You may reveal a '+phrase+
                ' card from among them and put it into your hand. Put the rest on the bottom of your library in any order.')
            self.assertEqual([subtype], template.predicate_groups[0][0]['subtypes_any'])

    def test_slot_priority_and_unrepresented_subtype_descriptor_mutants_are_killed(self):
        import copy
        from quorune.compiler import fixed_library_selection_templates as templates
        text = ('Reveal the top four cards of your library. You may put a creature card and/or '
            'a land card from among them into your hand. Put the rest into your graveyard.')
        def assert_two_slots():
            self.assertEqual('optional_slots', fixed_library_selection_effect_template(text).selection_policy)
        assert_two_slots()
        with patch.object(templates, '_optional_slots_shape', return_value=None):
            with self.assertRaises(AssertionError):
                assert_two_slots()
        effect = fixed_library_selection_effect_template(text).compiled()[1][0]
        bad = copy.deepcopy(effect)
        bad['predicate_groups'][0][0]['types_any'] = []
        bad['predicate_groups'][0][0]['subtypes_any'] = ['creature card']
        self.assertFalse(fixed_library_selection_node_capabilities(effects=(bad,), target_schema=None,
            mechanic_ids=(FIXED_LIBRARY_SELECTION_MECHANIC,)))

    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.base = base_record()

    def record(self, name: str, text: str, type_line: str) -> CardRecord:
        return replace(
            self.base,
            oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
            name=name,
            oracle_text=text,
            type_line=type_line,
        )

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_compiles_fixed_library_selection_across_spell_activation_trigger_and_modal_contexts(self):
        instruction = (
            "Look at the top three cards of your library. You may reveal a "
            "creature or land card from among them and put it into your hand. "
            "Put the rest on the bottom of your library in any order."
        )
        fixtures = (
            self.record("Selection Spell", instruction, "Sorcery"),
            self.record(
                "Selection Activation",
                f"{{2}}, {{T}}: {instruction}",
                "Artifact",
            ),
            self.record(
                "Selection Trigger",
                f"When this artifact enters, {instruction}",
                "Artifact",
            ),
            self.record(
                "Selection Modal",
                f"Choose one —\n• {instruction}\n• Draw a card.",
                "Sorcery",
            ),
        )
        for record in fixtures:
            with self.subTest(card=record.name):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status)
                nodes = [node for face in ir.faces for node in face.nodes]
                effects = [
                    effect
                    for node in nodes
                    for effect in nested_effects(
                        {"effects": node.effects, "target": node.target_schema}
                    )
                    if effect.get("op") == "fixed_library_selection"
                ]
                self.assertEqual(1, len(effects))
                self.assertTrue(
                    any(
                        FIXED_LIBRARY_SELECTION_CAPABILITY
                        in node.capability_dependencies
                        for node in nodes
                    )
                )

    def test_unsupported_library_selection_grammar_remains_residual(self):
        variants = (
            "Look at the top X cards of your library. Put one into your hand and the rest into your graveyard.",
            "Look at the top three cards of target player's library. Put one into your hand and the rest into their graveyard.",
            "Look at the top three cards of your library. You may put a creature card with mana value 2 or less from among them into your hand. Put the rest on the bottom of your library in any order.",
            "Look at the top three cards of your library. Put a creature card onto the battlefield and the rest into your graveyard.",
            "Look at the top three cards of your library. Put one into your hand and the rest into your graveyard. You gain 3 life.",
        )
        for index, text in enumerate(variants):
            with self.subTest(text=text):
                ir = self.compile(
                    replace(
                        self.base,
                        oracle_id=f"fixture:unsupported-selection-{index}",
                        oracle_text=text,
                    )
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_library_selection_shape_and_compiler_mutants_are_killed(self):
        template = fixed_library_selection_effect_template(
            self.base.oracle_text
        )
        self.assertIsNotNone(template)
        _template_id, effects, target, mechanics = template.compiled()
        self.assertEqual(
            (FIXED_LIBRARY_SELECTION_CAPABILITY,),
            fixed_library_selection_node_capabilities(
                effects=effects,
                target_schema=target,
                mechanic_ids=mechanics,
            ),
        )
        for mutated, mutated_target, mutated_mechanics in (
            (({**effects[0], "look_count": True},), None, mechanics),
            (({**effects[0], "player": "$target.0"},), None, mechanics),
            (({**effects[0], "extra": 1},), None, mechanics),
            (effects, {"zones": ["player"]}, mechanics),
            (effects, None, ()),
        ):
            with self.subTest(effects=mutated):
                self.assertEqual(
                    (),
                    fixed_library_selection_node_capabilities(
                        effects=mutated,
                        target_schema=mutated_target,
                        mechanic_ids=mutated_mechanics,
                    ),
                )
        subtype_template = fixed_library_selection_effect_template(
            "Look at the top three cards of your library. You may reveal a "
            "Goblin card from among them and put it into your hand. Put the "
            "rest into your graveyard."
        )
        self.assertIsNotNone(subtype_template)
        _template_id, subtype_effects, target, mechanics = (
            subtype_template.compiled()
        )
        predicate = dict(subtype_effects[0]["predicate_groups"][0][0])
        predicate["excluded_subtypes"] = ["elf"]
        represented = dict(subtype_effects[0])
        represented["predicate_groups"] = [[dict(predicate)]]
        self.assertEqual((FIXED_LIBRARY_SELECTION_CAPABILITY,),
            fixed_library_selection_node_capabilities(effects=(represented,),
                target_schema=target, mechanic_ids=mechanics))
        predicate["excluded_subtypes"] = ["unrepresented subtype"]
        mutated = dict(subtype_effects[0])
        mutated["predicate_groups"] = [[predicate]]
        self.assertEqual(
            (),
            fixed_library_selection_node_capabilities(
                effects=(mutated,),
                target_schema=target,
                mechanic_ids=mechanics,
            ),
        )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "zone.change.destination_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        blocked = compile_oracle_card(
            self.base,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)
        with patch(
            "quorune.oracle_ir.fixed_library_selection_effect_template",
            return_value=None,
        ):
            killed = self.compile(self.base)
        self.assertNotEqual("exact", killed.status)


class FixedLibrarySelectionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=True,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def begin(self, session, text: str, *, seat: str = "A") -> tuple[str, ...]:
        template = fixed_library_selection_effect_template(text)
        self.assertIsNotNone(template)
        effect = dict(template.compiled()[1][0])
        effect["player"] = seat
        count = int(effect["look_count"])
        engine = session.engine
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(
                engine.state.players[seat].zones["library"][-count:]
            )
        )
        card = next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.is_card_object
            and card.zone not in {"command", "outside", "library"}
        )
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = seat
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        key = f"test:fixed-library-selection:{uuid.uuid4().hex}"
        program = SemanticProgram(
            key=key,
            label="Fixed library selection",
            effects=[effect],
            destination="graveyard",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref=f"S-selection-{uuid.uuid4().hex}",
            kind="spell",
            controller=seat,
            label=program.label,
            card_object_id=card.object_id,
            semantic_key=key,
            default_destination="graveyard",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            program.effects,
            program.destination,
            note="typed fixed library selection regression",
        )
        return expected

    @staticmethod
    def set_top(engine, seat: str, object_ids_top_first: tuple[str, ...]) -> None:
        library = engine.state.players[seat].zones["library"]
        for object_id in object_ids_top_first:
            library.remove(object_id)
        library.extend(reversed(object_ids_top_first))

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )

    def add_card(self, engine, *, seat: str, name: str, ref: str):
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def test_selected_cards_and_remainder_commit_in_printed_destinations(self):
        session = self.session(401401)
        engine = session.engine
        expected = self.begin(session, base_record().oracle_text)
        schema = engine.state.pending_decision.payload_by_actor["A"][
            "legal_actions"
        ][0]["choice_schema"]
        self.assertEqual("ordered_partition", schema["shape"])
        self.assertEqual({"hand", "graveyard"}, set(schema["partitions"]))
        selected = expected[1]
        remainder = (expected[0], expected[2])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {
                    "hand": [selected],
                    "graveyard": list(remainder),
                },
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "hand",
            next(card for card in engine.state.cards.values() if card.ref == selected).zone,
        )
        self.assertTrue(
            all(
                next(card for card in engine.state.cards.values() if card.ref == ref).zone
                == "graveyard"
                for ref in remainder
            )
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "fixed-library-selection"
            session.save(record)
            replay = replay_record(record, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        random_bottom = self.session(401402)
        random_engine = random_bottom.engine
        looked = self.begin(
            random_bottom,
            "Look at the top three cards of your library. Put one of them into your hand and the rest on the bottom of your library in a random order.",
        )
        accepted = random_bottom.act(
            "pilot:A",
            {"action_id": "choose", "cards": [looked[0]]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        bottom_refs = {
            random_engine.state.cards[object_id].ref
            for object_id in random_engine.state.players["A"].zones["library"][:2]
        }
        self.assertEqual(set(looked[1:]), bottom_refs)

        library_cards = [
            random_engine.state.cards[object_id]
            for object_id in random_engine.state.players["A"].zones["library"]
            if "B" not in random_engine.state.cards[object_id].known_to
        ]
        creature = next(
            card
            for card in library_cards
            if random_engine.card_record(card).is_creature
        )
        noncreature = next(
            card
            for card in library_cards
            if not random_engine.card_record(card).is_creature
        )
        self.set_top(
            random_engine,
            "A",
            (creature.object_id, noncreature.object_id),
        )
        all_matching = self.begin(
            random_bottom,
            "Reveal the top two cards of your library. Put all creature cards "
            "revealed this way into your hand and the rest on the bottom of "
            "your library in a random order.",
        )
        self.assertNotEqual(
            "semantic.choice",
            getattr(random_engine.state.pending_decision, "kind", None),
        )
        self.assertEqual(
            "hand",
            next(
                card
                for card in random_engine.state.cards.values()
                if card.ref == all_matching[0]
            ).zone,
        )
        self.assertEqual(
            "library",
            next(
                card
                for card in random_engine.state.cards.values()
                if card.ref == all_matching[1]
            ).zone,
        )
        self.assertIn(
            "B",
            next(
                card
                for card in random_engine.state.cards.values()
                if card.ref == all_matching[1]
            ).known_to,
        )

        random_engine.permissions.invalidate_current()
        random_engine.state.pending_decision = None
        random_engine.state.priority_player = None
        random_engine.state.priority_passes = []

        library_cards = [
            random_engine.state.cards[object_id]
            for object_id in random_engine.state.players["A"].zones["library"]
            if "B" not in random_engine.state.cards[object_id].known_to
        ]
        artifact = next(
            card
            for card in library_cards
            if "Artifact" in random_engine.card_record(card).type_line
        )
        creature = next(
            card
            for card in library_cards
            if random_engine.card_record(card).is_creature
            and card.object_id != artifact.object_id
        )
        fillers = tuple(
            card
            for card in library_cards
            if card.object_id not in {artifact.object_id, creature.object_id}
        )[:2]
        self.set_top(
            random_engine,
            "A",
            (artifact.object_id, creature.object_id, *(
                card.object_id for card in fillers
            )),
        )
        optional_slots = self.begin(
            random_bottom,
            "Look at the top four cards of your library. You may reveal an "
            "artifact card and/or a creature card from among them and put them "
            "into your hand. Put the rest on the bottom of your library in any "
            "order.",
        )
        chosen = optional_slots[:2]
        remainder = optional_slots[2:]
        self.assertTrue(
            all(
                "B" not in next(
                    card
                    for card in random_engine.state.cards.values()
                    if card.ref == ref
                ).known_to
                for ref in chosen
            )
        )
        accepted = random_bottom.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {
                    "hand": list(chosen),
                    "bottom": list(remainder),
                },
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(
            all(
                "B" in next(
                    card
                    for card in random_engine.state.cards.values()
                    if card.ref == ref
                ).known_to
                for ref in chosen
            )
        )

    def test_malformed_stale_and_predicate_invalid_responses_fail_before_mutation(self):
        stale = self.session(401403)
        stale_engine = stale.engine
        looked = self.begin(stale, base_record().oracle_text)
        top = stale_engine.state.cards[
            stale_engine.state.players["A"].zones["library"][-1]
        ]
        stale_engine.move_card(top.object_id, "hand", log=False)
        stale_engine.move_card(top.object_id, "library", position="top", log=False)
        before = authoritative_state_hash(stale_engine.state)
        rejected = stale.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"hand": [looked[0]], "graveyard": list(looked[1:])},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale_engine.state))

        predicate = self.session(401404)
        predicate_engine = predicate.engine
        library_cards = [
            predicate_engine.state.cards[object_id]
            for object_id in predicate_engine.state.players["A"].zones["library"]
        ]
        creature = next(
            card for card in library_cards if predicate_engine.card_record(card).is_creature
        )
        noncreature = next(
            card for card in library_cards if not predicate_engine.card_record(card).is_creature
        )
        self.set_top(
            predicate_engine,
            "A",
            (creature.object_id, noncreature.object_id),
        )
        looked = self.begin(
            predicate,
            "Look at the top two cards of your library. You may reveal a creature card from among them and put it into your hand. Put the rest on the bottom of your library in a random order.",
        )
        before = authoritative_state_hash(predicate_engine.state)
        rejected = predicate.act(
            "pilot:A",
            {"action_id": "choose", "cards": [looked[1]]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(predicate_engine.state))
        with self.assertRaises((TypeError, ValueError)):
            LibrarySelectionIntent(
                actor="A",
                player="A",
                arrangement=None,
                reason="malformed",
                source_stack_ref="S-malformed",
                looked_are_public=False,
                selected_are_public=False,
            )

    def test_private_look_and_public_reveal_are_projected_to_the_correct_seats(self):
        private = self.session(401405)
        private_engine = private.engine
        private_refs = self.begin(private, base_record().oracle_text)
        private_packet = json.dumps(
            StateProjector(self.db, private_engine.state)._snapshot("pilot:B"),
            sort_keys=True,
        )
        private_cards = [
            next(
                card
                for card in private_engine.state.cards.values()
                if card.ref == ref
            )
            for ref in private_refs
        ]
        self.assertTrue(all("B" not in card.known_to for card in private_cards))
        self.assertTrue(
            all(card.printed_name not in private_packet for card in private_cards)
        )

        public = self.session(401406)
        public_engine = public.engine
        public_refs = self.begin(
            public,
            "Reveal the top three cards of your library. Put up to two instant and/or sorcery cards from among them into your hand and the rest into your graveyard.",
        )
        public_packet = json.dumps(
            StateProjector(self.db, public_engine.state)._snapshot("pilot:B"),
            sort_keys=True,
        )
        public_cards = [
            next(
                card
                for card in public_engine.state.cards.values()
                if card.ref == ref
            )
            for ref in public_refs
        ]
        self.assertTrue(
            all(
                "B" in card.known_to and "B" in card.revealed_to
                for card in public_cards
            )
        )
        self.assertTrue(
            all(card.printed_name in public_packet for card in public_cards)
        )

    def test_graveyard_destination_replacement_resumes_before_partition_commit(self):
        session = self.session(401407)
        engine = session.engine
        self.register(engine, "Dauthi Voidwalker")
        voidwalker = self.add_card(
            engine,
            seat="B",
            name="Dauthi Voidwalker",
            ref="selection-voidwalker",
        )
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="Library selection replacement-order witness",
        )
        looked = self.begin(
            session,
            "Look at the top two cards of your library. Put one of them into your hand and the rest into your graveyard.",
        )
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"hand": [looked[0]], "graveyard": [looked[1]]},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertTrue(
            all(
                next(card for card in engine.state.cards.values() if card.ref == ref).zone
                == "library"
                for ref in looked
            )
        )
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        option = projected["ctx"]["options"][0]["id"]
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": option},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "hand",
            next(card for card in engine.state.cards.values() if card.ref == looked[0]).zone,
        )
        remainder = next(
            card for card in engine.state.cards.values() if card.ref == looked[1]
        )
        self.assertEqual("exile", remainder.zone)
        self.assertEqual(1, remainder.counters["void"])


class PsychicShuffleNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixture_dir = ROOT / "tests" / "fixtures"
        path = Path(cls.temporary.name) / "psychic-surgery.sqlite3"
        build_fixture_database([fixture_dir / name for name in (
            "bound-effect-program-cards.json", "scryfall-exact-lists.json",
            "announced-x-library-search-cards.json", "psychic-surgery-cards.json", "targeted-spell-control-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private shuffle", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepared_zenith(self, seed, seat, *, empty=False):
        from quorune.zone_transitions import ZoneTransitionOwner

        game = self.session(seed)
        surgery = self.add(game.engine, "Psychic Surgery")
        zenith = self.add(game.engine, "Green Sun's Zenith", seat=seat, zone="hand")
        if empty:
            library = game.state.players[seat].zones["library"]
            ZoneTransitionOwner(game.engine).move_cards_simultaneously(
                [(oid, "graveyard") for oid in tuple(library)], reason="Prepare empty library", log=False,
            )
        self.assert_closed(game, surgery)
        self.assert_closed(game, zenith)
        self.prepare(game, actor=seat, active=seat, mana={"G": 1})
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        action = self.action(game, seat, zenith)
        cast = game.act("pilot:" + seat, {"action_id": action["id"], "x": 0, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        baseline = game.state.players[seat].stats.get("shuffle_count", 0)
        self.choose(game, seat, search_cards=[])
        return game, surgery, zenith, baseline

    def choose(self, game, actor="A", **payload):
        result = game.act("pilot:" + actor, {"action_id": "choose", **payload})
        self.assertTrue(result.ok, result.summary)

    def order_triggers(self, game, source, baseline):
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        triggers = {item.event_facts["shuffle_count"]: item for batch in game.state.pending_trigger_batches
                    for item in batch.items if item.source_object_id == source.object_id}
        self.assertEqual({baseline + 1, baseline + 2}, set(triggers))
        self.assertEqual({"B"}, {item.event_facts["player"] for item in triggers.values()})
        result = game.act("pilot:A", {"action_id": "order", "triggers":
                                      [item.ref for item in triggers.values()]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, self.trigger_count(game, source))

    def assert_look_choice(self, game):
        decision = self.resolve(game)
        summary = (None if decision is None else (decision.kind, decision.actors),
                   game.state.stack[-1].to_dict() if game.state.stack else None,
                   game.packet("pilot:A", full=True).get("decision"))
        self.assertIsNotNone(decision, summary)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors), summary)

    @staticmethod
    def top_objects(game, seat="B"):
        return tuple(game.state.cards[oid] for oid in reversed(game.state.players[seat].zones["library"][-2:]))

    def test_actual_zenith_two_shuffles_offer_separate_current_private_top_choices(self):
        game, source, _, baseline = self.prepared_zenith(70120011, "B")
        self.order_triggers(game, source, baseline)
        self.assert_look_choice(game)
        top = self.top_objects(game)
        refs = [card.ref for card in top]
        for seat in "ABCD":
            self.assertTrue(all(ref not in json.dumps(game.packet("pilot:" + seat, full=True)) for ref in refs))
        game = self.replay(game, load=True)
        self.choose(game, choice="look")
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertTrue(all(ref in json.dumps(packet) for ref in refs))
        for seat in "BCD":
            self.assertTrue(all(ref not in json.dumps(game.packet("pilot:" + seat, full=True)) for ref in refs))
        stable = authoritative_state_hash(game.state)
        for seat, cards in (("B", {"top": refs, "exile": []}), ("A", {"top": [], "exile": refs})):
            result = game.act("pilot:" + seat, {"action_id": "choose", "cards": cards})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(stable, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, cards={"top": refs[::-1], "exile": []})
        self.assertEqual(refs[::-1], [card.ref for card in self.top_objects(game)])
        self.assert_look_choice(game)
        self.choose(game, choice="look")
        current = game.packet("pilot:A", full=True)["decision"]
        legal = current["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertEqual(refs[::-1], legal)
        self.choose(game, cards={"top": refs[:1], "exile": refs[1:]})
        exiled = game.state.cards[top[1].object_id]
        self.assertEqual("exile", exiled.zone)
        self.assertFalse(exiled.face_down)
        self.assertEqual(refs[0], self.top_objects(game)[0].ref)
        for seat in "ABCD":
            self.assertIn(exiled.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_zenith_empty_to_one_card_shuffles_and_ignores_the_controller(self):
        own, source, _, baseline = self.prepared_zenith(70120012, "A")
        self.settle(own)
        self.assertEqual(0, self.trigger_count(own, source))
        self.assertEqual(baseline + 2, own.state.players["A"].stats["shuffle_count"])
        self.replay(own, load=True)
        game, source, zenith, baseline = self.prepared_zenith(70120013, "B", empty=True)
        self.order_triggers(game, source, baseline)
        self.assert_look_choice(game)
        (only,) = self.top_objects(game)
        self.assertEqual(zenith.object_id, only.object_id)
        before = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.choose(game, choice="decline")
        self.assertEqual(before, {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"})
        self.assert_look_choice(game)
        self.choose(game, choice="look")
        self.choose(game, cards={"top": [], "exile": [only.ref]})
        self.assertEqual([], game.state.players["B"].zones["library"])
        self.assertEqual("exile", game.state.cards[only.object_id].zone)
        self.replay(game, load=True)

    def test_foreign_controlled_zenith_shuffles_the_owners_library_without_triggering_for_it(self):
        game = self.session(70120015)
        surgery = self.add(game.engine, "Psychic Surgery")
        zenith = self.add(game.engine, "Green Sun's Zenith", seat="B", zone="hand")
        snatch = self.add(game.engine, "Aethersnatch", seat="C", zone="hand")
        for card in (surgery, zenith, snatch):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"G": 1})
        game.state.players["C"].mana_pool.update({"C": 4, "U": 2})
        game.state.config.semantic_policy = "trusted_only"
        counts = {seat: game.state.players[seat].stats.get("shuffle_count", 0) for seat in "BC"}
        self.checkpoint(game)
        action = self.action(game, "B", zenith)
        cast = game.act("pilot:B", {"action_id": action["id"], "x": 0, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        subject = game.state.stack[-1]
        self.cast(game, "C", snatch, [subject.ref])
        self.resolve_top(game)
        self.assertEqual("C", game.state.stack[-1].controller)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        self.choose(game, "C", search_cards=[])
        self.assertEqual(1, self.trigger_count(game, surgery))
        triggered, = game.state.stack
        self.assertEqual("C", triggered.context["player"])
        self.assertEqual(counts["C"] + 1, triggered.context["shuffle_count"])
        self.assertEqual(counts["B"] + 1, game.state.players["B"].stats["shuffle_count"])
        self.assertEqual("library", game.state.cards[zenith.object_id].zone)
        game = self.replay(game, load=True)
        self.assert_look_choice(game)
        self.choose(game, choice="decline")
        self.replay(game, load=True)

    def test_exact_psychic_shuffle_withdrawal_and_modified_authority_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.opponent_library_shuffle import OPPONENT_SHUFFLE_CAPABILITIES

        game = self.session(70120014)
        card = self.add(game.engine, "Psychic Surgery")
        self.assert_closed(game, card)
        (program,) = game.engine.semantics.programs_for_oracle(card.oracle_id)
        for missing in OPPONENT_SHUFFLE_CAPABILITIES:
            deps = tuple(cap for cap in program.capability_dependencies if cap != missing)
            changed = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertIn("capability:undeclared_runtime_dependency:" + missing, bound["blockers"])
        record = self.db.by_oracle_id(card.oracle_id)
        for stale in (record.oracle_text.replace("an opponent", "you"),
                      record.oracle_text.replace("top two", "top three"),
                      record.oracle_text.replace("may exile one", "exile two")):
            ir = compile_oracle_card(replace(record, oracle_text=stale), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)
        invalid = replace(program, effects=[{**program.effects[0], "library_owner": "$controller"}])
        self.assertFalse(bind_semantic_program_runtime(
            invalid, capability_registry=self.registry, profile="commander_review",
        )["strict"])


if __name__ == "__main__":
    unittest.main()

EXPRESSIVE_ITERATION = (
    "Look at the top three cards of your library. Put one of them into your hand, "
    "put one of them on the bottom of your library, and exile one of them. "
    "You may play the exiled card this turn."
)


class ExpressiveIterationNativeTests(InheritedNativeFixture, unittest.TestCase):
    ready = bound_witnesses.BoundEffectProgramRuntimeTests.ready

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "expressive-iteration.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/library-selection-predicate-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private partition witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def iteration(self, seed, *, names, only_cards=False, payment_land=False):
        game = self.session(seed)
        engine = game.engine
        hand = game.state.players["A"].zones["hand"]
        if hand:
            engine.move_card(hand[0], "outside", log=False)
        if payment_land:
            self.add(engine, "Generic Bound Plains", ref="iteration-payment")
        if only_cards:
            for object_id in tuple(game.state.players["A"].zones["library"]):
                engine.move_card(object_id, "outside", log=False)
        cards = [
            self.add(engine, name, zone="library", ref=f"iteration-role-{index}")
            for index, name in enumerate(names)
        ]
        source = self.add(engine, "Expressive Iteration", zone="hand", ref="iteration-source")
        self.assert_closed(game, source)
        action = self.ready(game, source, {"R": 1, "U": 1})
        self.checkpoint(game)
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(game)
        if len(cards) > 1:
            self.assertIsNotNone(decision)
            self.assertEqual("semantic.choice", decision.kind)
        else:
            self.assertIsNone(decision)
        return game, tuple(card.object_id for card in cards)

    def submit(self, game, *, hand, remainder, actor="A"):
        return game.act("pilot:" + actor, {
            "action_id": "choose",
            "cards": {"hand": [hand], "remainder": list(remainder)},
        })

    def actions(self, game, seat="A"):
        return {
            row["id"] for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        }

    def test_iteration_partitions_privately_then_plays_the_exiled_land(self):
        game, ids = self.iteration(873001, names=(
            "Generic Bound Plains", "Generic Bound Growth", "Generic Bound Body",
        ))
        land, hand, bottom = (game.state.cards[object_id] for object_id in ids)
        for card in (land, hand, bottom):
            self.assertIn(card.ref, json.dumps(game.packet("pilot:A", full=True)))
            for seat in "BCD":
                self.assertNotIn(card.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.assertNotIn("temporary_play_permission", land.annotations)
        before = authoritative_state_hash(game.state)
        for actor, remainder in (
            ("B", [bottom.ref, land.ref]), ("A", [hand.ref, land.ref]), ("A", [bottom.ref, "unissued"]),
        ):
            self.assertFalse(self.submit(game, hand=hand.ref, remainder=remainder, actor=actor).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = self.submit(game, hand=hand.ref, remainder=[bottom.ref, land.ref])
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        land, hand, bottom = (game.state.cards[object_id] for object_id in ids)
        self.assertEqual(("exile", "hand", "library"), (land.zone, hand.zone, bottom.zone))
        self.assertEqual(bottom.object_id, game.state.players["A"].zones["library"][0])
        self.assertEqual(["A"], bottom.known_to)
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertIn(land.ref, packet)
            self.assertNotIn(hand.ref, packet)
            self.assertNotIn(bottom.ref, packet)
        self.settle(game)
        action = f"play-land:{land.ref}"
        self.assertIn(action, self.actions(game))
        self.assertTrue(game.act("pilot:A", {"action_id": action}).ok)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self.assertNotIn("temporary_play_permission", game.state.cards[land.object_id].annotations)
        self.replay(game, load=True)

    def test_iteration_short_libraries_use_only_the_printed_prefix(self):
        names = ("Generic Bound Plains", "Generic Bound Body")
        for count in range(3):
            with self.subTest(available=count):
                game, ids = self.iteration(873010 + count, names=names[:count], only_cards=True)
                if count == 2:
                    hand, bottom = (game.state.cards[object_id] for object_id in ids)
                    result = self.submit(game, hand=hand.ref, remainder=[bottom.ref])
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                self.assertEqual(
                    tuple(["hand", "library"][:count]),
                    tuple(game.state.cards[object_id].zone for object_id in ids),
                )
                self.assertEqual(list(ids[1:]), game.state.players["A"].zones["library"])
                self.assertEqual([], game.state.players["A"].zones["exile"])
                self.replay(game)

    def test_iteration_binds_play_to_actual_exile_and_expires_at_cleanup(self):
        from quorune.impulse_access import temporary_play_permission_is_current
        from quorune.zone_transitions import ZoneTransitionOwner

        names = ("Generic Bound Draw Loss", "Generic Bound Growth", "Generic Bound Body")
        game, ids = self.iteration(873021, names=names)
        exile, hand, bottom = (game.state.cards[object_id] for object_id in ids)
        result = self.submit(game, hand=hand.ref, remainder=[bottom.ref, exile.ref])
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        card = game.state.cards[exile.object_id]
        permission = card.annotations["temporary_play_permission"]
        self.assertTrue(temporary_play_permission_is_current(game.state, "A", card, permission))
        self.assertFalse(temporary_play_permission_is_current(game.state, "B", card, permission))
        self.assertNotIn(f"cast:{card.ref}", self.actions(game))
        self.until(game, lambda s: s.state.active_player != "A" or (
            s.state.pending_decision is not None and s.state.pending_decision.kind == "cleanup.discard"
        ))
        if game.state.active_player == "A":
            context = game.packet("pilot:A", full=True)["decision"]["ctx"]
            result = game.act("pilot:A", {
                "action_id": "discard", "cards": [row["id"] for row in context["hand"][:context["count"]]],
            })
            self.assertTrue(result.ok, result.summary)
            self.until(game, lambda s: s.state.active_player != "A")
        card = game.state.cards[exile.object_id]
        self.assertEqual("exile", card.zone)
        self.assertNotIn("temporary_play_permission", card.annotations)
        self.replay(game, load=True)

        game, ids = self.iteration(873022, names=names)
        exile, hand, bottom = (game.state.cards[object_id] for object_id in ids)
        original_move = ZoneTransitionOwner.move_cards_simultaneously
        arrivals = []

        def redirect(owner, moves, **kwargs):
            requested = list(moves)
            if requested == [(exile.object_id, "exile")]:
                self.assertEqual(bottom.object_id, game.state.players["A"].zones["library"][0])
                self.assertEqual("hand", game.state.cards[hand.object_id].zone)
                arrivals.append("exile")
                requested = [(exile.object_id, "hand")]
            return original_move(owner, requested, **kwargs)

        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", autospec=True, side_effect=redirect):
            result = self.submit(game, hand=hand.ref, remainder=[bottom.ref, exile.ref])
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["exile"], arrivals)
        redirected = game.state.cards[exile.object_id]
        self.assertEqual("hand", redirected.zone)
        self.assertNotIn("temporary_play_permission", redirected.annotations)

        game, ids = self.iteration(873023, names=(
            "Generic Bound Body", "Generic Bound Growth", "Generic Bound Draw Loss",
        ), payment_land=True)
        exile, hand, bottom = (game.state.cards[object_id] for object_id in ids)
        result = self.submit(game, hand=hand.ref, remainder=[bottom.ref, exile.ref])
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        card = game.state.cards[exile.object_id]
        action = f"cast:{card.ref}"
        self.assertIn(action, self.actions(game))
        result = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards["fixture:iteration-payment"].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertNotIn("temporary_play_permission", game.state.cards[card.object_id].annotations)
        self.replay(game, load=True)

    def test_iteration_shape_and_dependency_withdrawal_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.library_play_partition import PRIVATE_HAND_BOTTOM_EXILE_CAPABILITY as capability

        record = replace(base_record(), oracle_text=EXPRESSIVE_ITERATION)
        ir = compile_oracle_card(record, capability_registry=self.registry)
        self.assertEqual("exact", ir.status)
        template = fixed_library_selection_effect_template(record.oracle_text)
        _, effects, target, mechanics = template.compiled()
        for mutation in (
            {"schema_version": True}, {"remainder_destination": "exile"}, {"look_count": 2},
            {"public_reveal": True}, {"player": "$target.0"},
        ):
            self.assertEqual((), fixed_library_selection_node_capabilities(
                effects=({**effects[0], **mutation},), target_schema=target, mechanic_ids=mechanics,
            ))
        for text in (
            record.oracle_text.replace("this turn", "until your next turn"),
            record.oracle_text.replace("play", "cast"),
            record.oracle_text.replace("exile one of them", "you may exile one of them"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=text),
                                                           capability_registry=self.registry).status)
        with patch("quorune.compiler.fixed_library_selection_templates.PrivateHandBottomExileEffectTemplate",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)
        game = self.session(873025)
        source = self.add(game.engine, "Expressive Iteration", zone="hand")
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual(1, len(programs))
        program, = programs
        self.assertTrue(game.engine.semantic_program_is_current_trusted(program))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != capability)
        mutant = replace(program, capability_dependencies=dependencies,
                         capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", binding["blockers"])
