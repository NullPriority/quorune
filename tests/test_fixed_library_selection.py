from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import keep_all, load_assets, make_session
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


class FixedLibrarySelectionCompilerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
