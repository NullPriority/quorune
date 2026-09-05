from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, CombatState, StackItem
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.spell_cast_events import (
    SpellCastEvent,
    SpellCastEventError,
    SpellCopyEvent,
)
from quorune.selection.storm import StormCompletionContext
from quorune.storm import STORM_SEMANTIC_KEY
from scripts.build_test_database import build_fixture_database


FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "ability-word-event-trigger-cards.json"
)
TEMPLATE_IDS = {
    "fixed-counter-heroic-spell-cast-trigger-v1",
    "fixed-counter-magecraft-spell-action-trigger-v1",
    "fixed-counter-constellation-entry-trigger-v1",
    "fixed-counter-battalion-attack-trigger-v1",
    "fixed-typed-effect-heroic-spell-cast-trigger-v1",
    "fixed-typed-effect-magecraft-spell-action-trigger-v1",
    "fixed-typed-effect-constellation-entry-trigger-v1",
    "fixed-typed-effect-battalion-attack-trigger-v1",
}


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "ability-word-event-triggers.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class AbilityWordEventTriggerCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, name: str):
        return compile_oracle_card(
            self.db.lookup(name),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_four_strict_ability_word_bindings_compile_through_shared_owner(self):
        cases = (
            (
                "Heroic Counter Fixture",
                "spell.cast",
                "fixed-counter-heroic-spell-cast-trigger-v1",
                {"controller", "targets"},
            ),
            (
                "Edda, Trial Hero",
                "spell.cast",
                "fixed-typed-effect-heroic-spell-cast-trigger-v1",
                {"controller", "targets"},
            ),
            (
                "Magecraft Draw Fixture",
                "spell.cast_or_copy",
                "fixed-typed-effect-magecraft-spell-action-trigger-v1",
                {"controller", "types"},
            ),
            (
                "Constellation Life Fixture",
                "permanent.enter",
                "fixed-typed-effect-constellation-entry-trigger-v1",
                {"controller", "types"},
            ),
            (
                "Constellation Self Fixture",
                "permanent.enter",
                "fixed-typed-effect-constellation-entry-trigger-v1",
                {"card", "controller", "types"},
            ),
            (
                "Battalion Counter Fixture",
                "creature.attacks",
                "fixed-counter-battalion-attack-trigger-v1",
                {"card", "attacker_count"},
            ),
            (
                "Talia, Formation Leader",
                "creature.attacks",
                "fixed-typed-effect-battalion-attack-trigger-v1",
                {"card", "attacker_count"},
            ),
        )
        for name, event, template_id, fields in cases:
            with self.subTest(name=name):
                ir = self.compile(name)
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == template_id
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(event, node.event)
                self.assertEqual(
                    (CURRENT_ABILITY_FRAGMENT_COVERAGE,),
                    node.runtime_coverage,
                )
                self.assertEqual(fields, self.condition_fields(node.event_condition))

    def test_adjacent_event_and_body_grammar_remain_material(self):
        base = self.db.lookup("Heroic Counter Fixture")
        cases = (
            "Heroic — Whenever you cast a spell that targets a creature, draw a card.",
            "Heroic — Whenever an opponent casts a spell that targets this creature, draw a card.",
            "Magecraft — Whenever you cast or copy a permanent spell, draw a card.",
            "Constellation — Whenever one or more enchantments you control enter, draw a card.",
            "Battalion — Whenever this creature and two other creatures attack, draw a card.",
            "Heroic — Whenever you cast a spell that targets this creature, exchange control of all permanents.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"17000000-0000-4000-8000-{index + 100:012d}",
                        name="Near Miss Fixture",
                        oracle_text=text,
                        keywords=(),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.faces[0].residuals)

    def test_spell_cast_and_copy_receipts_are_strict_and_round_trip(self):
        cast = SpellCastEvent(
            schema_version=5,
            card_ref="spell-card",
            object_id="object:spell-card",
            logical_object_id="logical:spell-card:1",
            controller="A",
            origin="hand",
            stack_ref="S1",
            types=("Instant",),
            mana_value=1,
            owner="A",
            active_player="A",
            caster_spell_number=1,
            kicked=False,
            has_x_cost=False,
            has_adventure=False,
            keywords=(),
            phase="precombat_main",
            targets=("Heroic-Source", "B"),
        )
        copied = SpellCopyEvent(
            card_ref="copy-card",
            object_id="object:copy-card",
            logical_object_id="logical:copy-card:1",
            controller="A",
            stack_ref="S2",
            copied_from_stack_ref="S1",
            types=("Sorcery",),
            targets=("Heroic-Source",),
        )

        self.assertEqual(cast, SpellCastEvent.from_context(cast.to_context()))
        self.assertEqual(copied, SpellCopyEvent.from_context(copied.to_context()))
        self.assertEqual(["Heroic-Source", "B"], cast.to_context()["targets"])
        self.assertEqual(["Heroic-Source"], copied.to_context()["targets"])
        legacy = deepcopy(cast.to_context())
        legacy["schema_version"] = 4
        legacy.pop("targets")
        self.assertEqual(legacy, SpellCastEvent.from_context(legacy).to_context())
        malformed = copied.to_context()
        malformed["secret"] = "not public"
        with self.assertRaises(SpellCastEventError):
            SpellCopyEvent.from_context(malformed)

    @staticmethod
    def condition_fields(condition) -> set[str]:
        if not isinstance(condition, dict):
            return set()
        result = {condition["field"]} if "field" in condition else set()
        for key in ("all", "any"):
            for value in condition.get(key, ()):
                result.update(
                    AbilityWordEventTriggerCompilerTests.condition_fields(value)
                )
        return result


class AbilityWordEventTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
    ) -> CardInstance:
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if zone == "battlefield" else [seat],
            revealed_to=(
                list(engine.seats) if zone == "battlefield" else []
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register(self, engine, card: CardInstance):
        programs = generated_programs(
            self.db,
            self.db.by_oracle_id(card.oracle_id),
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        for program in programs:
            engine.semantics.put(program)
        selected = [
            program
            for program in programs
            if program.provenance.get("template_id") in TEMPLATE_IDS
        ]
        self.assertEqual(1, len(selected))
        return selected[0]

    def register_spell(self, engine, card: CardInstance) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.by_oracle_id(card.oracle_id),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )

    @staticmethod
    def prepare_main(engine, seat: str = "A") -> None:
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = seat
        engine.state.priority_passes = []

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def order_pending_triggers(self, session, seat: str = "A") -> None:
        engine = session.engine
        decision = engine.state.pending_decision
        if decision is None:
            engine._stabilize()
            decision = engine.state.pending_decision
        if decision is None or decision.kind != "trigger.order":
            return
        refs = [
            value["id"]
            for value in decision.payload_by_actor[seat]["triggers"]
        ]
        result = session.act(
            f"pilot:{seat}",
            {"action_id": "order", "triggers": refs},
        )
        self.assertTrue(result.ok, result.summary)

    def test_targeted_cast_triggers_heroic_and_magecraft_in_one_batch(self):
        session = self.session(170001, players=4)
        engine = session.engine
        heroic = self.add_card(
            engine,
            seat="A",
            name="Heroic Counter Fixture",
            ref="heroic-source",
            zone="battlefield",
        )
        magecraft = self.add_card(
            engine,
            seat="A",
            name="Magecraft Draw Fixture",
            ref="magecraft-source",
            zone="battlefield",
        )
        heroic_program = self.register(engine, heroic)
        magecraft_program = self.register(engine, magecraft)
        spell = self.add_card(
            engine,
            seat="A",
            name="Ability Word Target Spell Fixture",
            ref="targeted-spell",
            zone="hand",
        )
        self.register_spell(engine, spell)
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["W"] = 1

        engine._cast(
            "A",
            {"card": spell.ref, "targets": [heroic.ref], "pay": "auto"},
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(2, len(batch.items))
        self.assertEqual(
            ["A", "B", "C", "D"],
            list(batch.apnap_order),
        )
        self.assertEqual(
            {heroic_program.key, magecraft_program.key},
            {item.source_ability_id for item in batch.items},
        )
        self.assertTrue(
            all(
                item.normalized_event_id
                in {"spell.cast", "spell.cast_or_copy"}
                for item in batch.items
            )
        )
        self.order_pending_triggers(session)
        trigger_by_program = {
            item.semantic_key: item for item in engine.state.stack if item.kind == "triggered_ability"
        }
        self.assertEqual([heroic.ref], trigger_by_program[heroic_program.key].context["targets"])
        for seat in engine.active_seats:
            packet = json.dumps(session.packet(f"pilot:{seat}", full=True), sort_keys=True)
            self.assertNotIn(heroic.object_id, packet)
            self.assertNotIn(heroic.logical_object_id, packet)

    def test_heroic_uses_caster_target_and_shared_current_ability_query(self):
        for controller, targets, remove_abilities, expected in (
            ("A", ("heroic-source",), False, 1),
            ("A", ("other-target",), False, 0),
            ("B", ("heroic-source",), False, 0),
            ("A", ("heroic-source",), True, 0),
        ):
            with self.subTest(
                controller=controller,
                targets=targets,
                remove_abilities=remove_abilities,
            ):
                session = self.session(170010 + expected + len(controller))
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Heroic Counter Fixture",
                    ref="heroic-source",
                    zone="battlefield",
                )
                other = self.add_card(
                    engine,
                    seat="A",
                    name="Ability Word Haste Creature Fixture",
                    ref="other-target",
                    zone="battlefield",
                )
                program = self.register(engine, source)
                if remove_abilities:
                    commit_continuous_effect(
                        engine.state,
                        ContinuousEffect(
                            effect_id="fixture:remove-heroic",
                            source_id="fixture:ability-removal",
                            layer=Layer.ABILITY,
                            sublayer="6",
                            timestamp=engine._next_zone_timestamp(),
                            operations=(
                                ContinuousOperation("remove_all_abilities"),
                            ),
                            origin=ContinuousEffectOrigin.RESOLUTION,
                            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                            applies=ObjectQuerySpec(zones=("battlefield",)),
                            locked_objects=(
                                ContinuousObjectIdentity(
                                    object_id=source.object_id,
                                    logical_object_id=source.logical_object_id,
                                ),
                            ),
                        ),
                    )
                context = SpellCastEvent(
                    schema_version=5,
                    card_ref="cast-card",
                    object_id="object:cast-card",
                    logical_object_id="logical:cast-card:1",
                    controller=controller,
                    origin="hand",
                    stack_ref="cast-stack",
                    types=("instant",),
                    mana_value=1,
                    owner=controller,
                    active_player=controller,
                    caster_spell_number=1,
                    kicked=False,
                    has_x_cost=False,
                    has_adventure=False,
                    keywords=(),
                    phase="precombat_main",
                    targets=targets,
                ).to_context()
                refs = engine._dispatch_semantic_event(
                    "spell.cast",
                    context,
                    sources=[source, other],
                )
                self.assertEqual(expected, len(refs))
                if refs:
                    self.assertEqual(
                        program.key,
                        engine.state.pending_trigger_batches[0].items[0].source_ability_id,
                    )

    def test_magecraft_uses_canonical_ordinary_spell_copy_occurrence(self):
        session = self.session(170020, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Magecraft Draw Fixture",
            ref="magecraft-copy-source",
            zone="battlefield",
        )
        program = self.register(engine, source)
        original_card = self.add_card(
            engine,
            seat="A",
            name="Ability Word Target Spell Fixture",
            ref="copy-original-card",
            zone="hand",
        )
        engine._remove_from_zone(original_card)
        engine._reset_zone_change(original_card, "stack")
        original_card.zone = "stack"
        original = StackItem(
            stack_id="stack:copy-original",
            ref="copy-original",
            kind="spell",
            controller="A",
            label=original_card.printed_name,
            card_object_id=original_card.object_id,
            targets=[source.ref],
            default_destination="graveyard",
            visibility=list(engine.seats),
            context={"dynamic_effects": []},
        )
        engine.state.stack.append(original)
        hand_before = len(engine.state.players["A"].zones["hand"])

        copied = engine._copy_stack_item(
            controller="A",
            target=original,
            targets=[source.ref],
            target_groups={},
            reason="Magecraft copy regression",
        )
        engine._stabilize()

        trigger = next(
            item for item in engine.state.stack if item.semantic_key == program.key
        )
        self.assertEqual("spell.cast_or_copy", trigger.context["event"])
        self.assertEqual(copied.ref, trigger.context["stack"])
        self.assertEqual(original.ref, trigger.context["copied_from_stack"])
        self.assertEqual(["instant"], trigger.context["types"])
        self.assertEqual([source.ref], trigger.context["targets"])
        copy_object = engine.state.cards[copied.card_object_id]
        for seat in engine.active_seats:
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertNotIn(copy_object.object_id, packet)
            self.assertNotIn(copy_object.logical_object_id, packet)
        self.resolve_top(engine)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        for controller, types in (("B", ("instant",)), ("A", ("artifact",))):
            with self.subTest(controller=controller, types=types):
                refs = engine._dispatch_semantic_event(
                    "spell.cast_or_copy",
                    SpellCopyEvent(
                        card_ref=f"copy-{controller}-{types[0]}",
                        object_id=f"object:copy-{controller}-{types[0]}",
                        logical_object_id=(
                            f"logical:copy-{controller}-{types[0]}:1"
                        ),
                        controller=controller,
                        stack_ref=f"stack:copy-{controller}-{types[0]}",
                        copied_from_stack_ref="copy-original",
                        types=types,
                    ).to_context(),
                    sources=[source],
                )
                self.assertEqual([], refs)

    def test_magecraft_uses_canonical_storm_spell_copy_occurrence(self):
        session = self.session(170021)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Magecraft Draw Fixture",
            ref="magecraft-storm-source",
            zone="battlefield",
        )
        program = self.register(engine, source)
        original_card = self.add_card(
            engine,
            seat="A",
            name="Ability Word Target Spell Fixture",
            ref="storm-original-card",
            zone="hand",
        )
        trigger = StackItem(
            stack_id="stack:storm-trigger",
            ref="storm-trigger",
            kind="triggered_ability",
            controller="A",
            label="Storm trigger",
            semantic_key=STORM_SEMANTIC_KEY,
            visibility=list(engine.seats),
            context={},
        )
        engine.state.stack.append(trigger)
        template = {
            "label": original_card.printed_name,
            "source_spell_ref": "storm-original-stack",
            "controller": "A",
            "card_object_id": original_card.object_id,
            "semantic_key": None,
            "targets": [],
            "modes": [],
            "x_value": None,
            "default_destination": "graveyard",
            "referred_object_ids": [],
            "target_groups": {},
            "target_snapshots": {},
            "target_schema": None,
        }
        context = StormCompletionContext(
            seat="A",
            response={"copy_targets": [[]]},
            trigger=trigger,
            count=1,
            template=template,
        )
        copies = engine._build_storm_copies(context)

        engine._commit_storm_copies(context, copies)
        self.assertIsNone(engine.state.pending_decision)
        engine._stabilize()

        trigger_item = next(
            item for item in engine.state.stack if item.semantic_key == program.key
        )
        self.assertEqual("spell.cast_or_copy", trigger_item.context["event"])
        self.assertEqual(
            "storm-original-stack",
            trigger_item.context["copied_from_stack"],
        )
        self.assertEqual(["instant"], trigger_item.context["types"])

    def test_spell_copy_dispatch_mutant_is_killed(self):
        session = self.session(170022)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Magecraft Draw Fixture",
            ref="magecraft-copy-mutation-source",
            zone="battlefield",
        )
        self.register(engine, source)
        original_card = self.add_card(
            engine,
            seat="A",
            name="Ability Word Target Spell Fixture",
            ref="copy-mutation-original-card",
            zone="hand",
        )
        engine._remove_from_zone(original_card)
        engine._reset_zone_change(original_card, "stack")
        original_card.zone = "stack"
        original = StackItem(
            stack_id="stack:copy-mutation-original",
            ref="copy-mutation-original",
            kind="spell",
            controller="A",
            label=original_card.printed_name,
            card_object_id=original_card.object_id,
            default_destination="graveyard",
            visibility=list(engine.seats),
            context={"dynamic_effects": []},
        )
        engine.state.stack.append(original)

        with patch(
            "quorune.engine.copy_events.dispatch_normalized_spell_copy_event",
            return_value=[],
        ):
            engine._copy_stack_item(
                controller="A",
                target=original,
                targets=[],
                target_groups={},
                reason="suppressed Magecraft copy occurrence",
            )

        self.assertFalse(engine.state.pending_trigger_batches)
        self.assertFalse(
            any(item.kind == "triggered_ability" for item in engine.state.stack)
        )

    def test_constellation_distinguishes_control_and_includes_source_entry(self):
        session = self.session(170030)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="A",
            name="Constellation Life Fixture",
            ref="constellation-observer",
            zone="battlefield",
        )
        program = self.register(engine, observer)
        controlled = self.add_card(
            engine,
            seat="A",
            name="Ability Word Enchantment Fixture",
            ref="controlled-enchantment",
            zone="hand",
        )
        opponent = self.add_card(
            engine,
            seat="B",
            name="Ability Word Enchantment Fixture",
            ref="opponent-enchantment",
            zone="hand",
        )
        life_before = engine.state.players["A"].life

        engine.move_card(
            controlled.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="controlled Constellation entry",
        )
        engine._stabilize()
        trigger = next(
            item for item in engine.state.stack if item.semantic_key == program.key
        )
        self.resolve_top(engine)
        self.assertEqual(life_before + 1, engine.state.players["A"].life)

        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.move_card(
            opponent.object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
            reason="opponent Constellation entry",
        )
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )
        self.assertFalse(engine.state.pending_trigger_batches)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        self_source = self.add_card(
            engine,
            seat="A",
            name="Constellation Self Fixture",
            ref="constellation-self",
            zone="hand",
        )
        self_program = self.register(engine, self_source)
        engine.move_card(
            self_source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="source Constellation entry",
        )
        self.assertTrue(
            any(
                item.source_ability_id == self_program.key
                and item.normalized_event_id == "permanent.enter"
                for batch in engine.state.pending_trigger_batches
                for item in batch.items
            )
        )

    def test_battalion_uses_sealed_three_attacker_transition(self):
        for attacker_count, expected in ((2, 0), (3, 1)):
            with self.subTest(attacker_count=attacker_count):
                session = self.session(170040 + attacker_count, players=4)
                engine = session.engine
                engine.state.active_player = "A"
                engine.state.phase_index = 5
                engine.state.phase = "combat"
                engine.state.step = "declare_attackers"
                engine.state.combat = CombatState()
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Battalion Counter Fixture",
                    ref="battalion-source",
                    zone="battlefield",
                )
                program = self.register(engine, source)
                attackers = [source]
                for index in range(attacker_count - 1):
                    attackers.append(
                        self.add_card(
                            engine,
                            seat="A",
                            name="Ability Word Haste Creature Fixture",
                            ref=f"battalion-attacker-{index}",
                            zone="battlefield",
                        )
                    )
                engine._issue_attackers()
                declared = session.act(
                    "pilot:A",
                    {
                        "a": "attack",
                        "atk": {card.ref: "B" for card in attackers},
                    },
                )
                self.assertTrue(declared.ok, declared.summary)
                matching = [
                    item
                    for item in engine.state.stack
                    if item.semantic_key == program.key
                ]
                self.assertEqual(expected, len(matching))
                if matching:
                    self.assertEqual(
                        attacker_count,
                        matching[0].context["attacker_count"],
                    )
                    self.assertEqual(
                        attacker_count,
                        len(
                            matching[0].context["attack_transition"][
                                "assignments"
                            ]
                        ),
                    )

    def test_targeted_cast_projection_save_load_and_exact_replay(self):
        session = self.session(170050, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Heroic Counter Fixture",
            ref="replay-heroic-source",
            zone="battlefield",
        )
        program = self.register(engine, source)
        spell = self.add_card(
            engine,
            seat="A",
            name="Ability Word Target Spell Fixture",
            ref="replay-targeted-spell",
            zone="hand",
        )
        self.register_spell(engine, spell)
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["W"] = 1
        engine._issue_priority("A")
        action = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action["id"] == f"cast:{spell.ref}"
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [source.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(result.ok, result.summary)
        trigger = next(
            item for item in engine.state.stack if item.semantic_key == program.key
        )
        self.assertEqual(5, trigger.context["schema_version"])
        self.assertEqual([source.ref], trigger.context["targets"])
        for seat in engine.active_seats:
            projected = json.dumps(
                StateProjector(self.db, engine.state)._snapshot(f"pilot:{seat}"),
                sort_keys=True,
            )
            self.assertNotIn(source.object_id, projected)
            self.assertNotIn(source.logical_object_id, projected)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "ability-word-event-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
