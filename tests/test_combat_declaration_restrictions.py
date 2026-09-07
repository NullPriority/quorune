from __future__ import annotations

from dataclasses import replace
import json
import re
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from common import keep_all, load_assets, make_session
from declaration_support import compiled_declaration_fragments
from quorune.ability_fragments import (
    StaticComponentSpec,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
)
from quorune.aura import SimpleEnchantSpec
from quorune.characteristic_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
)
from quorune.carddb import CardRecord
from quorune.compiler.declaration_nodes import (
    fixed_static_declaration_grant_handler,
)
from quorune.compiler.continuous_templates import (
    attached_fixed_characteristics_handler,
    fixed_query_keyword_grant_handler,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.declaration_restrictions import (
    parse_declaration_restriction_line,
)
from quorune.declaration_requirements import parse_declaration_requirement_line
from quorune.declaration_fragments import DeclarationRestrictionTemplate
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime.ability_fragments import (
    DECLARATION_COST_FRAGMENT_HANDLER_ID,
    DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID,
    DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
    default_ability_fragment_registry,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)


class CombatDeclarationRestrictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_combat_session(self, seed: int, *, players: int = 3):
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
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def creature(
        engine,
        seat: str,
        name: str,
        *,
        oracle_text: str = "",
        keywords: tuple[str, ...] = (),
        ability_fragments: list[dict] | None = None,
        power: str = "2",
        toughness: str = "2",
        subtype: str = "Test",
        colors: tuple[str, ...] = (),
        type_line: str | None = None,
    ):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": type_line or f"Token Creature — {subtype}",
                "oracle_text": oracle_text,
                "ability_fragments": [
                    *(ability_fragments or []),
                    *compiled_declaration_fragments(name, oracle_text),
                ],
                "power": power,
                "toughness": toughness,
                "colors": list(colors),
            },
            temporary_keywords=keywords,
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def static_source(engine, seat: str, name: str, oracle_text: str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Enchantment",
                "oracle_text": oracle_text,
                "ability_fragments": compiled_declaration_fragments(
                    name,
                    oracle_text,
                ),
            },
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def permanent(
        engine,
        seat: str,
        name: str,
        *,
        type_line: str,
        tapped: bool = False,
        oracle_text: str = "",
        ability_fragments: list[dict] | None = None,
        aura_target_ref: str | None = None,
    ):
        characteristics = {
            "type_line": type_line,
            "oracle_text": oracle_text,
            "ability_fragments": [
                *(ability_fragments or []),
                *compiled_declaration_fragments(name, oracle_text),
            ],
        }
        if "planeswalker" in type_line.casefold():
            characteristics["loyalty"] = "3"
        ref = engine.create_token(
            seat,
            name=name,
            characteristics=characteristics,
            tapped=tapped,
            aura_target_ref=aura_target_ref,
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    def set_block_step(self, engine, attackers):
        engine.state.phase_index = 6
        engine.state.step = "declare_blockers"
        engine.state.combat = CombatState(
            attackers={card.object_id: defender for card, defender in attackers},
            attackers_declared=True,
            defending_players=sorted({defender for _, defender in attackers}),
        )
        for card, defender in attackers:
            card.attacking = defender

    def compiled_static_grant_source(
        self,
        engine,
        *,
        seat: str,
        name: str,
        text: str,
        aura_target_ref: str | None = None,
    ):
        compiled = fixed_static_declaration_grant_handler(
            text,
            source_name=name,
        )
        self.assertIsNotNone(compiled)
        assert compiled is not None
        semantic_key = "test:static-declaration-grant:" + re.sub(
            r"[^a-z0-9]+",
            "-",
            name.casefold(),
        ).strip("-")
        source = self.permanent(
            engine,
            seat,
            name,
            type_line="Token Enchantment",
            ability_fragments=[
                ability_fragment_to_dict(StaticComponentSpec(semantic_key))
            ],
        )
        if aura_target_ref is not None:
            target = next(
                card
                for card in engine.state.cards.values()
                if card.ref == aura_target_ref
                and card.zone == "battlefield"
            )
            source.attached_to = target.object_id
            target.attachments.append(source.object_id)
        engine.semantics.put(
            SemanticProgram(
                key=semantic_key,
                label=name,
                oracle_id=source.oracle_id,
                ability_id=f"static:{semantic_key}",
                active_zone="battlefield",
                event="characteristics.evaluate",
                handlers=[compiled[1]],
                trust_level="provisional",
            )
        )
        return source

    def test_shared_parser_is_anchored_and_classifies_exact_families(self):
        cases = {
            "This creature can't attack or block.": (
                "intrinsic-attack-block-prohibition-v1",
                ("attack", "block"),
            ),
            "Enchanted creature can't attack or block.": (
                "attached-attack-block-prohibition-v1",
                ("attack", "block"),
            ),
            "This creature can't attack or block alone.": (
                "intrinsic-attack-block-not-alone-v1",
                ("attack", "block"),
            ),
            "No more than one creature can attack each combat.": (
                "global-maximum-1-attack-v1",
                ("attack",),
            ),
            "Creatures with power less than this creature's power can't block it.": (
                "source-power-evasion-v1",
                ("block",),
            ),
            "This creature can't be blocked.": (
                "intrinsic-unblockable-v1",
                ("block",),
            ),
            "This creature can't be blocked by more than one creature.": (
                "intrinsic-maximum-blockers-v1",
                ("block",),
            ),
            "This creature can't be blocked except by three or more creatures.": (
                "intrinsic-minimum-blockers-v1",
                ("block",),
            ),
            "This creature can block only creatures with flying.": (
                "intrinsic-block-only-keyword-v1",
                ("block",),
            ),
            "This creature can't be blocked by artifact creatures.": (
                "intrinsic-blocker-filter-evasion-v1",
                ("block",),
            ),
            "This creature can't be blocked except by creatures with flying or reach.": (
                "intrinsic-allowed-blocker-filter-v1",
                ("block",),
            ),
            "Enchanted creature can't be blocked by black creatures.": (
                "attached-blocker-filter-evasion-v1",
                ("block",),
            ),
            "Enchanted creature can't be blocked except by walls.": (
                "attached-allowed-blocker-filter-v1",
                ("block",),
            ),
            "This creature can't block Humans.": (
                "intrinsic-block-filter-prohibition-v1",
                ("block",),
            ),
            "Creatures with flying can block only creatures with flying.": (
                "global-block-only-filter-v1",
                ("block",),
            ),
            "This creature can't attack unless defending player controls an Island.": (
                "intrinsic-defending-player-battlefield-attack-unless-v1",
                ("attack",),
            ),
            "This creature can't block if you control an untapped land.": (
                "intrinsic-controller-battlefield-block-if-v1",
                ("block",),
            ),
            "This creature can't be blocked as long as defending player controls an artifact.": (
                "intrinsic-defending-player-conditional-unblockable-v1",
                ("block",),
            ),
            "This creature can't attack unless at least two other creatures attack.": (
                "intrinsic-other-declarations-minimum-v1",
                ("attack",),
            ),
            "This creature can't attack unless a black or green creature also attacks.": (
                "intrinsic-matching-companion-minimum-v1",
                ("attack",),
            ),
            "This creature can't be blocked as long as it's attacking alone.": (
                "intrinsic-attacking-alone-evasion-v1",
                ("block",),
            ),
            "Enchanted creature can't be blocked as long as it's attacking alone.": (
                "attached-attacking-alone-evasion-v1",
                ("block",),
            ),
            "This creature can't be blocked as long as you control no other creatures.": (
                "intrinsic-no-other-creature-evasion-v1",
                ("block",),
            ),
            "Creatures can't attack you.": (
                "global-source-controller-attack-v1",
                ("attack",),
            ),
            "Creatures with flying can't attack you or block creatures you control.": (
                "global-source-controller-attack-block-v1",
                ("attack", "block"),
            ),
            "Enchanted creature can't attack you or planeswalkers you control.": (
                "attached-source-controller-attack-v1",
                ("attack",),
            ),
            "This creature can't be blocked unless defending player controls three or more creatures that share a creature type.": (
                "intrinsic-defending-player-shared-subtype-block-unless-v1",
                ("block",),
            ),
            "No more than two creatures can attack you each combat.": (
                "source-controller-attack-maximum-v1",
                ("attack",),
            ),
        }
        for text, (template_id, declarations) in cases.items():
            with self.subTest(text=text):
                parsed = parse_declaration_restriction_line(text)
                self.assertTrue(parsed.exact)
                self.assertEqual(template_id, parsed.template.template_id)
                self.assertEqual(declarations, parsed.declarations)

        named_source = parse_declaration_restriction_line(
            "No more than one creature can attack The Eternal Wanderer each combat.",
            card_name="The Eternal Wanderer",
        )
        self.assertTrue(named_source.exact)
        self.assertEqual(
            "source-attack-maximum-v1",
            named_source.template.template_id,
        )

        triggered = parse_declaration_restriction_line(
            "Whenever this creature attacks, target creature can't block this turn."
        )
        self.assertFalse(triggered.recognized)

        unsupported = parse_declaration_restriction_line(
            "This creature can't attack unless you have seven cards in hand."
        )
        self.assertTrue(unsupported.recognized)
        self.assertFalse(unsupported.exact)
        self.assertEqual(("attack",), unsupported.declarations)
        for complex_filter in (
            "This creature can't be blocked by creatures that don't have a name.",
            "This creature can't block unless you have four or more cards in hand.",
        ):
            with self.subTest(complex_filter=complex_filter):
                parsed = parse_declaration_restriction_line(complex_filter)
                self.assertTrue(parsed.recognized)
                self.assertFalse(parsed.exact)

    def test_absolute_self_and_attached_restrictions_remove_domains(self):
        session = self.make_combat_session(508010901, players=2)
        engine = session.engine
        self.creature(
            engine,
            "A",
            "Grounded",
            oracle_text="This creature can't attack.",
            keywords=("Haste",),
        )
        attached = self.creature(
            engine, "A", "Pacified", keywords=("Haste",)
        )
        aura = self.static_source(
            engine,
            "B",
            "Exact Aura",
            "Enchanted creature can't attack or block.",
        )
        aura.attached_to = attached.object_id
        attached.attachments.append(aura.object_id)
        free = self.creature(engine, "A", "Free", keywords=("Haste",))

        engine._issue_attackers()

        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual(
            {free.ref},
            set(payload["declaration_constraints"]["domains"]),
        )
        self.assertEqual([free.ref], [item["id"] for item in payload["candidates"]])

        attacker = self.creature(engine, "B", "Opponent")
        self.assertEqual(
            (
                False,
                "declaration_restriction:attached-attack-block-prohibition-v1",
            ),
            engine._can_block(attacker, attached),
        )

    def test_attack_or_block_alone_allows_different_defenders_and_replays(self):
        session = self.make_combat_session(508010902)
        engine = session.engine
        flunky = self.creature(
            engine,
            "A",
            "Group Attacker",
            oracle_text="This creature can't attack or block alone.",
            keywords=("Haste",),
        )
        ally = self.creature(engine, "A", "Ally", keywords=("Haste",))
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {"a": "attack", "atk": {flunky.ref: "B"}},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act(
            "pilot:A",
            {
                "a": "attack",
                "atk": {flunky.ref: "B", ally.ref: "C"},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "not-alone-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_block_alone_allows_other_blocker_on_different_attacker(self):
        session = self.make_combat_session(509010901, players=2)
        engine = session.engine
        first = self.creature(engine, "A", "First", keywords=("Haste",))
        second = self.creature(engine, "A", "Second", keywords=("Haste",))
        hulk = self.creature(
            engine,
            "B",
            "Group Blocker",
            oracle_text="This creature can't block alone.",
        )
        ally = self.creature(engine, "B", "Other Blocker")
        self.set_block_step(engine, [(first, "B"), (second, "B")])
        engine._issue_next_blocker()

        rejected = session.act(
            "pilot:B",
            {"a": "block", "blocks": {hulk.ref: first.ref}},
        )
        self.assertFalse(rejected.ok)

        accepted = session.act(
            "pilot:B",
            {
                "a": "block",
                "blocks": {hulk.ref: first.ref, ally.ref: second.ref},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)

    def test_global_maximum_constrains_requirement_solver(self):
        session = self.make_combat_session(508010903, players=2)
        engine = session.engine
        attackers = [
            self.creature(
                engine,
                "A",
                f"Required {index}",
                oracle_text="This creature attacks each combat if able.",
                keywords=("Haste",),
            )
            for index in range(2)
        ]
        self.static_source(
            engine,
            "B",
            "Exact Arbiter",
            "No more than one creature can attack each combat.",
        )
        engine._issue_attackers()

        constraints = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]
        self.assertEqual(1, constraints["maximum_requirements"])
        self.assertFalse(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {card.ref: "B" for card in attackers},
                },
            ).ok
        )
        self.assertTrue(
            session.act(
                "pilot:A",
                {"a": "attack", "atk": {attackers[0].ref: "B"}},
            ).ok
        )

    def test_goaded_opponent_creature_cannot_block(self):
        session = self.make_combat_session(509010902, players=2)
        engine = session.engine
        attacker = self.creature(engine, "A", "Attacker", keywords=("Haste",))
        goaded = self.creature(engine, "B", "Goaded Blocker")
        free = self.creature(engine, "B", "Free Blocker")
        self.creature(
            engine,
            "A",
            "Restriction Source",
            oracle_text="Goaded creatures your opponents control can't block.",
        )
        engine.apply_effect({"op": "goad", "card": goaded.ref}, actor="A")
        self.set_block_step(engine, [(attacker, "B")])
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(goaded.ref, domains)
        self.assertIn(free.ref, domains)

    def test_keyword_filtered_global_attack_restriction(self):
        session = self.make_combat_session(508010904, players=2)
        engine = session.engine
        grounded = self.creature(engine, "A", "Grounded", keywords=("Haste",))
        flying = self.creature(
            engine, "A", "Flying", keywords=("Haste", "Flying")
        )
        islandwalk = self.creature(
            engine, "A", "Walker", keywords=("Haste", "Islandwalk")
        )
        self.static_source(
            engine,
            "B",
            "Exact Tide",
            "Creatures without flying or islandwalk can't attack.",
        )
        engine._issue_attackers()

        domains = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(grounded.ref, domains)
        self.assertIn(flying.ref, domains)
        self.assertIn(islandwalk.ref, domains)

    def test_power_color_and_subtype_block_restrictions_are_cumulative(self):
        session = self.make_combat_session(509010903, players=2)
        engine = session.engine
        wolf = self.creature(
            engine,
            "A",
            "Power Evasion",
            oracle_text=(
                "Creatures with power less than this creature's power "
                "can't block it."
            ),
            keywords=("Haste",),
            power="3",
            subtype="Warrior",
            colors=("B",),
        )
        small = self.creature(engine, "B", "Small", power="2")
        equal = self.creature(engine, "B", "Equal", power="3")
        color_limited = self.creature(
            engine,
            "B",
            "Color Limited",
            oracle_text="This creature can't block black creatures.",
            power="4",
        )
        coward = self.creature(
            engine, "B", "Coward", power="4", subtype="Coward"
        )
        self.static_source(
            engine,
            "A",
            "Subtype Rule",
            "Cowards can't block Warriors.",
        )
        self.set_block_step(engine, [(wolf, "B")])
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(small.ref, domains)
        self.assertIn(equal.ref, domains)
        self.assertNotIn(color_limited.ref, domains)
        self.assertNotIn(coward.ref, domains)

    def test_numeric_power_block_restriction_uses_effective_power(self):
        session = self.make_combat_session(509010904, players=2)
        engine = session.engine
        small = self.creature(
            engine, "A", "Small Attack", keywords=("Haste",), power="1"
        )
        large = self.creature(
            engine, "A", "Large Attack", keywords=("Haste",), power="2"
        )
        blocker = self.creature(
            engine,
            "B",
            "Numeric Blocker",
            oracle_text="This creature can't block creatures with power 2 or greater.",
        )
        self.set_block_step(engine, [(small, "B"), (large, "B")])
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual([small.ref], domains[blocker.ref])

    def test_unblockable_and_blocker_stat_evasion_filter_domains(self):
        session = self.make_combat_session(509010905, players=2)
        engine = session.engine
        unblockable = self.creature(
            engine,
            "A",
            "Unblockable",
            oracle_text="This creature can't be blocked.",
            keywords=("Haste",),
        )
        limited = self.creature(
            engine,
            "A",
            "Stat Evasion",
            oracle_text=(
                "This creature can't be blocked by creatures with power 3 "
                "or greater."
            ),
            keywords=("Haste",),
        )
        small = self.creature(engine, "B", "Small Blocker", power="2")
        large = self.creature(engine, "B", "Large Blocker", power="3")
        self.set_block_step(
            engine, [(unblockable, "B"), (limited, "B")]
        )
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual([limited.ref], domains[small.ref])
        self.assertNotIn(large.ref, domains)

    def test_type_color_keyword_and_attached_evasion_filters_are_cumulative(self):
        session = self.make_combat_session(509010910, players=2)
        engine = session.engine
        artifact_evasion = self.creature(
            engine,
            "A",
            "Artifact Evasion",
            oracle_text="This creature can't be blocked by artifact creatures.",
            keywords=("Haste",),
        )
        allowed_union = self.creature(
            engine,
            "A",
            "Allowed Union",
            oracle_text=(
                "This creature can't be blocked except by artifact creatures "
                "and/or white creatures."
            ),
            keywords=("Haste",),
        )
        attached = self.creature(
            engine, "A", "Attached Evasion", keywords=("Haste",)
        )
        aura = self.static_source(
            engine,
            "A",
            "Attached Filter",
            (
                "Enchanted creature can't be blocked except by walls and/or "
                "creatures with flying."
            ),
        )
        aura.attached_to = attached.object_id
        attached.attachments.append(aura.object_id)
        artifact = self.creature(
            engine,
            "B",
            "Artifact Blocker",
            type_line="Artifact Creature — Construct",
        )
        white = self.creature(
            engine, "B", "White Blocker", colors=("W",)
        )
        flying = self.creature(
            engine, "B", "Flying Blocker", keywords=("Flying",)
        )
        wall = self.creature(engine, "B", "Wall", subtype="Wall")
        ground = self.creature(engine, "B", "Ground Blocker")
        self.set_block_step(
            engine,
            [
                (artifact_evasion, "B"),
                (allowed_union, "B"),
                (attached, "B"),
            ],
        )
        engine._issue_next_blocker()
        session.initial_checkpoint = checkpoint_envelope(session.state)

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual([allowed_union.ref], domains[artifact.ref])
        self.assertEqual(
            [artifact_evasion.ref, allowed_union.ref], domains[white.ref]
        )
        self.assertEqual(
            [artifact_evasion.ref, attached.ref], domains[flying.ref]
        )
        self.assertEqual(
            [artifact_evasion.ref, attached.ref], domains[wall.ref]
        )
        self.assertEqual([artifact_evasion.ref], domains[ground.ref])
        self.assertFalse(engine._can_block(artifact_evasion, artifact)[0])
        result = session.act(
            "pilot:B",
            {"a": "block", "blocks": {artifact.ref: allowed_union.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "filter-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)

    def test_self_attached_and_global_block_only_filters_share_domains(self):
        session = self.make_combat_session(509010911, players=2)
        engine = session.engine
        human = self.creature(
            engine, "A", "Human", keywords=("Haste",), subtype="Human"
        )
        beast = self.creature(
            engine, "A", "Beast", keywords=("Haste",), subtype="Beast"
        )
        flying = self.creature(
            engine,
            "A",
            "Flying",
            keywords=("Haste", "Flying"),
        )
        self_limited = self.creature(
            engine,
            "B",
            "Human Limited",
            oracle_text="This creature can't block Humans.",
            keywords=("Reach",),
        )
        attached_limited = self.creature(
            engine, "B", "Attached Limited", keywords=("Reach",)
        )
        aura = self.static_source(
            engine,
            "B",
            "Attached Cloud Rule",
            "Enchanted creature can block only creatures with flying.",
        )
        aura.attached_to = attached_limited.object_id
        attached_limited.attachments.append(aura.object_id)
        flying_limited = self.creature(
            engine, "B", "Flying Limited", keywords=("Flying",)
        )
        self.static_source(
            engine,
            "B",
            "Global Cloud Rule",
            "Creatures with flying can block only creatures with flying.",
        )
        self.set_block_step(
            engine, [(human, "B"), (beast, "B"), (flying, "B")]
        )
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual(
            [beast.ref, flying.ref], domains[self_limited.ref]
        )
        self.assertEqual([flying.ref], domains[attached_limited.ref])
        self.assertEqual([flying.ref], domains[flying_limited.ref])

    def test_token_supertype_and_subtype_filters_use_public_characteristics(self):
        session = self.make_combat_session(509010912, players=2)
        engine = session.engine
        token_evasion = self.creature(
            engine,
            "A",
            "Token Evasion",
            oracle_text="This creature can't be blocked by creature tokens.",
            keywords=("Haste",),
        )
        legendary_only = self.creature(
            engine,
            "A",
            "Legendary Only",
            oracle_text=(
                "This creature can't be blocked except by legendary creatures."
            ),
            keywords=("Haste",),
        )
        token = self.creature(engine, "B", "Token Blocker")
        ordinary = self.creature(engine, "B", "Ordinary Blocker")
        ordinary.is_token = False
        legendary = self.creature(
            engine,
            "B",
            "Legendary Blocker",
            type_line="Legendary Creature — Hero",
        )
        legendary.is_token = False
        self.set_block_step(
            engine, [(token_evasion, "B"), (legendary_only, "B")]
        )
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(token.ref, domains)
        self.assertEqual([token_evasion.ref], domains[ordinary.ref])
        self.assertEqual(
            [token_evasion.ref, legendary_only.ref], domains[legendary.ref]
        )

    def test_source_relative_power_filters_use_effective_stats(self):
        session = self.make_combat_session(509010913, players=2)
        engine = session.engine
        small = self.creature(
            engine, "A", "Small", keywords=("Haste",), power="2"
        )
        large = self.creature(
            engine, "A", "Large", keywords=("Haste",), power="4"
        )
        limited_blocker = self.creature(
            engine,
            "B",
            "Relative Blocker",
            oracle_text=(
                "This creature can't block creatures with power greater than "
                "this creature's power."
            ),
            power="3",
        )
        relative_evasion = self.creature(
            engine,
            "A",
            "Relative Evasion",
            oracle_text=(
                "This creature can't be blocked by creatures with greater power."
            ),
            keywords=("Haste",),
            power="3",
        )
        bigger = self.creature(engine, "B", "Bigger", power="4")
        equal = self.creature(engine, "B", "Equal", power="3")
        self.set_block_step(
            engine,
            [(small, "B"), (large, "B"), (relative_evasion, "B")],
        )
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual(
            [small.ref, relative_evasion.ref], domains[limited_blocker.ref]
        )
        self.assertEqual([small.ref, large.ref], domains[bigger.ref])
        self.assertEqual(
            [small.ref, large.ref, relative_evasion.ref], domains[equal.ref]
        )

    def test_defender_conditions_filter_attack_options_and_replay(self):
        session = self.make_combat_session(508010907)
        engine = session.engine
        island_gate = self.creature(
            engine,
            "A",
            "Island Gate",
            oracle_text=(
                "This creature can't attack unless defending player controls "
                "an Island."
            ),
            keywords=("Haste",),
        )
        land_shy = self.creature(
            engine,
            "A",
            "Land Shy",
            oracle_text=(
                "This creature can't attack if defending player controls an "
                "untapped land."
            ),
            keywords=("Haste",),
        )
        godhunter = self.creature(
            engine,
            "A",
            "Enchantment Gate",
            oracle_text=(
                "This creature can't attack unless defending player controls "
                "an enchantment or an enchanted permanent."
            ),
            keywords=("Haste",),
        )
        self.permanent(
            engine,
            "B",
            "Defender Island",
            type_line="Token Land — Island",
        )
        enchanted = self.creature(engine, "B", "Enchanted Defender")
        aura = self.permanent(
            engine,
            "A",
            "Opponent Aura",
            type_line="Token Enchantment — Aura",
            oracle_text="Enchant creature",
            ability_fragments=[
                ability_fragment_to_dict(SimpleEnchantSpec("creature"))
            ],
            aura_target_ref=enchanted.ref,
        )

        problem = engine._attack_declaration_problem("A")
        self.assertEqual(("B",), problem.domains[island_gate.ref])
        self.assertEqual(("C",), problem.domains[land_shy.ref])
        self.assertEqual(("B",), problem.domains[godhunter.ref])

        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        accepted = session.act(
            "pilot:A",
            {
                "a": "attack",
                "atk": {
                    island_gate.ref: "B",
                    land_shy.ref: "C",
                    godhunter.ref: "B",
                },
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "conditional-attack-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_controller_conditions_use_another_stats_and_exact_size(self):
        session = self.make_combat_session(508010908, players=2)
        engine = session.engine
        artifact_gate = self.creature(
            engine,
            "A",
            "Artifact Gate",
            oracle_text=(
                "This creature can't attack unless you control another "
                "artifact."
            ),
            keywords=("Haste",),
            type_line="Artifact Creature — Serpent",
        )
        power_gate = self.creature(
            engine,
            "A",
            "Power Gate",
            oracle_text=(
                "This creature can't attack unless you control another "
                "creature with power 4 or greater."
            ),
            keywords=("Haste",),
            power="5",
            toughness="5",
        )
        exact_gate = self.creature(
            engine,
            "A",
            "Exact Gate",
            oracle_text=(
                "This creature can't attack unless you control a 1/1 "
                "creature."
            ),
            keywords=("Haste",),
        )

        initial = engine._attack_declaration_problem("A")
        self.assertNotIn(artifact_gate.ref, initial.domains)
        self.assertNotIn(power_gate.ref, initial.domains)
        self.assertNotIn(exact_gate.ref, initial.domains)

        self.creature(
            engine,
            "A",
            "Artifact Support",
            power="4",
            toughness="4",
            type_line="Artifact Creature — Construct",
        )
        supported = engine._attack_declaration_problem("A")
        self.assertIn(artifact_gate.ref, supported.domains)
        self.assertIn(power_gate.ref, supported.domains)
        self.assertNotIn(exact_gate.ref, supported.domains)

        self.creature(engine, "A", "Wrong Size", power="1", toughness="2")
        self.assertNotIn(
            exact_gate.ref,
            engine._attack_declaration_problem("A").domains,
        )
        self.creature(engine, "A", "Exact Size", power="1", toughness="1")
        self.assertIn(
            exact_gate.ref,
            engine._attack_declaration_problem("A").domains,
        )

    def test_minimum_and_relative_counts_are_defender_specific(self):
        session = self.make_combat_session(508010909)
        engine = session.engine
        outnumber = self.creature(
            engine,
            "A",
            "Outnumber Gate",
            oracle_text=(
                "This creature can't attack unless you control more creatures "
                "than defending player."
            ),
            keywords=("Haste",),
        )
        seven_lands = self.creature(
            engine,
            "A",
            "Seven Land Gate",
            oracle_text=(
                "This creature can't attack or block unless you control seven "
                "or more lands."
            ),
            keywords=("Haste",),
        )
        self.creature(engine, "A", "Ally")
        self.creature(engine, "B", "One Defender")
        self.creature(engine, "C", "First Defender")
        self.creature(engine, "C", "Second Defender")
        self.creature(engine, "C", "Third Defender")
        for number in range(6):
            self.permanent(
                engine,
                "A",
                f"Land {number}",
                type_line="Token Land",
            )

        problem = engine._attack_declaration_problem("A")
        self.assertEqual(("B",), problem.domains[outnumber.ref])
        self.assertNotIn(seven_lands.ref, problem.domains)
        self.permanent(
            engine,
            "A",
            "Seventh Land",
            type_line="Token Land",
        )
        self.assertEqual(
            ("B", "C"),
            engine._attack_declaration_problem("A").domains[seven_lands.ref],
        )

    def test_block_conditions_recompute_from_current_battlefield(self):
        session = self.make_combat_session(509010915, players=2)
        engine = session.engine
        attacker = self.creature(
            engine, "A", "Attacker", keywords=("Haste",)
        )
        zombie = self.creature(
            engine,
            "B",
            "Zombie Gate",
            oracle_text=(
                "This creature can't block unless you control another Zombie."
            ),
            subtype="Zombie",
        )
        land_shy = self.creature(
            engine,
            "B",
            "Untapped Land Gate",
            oracle_text=(
                "This creature can't block if you control an untapped land."
            ),
        )
        land_count = self.creature(
            engine,
            "B",
            "Land Count Gate",
            oracle_text=(
                "This creature can't block unless you control more lands than "
                "attacking player."
            ),
        )
        defender_land = self.permanent(
            engine,
            "B",
            "Defender Land",
            type_line="Token Land",
        )
        self.set_block_step(engine, [(attacker, "B")])

        initial = engine._block_declaration_problem("B")
        self.assertNotIn(zombie.ref, initial.domains)
        self.assertNotIn(land_shy.ref, initial.domains)
        self.assertIn(land_count.ref, initial.domains)
        self.assertFalse(engine._can_block(attacker, zombie)[0])
        self.assertFalse(engine._can_block(attacker, land_shy)[0])

        self.creature(engine, "B", "Zombie Ally", subtype="Zombie")
        defender_land.tapped = True
        recomputed = engine._block_declaration_problem("B")
        self.assertIn(zombie.ref, recomputed.domains)
        self.assertIn(land_shy.ref, recomputed.domains)
        self.assertTrue(engine._can_block(attacker, zombie)[0])
        self.assertTrue(engine._can_block(attacker, land_shy)[0])

        self.permanent(engine, "A", "Attack Land One", type_line="Token Land")
        self.permanent(engine, "A", "Attack Land Two", type_line="Token Land")
        self.assertNotIn(
            land_count.ref,
            engine._block_declaration_problem("B").domains,
        )

    def test_conditional_evasion_updates_direct_and_domain_legality(self):
        session = self.make_combat_session(509010916, players=2)
        engine = session.engine
        artifact_evasion = self.creature(
            engine,
            "A",
            "Artifact Evasion",
            oracle_text=(
                "This creature can't be blocked as long as defending player "
                "controls an artifact."
            ),
            keywords=("Haste",),
        )
        snow_evasion = self.creature(
            engine,
            "A",
            "Snow Evasion",
            oracle_text=(
                "This creature can't be blocked by creatures with power 2 or "
                "greater as long as defending player controls a snow land."
            ),
            keywords=("Haste",),
        )
        small = self.creature(engine, "B", "Small Blocker", power="1")
        large = self.creature(engine, "B", "Large Blocker", power="2")
        self.set_block_step(
            engine,
            [(artifact_evasion, "B"), (snow_evasion, "B")],
        )

        self.assertTrue(engine._can_block(artifact_evasion, large)[0])
        self.assertTrue(engine._can_block(snow_evasion, large)[0])
        self.permanent(
            engine,
            "B",
            "Artifact",
            type_line="Token Artifact",
        )
        self.permanent(
            engine,
            "B",
            "Snow Land",
            type_line="Snow Token Land",
        )

        self.assertFalse(engine._can_block(artifact_evasion, large)[0])
        self.assertFalse(engine._can_block(snow_evasion, large)[0])
        self.assertTrue(engine._can_block(snow_evasion, small)[0])
        domains = engine._block_declaration_problem("B").domains
        self.assertEqual((snow_evasion.ref,), domains[small.ref])
        self.assertNotIn(artifact_evasion.ref, domains.get(large.ref, ()))
        self.assertNotIn(snow_evasion.ref, domains.get(large.ref, ()))

    def test_source_can_require_two_other_attackers_and_replays(self):
        session = self.make_combat_session(508010917)
        engine = session.engine
        conscript = self.creature(
            engine,
            "A",
            "Conscript",
            oracle_text=(
                "This creature can't attack unless at least two other "
                "creatures attack."
            ),
            keywords=("Haste",),
        )
        allies = [
            self.creature(engine, "A", f"Ally {index}", keywords=("Haste",))
            for index in range(2)
        ]
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)

        restriction = next(
            item
            for item in engine.state.pending_decision.payload_by_actor["A"][
                "declaration_constraints"
            ]["restrictions"]
            if item["trigger_variable"] == conscript.ref
        )
        self.assertEqual("minimum_total_selections", restriction["kind"])
        self.assertEqual(3, restriction["count"])
        self.assertFalse(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {conscript.ref: "B", allies[0].ref: "C"},
                },
            ).ok
        )
        self.assertTrue(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {
                        conscript.ref: "B",
                        allies[0].ref: "C",
                        allies[1].ref: "B",
                    },
                },
            ).ok
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "other-attackers-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_matching_attack_and_block_companions_are_exact(self):
        attack_session = self.make_combat_session(508010918, players=2)
        engine = attack_session.engine
        puma = self.creature(
            engine,
            "A",
            "Puma",
            oracle_text=(
                "This creature can't attack unless a black or green creature "
                "also attacks."
            ),
            keywords=("Haste",),
        )
        white = self.creature(
            engine,
            "A",
            "White Ally",
            keywords=("Haste",),
            colors=("W",),
        )
        green = self.creature(
            engine,
            "A",
            "Green Ally",
            keywords=("Haste",),
            colors=("G",),
        )
        problem = engine._attack_declaration_problem("A")
        restriction = next(
            item
            for item in problem.restrictions
            if item.trigger_variable == puma.ref
        )
        self.assertEqual((green.ref,), restriction.variables)
        self.assertFalse(
            problem.evaluate({puma.ref: "B", white.ref: "B"}).legal
        )
        self.assertTrue(
            problem.evaluate({puma.ref: "B", green.ref: "B"}).legal
        )

        block_session = self.make_combat_session(509010917, players=2)
        engine = block_session.engine
        attackers = [
            self.creature(engine, "A", f"Attacker {index}", keywords=("Haste",))
            for index in range(2)
        ]
        small = self.creature(engine, "B", "Small Blocker", power="1")
        large = self.creature(engine, "B", "Large Blocker", power="3")
        gated = self.creature(
            engine,
            "B",
            "Power Gate",
            oracle_text=(
                "This creature can't block unless a creature with greater "
                "power also blocks."
            ),
            power="2",
        )
        self.set_block_step(
            engine, [(attackers[0], "B"), (attackers[1], "B")]
        )
        problem = engine._block_declaration_problem("B")
        self.assertFalse(
            problem.evaluate(
                {gated.ref: attackers[0].ref, small.ref: attackers[1].ref}
            ).legal
        )
        self.assertTrue(
            problem.evaluate(
                {gated.ref: attackers[0].ref, large.ref: attackers[1].ref}
            ).legal
        )

    def test_source_can_require_two_other_blockers(self):
        session = self.make_combat_session(509010924, players=2)
        engine = session.engine
        attackers = [
            self.creature(engine, "A", f"Attacker {index}", keywords=("Haste",))
            for index in range(3)
        ]
        gated = self.creature(
            engine,
            "B",
            "Group Blocker",
            oracle_text=(
                "This creature can't block unless at least two other "
                "creatures block."
            ),
        )
        allies = [
            self.creature(engine, "B", f"Blocker Ally {index}")
            for index in range(2)
        ]
        self.set_block_step(
            engine, [(attacker, "B") for attacker in attackers]
        )
        problem = engine._block_declaration_problem("B")
        self.assertFalse(
            problem.evaluate(
                {gated.ref: attackers[0].ref, allies[0].ref: attackers[1].ref}
            ).legal
        )
        self.assertTrue(
            problem.evaluate(
                {
                    gated.ref: attackers[0].ref,
                    allies[0].ref: attackers[1].ref,
                    allies[1].ref: attackers[2].ref,
                }
            ).legal
        )

    def test_attacking_alone_and_no_other_creature_evasion_recompute(self):
        session = self.make_combat_session(509010918, players=2)
        engine = session.engine
        lone = self.creature(
            engine,
            "A",
            "Lone Attacker",
            oracle_text=(
                "This creature can't be blocked as long as it's attacking alone."
            ),
            keywords=("Haste",),
        )
        blocker = self.creature(engine, "B", "Blocker")
        self.set_block_step(engine, [(lone, "B")])
        self.assertFalse(engine._can_block(lone, blocker)[0])
        self.assertNotIn(
            blocker.ref, engine._block_declaration_problem("B").domains
        )

        other = self.creature(engine, "A", "Other Attacker", keywords=("Haste",))
        self.set_block_step(engine, [(lone, "B"), (other, "B")])
        self.assertTrue(engine._can_block(lone, blocker)[0])
        self.assertIn(
            lone.ref,
            engine._block_declaration_problem("B").domains[blocker.ref],
        )

        solitary_session = self.make_combat_session(509010923, players=2)
        engine = solitary_session.engine
        solitary = self.creature(
            engine,
            "A",
            "Solitary",
            oracle_text=(
                "This creature can't be blocked as long as you control no "
                "other creatures."
            ),
            keywords=("Haste",),
        )
        blocker = self.creature(engine, "B", "Blocker")
        self.set_block_step(engine, [(solitary, "B")])
        self.assertFalse(engine._can_block(solitary, blocker)[0])
        self.creature(engine, "A", "Battlefield Ally")
        self.assertTrue(engine._can_block(solitary, blocker)[0])

    def test_attached_attacking_alone_evasion_uses_current_combat(self):
        session = self.make_combat_session(509010919, players=2)
        engine = session.engine
        attacker = self.creature(engine, "A", "Enchanted", keywords=("Haste",))
        blocker = self.creature(engine, "B", "Blocker")
        aura = self.static_source(
            engine,
            "A",
            "Lone Aura",
            "Enchanted creature can't be blocked as long as it's attacking alone.",
        )
        aura.attached_to = attacker.object_id
        attacker.attachments.append(aura.object_id)
        self.set_block_step(engine, [(attacker, "B")])
        self.assertFalse(engine._can_block(attacker, blocker)[0])
        other = self.creature(engine, "A", "Other", keywords=("Haste",))
        self.set_block_step(engine, [(attacker, "B"), (other, "B")])
        self.assertTrue(engine._can_block(attacker, blocker)[0])

    def test_attack_target_restrictions_are_source_controller_scoped(self):
        session = self.make_combat_session(508010919)
        engine = session.engine
        attacker = self.creature(engine, "A", "Attacker", keywords=("Haste",))
        self.static_source(
            engine,
            "B",
            "Archon Effect",
            "Creatures can't attack you.",
        )
        self.assertEqual(
            ("C",), engine._attack_declaration_problem("A").domains[attacker.ref]
        )

        attached_session = self.make_combat_session(508010922)
        engine = attached_session.engine
        attached = self.creature(engine, "A", "Attached", keywords=("Haste",))
        b_walker = self.permanent(
            engine,
            "B",
            "B Walker",
            type_line="Token Planeswalker — Test",
        )
        c_walker = self.permanent(
            engine,
            "C",
            "C Walker",
            type_line="Token Planeswalker — Test",
        )
        aura = self.static_source(
            engine,
            "C",
            "Grasp Effect",
            "Enchanted creature can't attack you or planeswalkers you control.",
        )
        aura.attached_to = attached.object_id
        attached.attachments.append(aura.object_id)
        self.assertEqual(
            ("B", b_walker.ref),
            engine._attack_declaration_problem("A").domains[attached.ref],
        )
        self.assertNotIn(
            c_walker.ref,
            engine._attack_declaration_problem("A").domains[attached.ref],
        )

    def test_filtered_attack_and_block_target_relations(self):
        attack_session = self.make_combat_session(508010920)
        engine = attack_session.engine
        ground = self.creature(engine, "A", "Ground", keywords=("Haste",))
        flyer = self.creature(
            engine, "A", "Flyer", keywords=("Haste", "Flying")
        )
        self.static_source(
            engine,
            "B",
            "Dragon Form",
            "Creatures without flying can't attack you.",
        )
        domains = engine._attack_declaration_problem("A").domains
        self.assertEqual(("C",), domains[ground.ref])
        self.assertEqual(("B", "C"), domains[flyer.ref])

        power_session = self.make_combat_session(508010924)
        engine = power_session.engine
        small = self.creature(
            engine, "A", "Small", keywords=("Haste",), power="2"
        )
        large = self.creature(
            engine, "A", "Large", keywords=("Haste",), power="3"
        )
        self.static_source(
            engine,
            "B",
            "Reverence Effect",
            "Creatures with power 2 or less can't attack you.",
        )
        domains = engine._attack_declaration_problem("A").domains
        self.assertEqual(("C",), domains[small.ref])
        self.assertEqual(("B", "C"), domains[large.ref])

        block_session = self.make_combat_session(509010920)
        engine = block_session.engine
        protected = self.creature(engine, "B", "Protected", keywords=("Haste",))
        unprotected = self.creature(engine, "A", "Unprotected", keywords=("Haste",))
        flying_blocker = self.creature(
            engine, "C", "Flying Blocker", keywords=("Flying",)
        )
        self.static_source(
            engine,
            "B",
            "Windrider Effect",
            "Creatures with flying can't attack you or block creatures you control.",
        )
        self.set_block_step(engine, [(protected, "C"), (unprotected, "C")])
        self.assertFalse(engine._can_block(protected, flying_blocker)[0])
        self.assertTrue(engine._can_block(unprotected, flying_blocker)[0])
        self.assertEqual(
            (unprotected.ref,),
            engine._block_declaration_problem("C").domains[flying_blocker.ref],
        )

    def test_source_controller_attack_cap_is_per_target_and_replays(self):
        session = self.make_combat_session(508010921)
        engine = session.engine
        attackers = [
            self.creature(engine, "A", f"Attacker {index}", keywords=("Haste",))
            for index in range(3)
        ]
        self.static_source(
            engine,
            "B",
            "Space Effect",
            "No more than two creatures can attack you each combat.",
        )
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)

        projected = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]
        cap = next(
            item
            for item in projected["restrictions"]
            if item["kind"] == "maximum_option_uses"
        )
        self.assertEqual("B", cap["option"])
        self.assertEqual(2, cap["count"])
        self.assertFalse(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {card.ref: "B" for card in attackers},
                },
            ).ok
        )
        self.assertTrue(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {
                        attackers[0].ref: "B",
                        attackers[1].ref: "B",
                        attackers[2].ref: "C",
                    },
                },
            ).ok
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "per-target-cap-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_only_unblockable_attackers_skip_pass_only_blocker_task(self):
        session = self.make_combat_session(509010909, players=2)
        engine = session.engine
        attacker = self.creature(
            engine,
            "A",
            "No Legal Blocks",
            oracle_text="This creature can't be blocked.",
            keywords=("Haste",),
        )
        self.creature(engine, "B", "Unable Blocker")
        self.set_block_step(engine, [(attacker, "B")])

        engine._issue_next_blocker()

        self.assertTrue(engine.state.combat.blockers_declared)
        self.assertEqual("A", engine.state.priority_player)
        self.assertTrue(
            all(
                decision.kind != "combat.blockers"
                for decision in session.decisions
            )
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "combat.block"
        )
        self.assertTrue(event.details["automatic"])

    def test_minimum_and_maximum_blocker_counts_are_inviolable(self):
        minimum_session = self.make_combat_session(509010906, players=2)
        engine = minimum_session.engine
        attacker = self.creature(
            engine,
            "A",
            "Needs Three",
            oracle_text=(
                "This creature can't be blocked except by three or more creatures."
            ),
            keywords=("Haste",),
        )
        blockers = [
            self.creature(engine, "B", f"Blocker {index}")
            for index in range(3)
        ]
        self.set_block_step(engine, [(attacker, "B")])
        engine._issue_next_blocker()
        self.assertFalse(
            minimum_session.act(
                "pilot:B",
                {
                    "a": "block",
                    "blocks": {
                        blocker.ref: attacker.ref for blocker in blockers[:2]
                    },
                },
            ).ok
        )
        self.assertTrue(
            minimum_session.act(
                "pilot:B",
                {
                    "a": "block",
                    "blocks": {
                        blocker.ref: attacker.ref for blocker in blockers
                    },
                },
            ).ok
        )

        maximum_session = self.make_combat_session(509010907, players=2)
        engine = maximum_session.engine
        attacker = self.creature(
            engine,
            "A",
            "Only One",
            oracle_text=(
                "This creature can't be blocked by more than one creature."
            ),
            keywords=("Haste",),
        )
        blockers = [
            self.creature(engine, "B", f"Max Blocker {index}")
            for index in range(2)
        ]
        self.set_block_step(engine, [(attacker, "B")])
        engine._issue_next_blocker()
        self.assertFalse(
            maximum_session.act(
                "pilot:B",
                {
                    "a": "block",
                    "blocks": {
                        blocker.ref: attacker.ref for blocker in blockers
                    },
                },
            ).ok
        )
        self.assertTrue(
            maximum_session.act(
                "pilot:B",
                {
                    "a": "block",
                    "blocks": {blockers[0].ref: attacker.ref},
                },
            ).ok
        )

    def test_block_only_keyword_filters_opposing_attackers(self):
        session = self.make_combat_session(509010908, players=2)
        engine = session.engine
        ground = self.creature(
            engine, "A", "Ground Attack", keywords=("Haste",)
        )
        flying = self.creature(
            engine,
            "A",
            "Flying Attack",
            keywords=("Haste", "Flying"),
        )
        blocker = self.creature(
            engine,
            "B",
            "Cloud Blocker",
            oracle_text="This creature can block only creatures with flying.",
            keywords=("Flying",),
        )
        self.set_block_step(engine, [(ground, "B"), (flying, "B")])
        engine._issue_next_blocker()

        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual([flying.ref], domains[blocker.ref])

    def test_cast_history_condition_recomputes_attack_domain(self):
        session = self.make_combat_session(508010905, players=2)
        engine = session.engine
        self.creature(
            engine,
            "A",
            "Conditional",
            oracle_text=(
                "This creature can't attack unless you've cast a creature "
                "spell this turn."
            ),
            keywords=("Haste",),
        )

        conditional = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Conditional"
        )
        self.assertNotIn(
            conditional.ref,
            engine._attack_declaration_problem("A").domains,
        )
        engine._record_turn_history(
            "spell_cast",
            actor="A",
            types={"creature"},
        )
        self.assertIn(
            conditional.ref,
            engine._attack_declaration_problem("A").domains,
        )

    def test_named_planeswalker_attack_cap_is_target_scoped_and_replays(self):
        session = self.make_combat_session(508010925, players=2)
        engine = session.engine
        attackers = [
            self.creature(
                engine, "A", f"Attacker {index}", keywords=("Haste",)
            )
            for index in range(2)
        ]
        walker = self.permanent(
            engine,
            "B",
            "The Eternal Wanderer",
            type_line="Token Planeswalker — Wanderer",
            oracle_text=(
                "No more than one creature can attack "
                "The Eternal Wanderer each combat."
            ),
        )
        walker.annotations["copy_overrides"].update(
            {
                "loyalty": "5",
            }
        )
        walker.counters["loyalty"] = 5

        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)

        constraint = next(
            item
            for item in engine.state.pending_decision.payload_by_actor["A"]
            ["declaration_constraints"]["restrictions"]
            if item["kind"] == "maximum_option_uses"
        )
        self.assertEqual(walker.ref, constraint["option"])
        self.assertFalse(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {
                        attackers[0].ref: walker.ref,
                        attackers[1].ref: walker.ref,
                    },
                },
            ).ok
        )
        self.assertTrue(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {
                        attackers[0].ref: walker.ref,
                        attackers[1].ref: "B",
                    },
                },
            ).ok
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "planeswalker-target-cap"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_shared_creature_subtype_condition_includes_changeling(self):
        session = self.make_combat_session(509010926, players=2)
        engine = session.engine
        attacker = self.creature(
            engine,
            "A",
            "Graxiplon",
            oracle_text=(
                "This creature can't be blocked unless defending player "
                "controls three or more creatures that share a creature type."
            ),
            keywords=("Haste",),
        )
        blocker = self.creature(
            engine, "B", "Human Blocker", subtype="Human"
        )
        self.creature(engine, "B", "Lone Elf", subtype="Elf")
        self.creature(engine, "B", "First Goblin", subtype="Goblin")
        self.set_block_step(engine, [(attacker, "B")])
        self.assertFalse(engine._can_block(attacker, blocker)[0])

        self.creature(
            engine,
            "B",
            "Changeling Ally",
            subtype="Shapeshifter",
            keywords=("Changeling",),
            ability_fragments=[
                ability_fragment_to_dict(
                    AllCreatureTypesCharacteristicDefinitionSpec()
                )
            ],
        )
        self.assertFalse(engine._can_block(attacker, blocker)[0])

        self.creature(engine, "B", "Second Goblin", subtype="Goblin")
        self.assertTrue(engine._can_block(attacker, blocker)[0])
        engine._issue_next_blocker()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        self.assertIn(
            attacker.ref,
            engine._block_declaration_problem("B").domains[blocker.ref],
        )
        result = session.act(
            "pilot:B",
            {"a": "block", "blocks": {blocker.ref: attacker.ref}},
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "shared-subtype-block"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_monarch_controller_blocker_filter_is_enforced(self):
        session = self.make_combat_session(509010914, players=2)
        engine = session.engine
        attacker = self.creature(
            engine,
            "A",
            "Complex Evasion",
            oracle_text=(
                "This creature can't be blocked by creatures the monarch "
                "controls."
            ),
            keywords=("Haste",),
        )
        self.creature(engine, "B", "Potential Blocker")
        self.set_block_step(engine, [(attacker, "B")])
        engine.become_monarch("B", reason="declaration restriction test")

        engine._issue_next_blocker()

        self.assertIsNone(engine.state.pending_decision)
        self.assertIsNone(engine._semantic_pause_annotation())
        self.assertEqual("B", engine.state.monarch)

    def test_triggered_text_is_not_misread_as_a_static_restriction(self):
        session = self.make_combat_session(508010906, players=2)
        engine = session.engine
        attacker = self.creature(
            engine,
            "A",
            "Triggered Text",
            oracle_text=(
                "Whenever this creature attacks, target creature can't block "
                "this turn."
            ),
            keywords=("Haste",),
        )

        engine._issue_attackers()

        domains = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]["domains"]
        self.assertIn(attacker.ref, domains)

    def test_raw_oracle_declaration_text_has_no_runtime_authority(self):
        session = self.make_combat_session(508010917, players=2)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Raw Prose Attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "oracle_text": "This creature can't attack.",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=("Haste",),
        )[0]

        engine._issue_attackers()

        domains = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]["domains"]
        self.assertIn(ref, domains)

    def test_declaration_component_runtime_mutant_is_killed(self):
        def assert_typed_cost(seed: int) -> None:
            session = self.make_combat_session(seed, players=2)
            engine = session.engine
            self.creature(engine, "A", "Taxed", keywords=("Haste",))
            self.static_source(
                engine,
                "B",
                "Typed Prison",
                (
                    "Creatures can't attack you unless their controller "
                    "pays {2} for each creature they control that's "
                    "attacking you."
                ),
            )
            engine._issue_attackers()
            costs = engine.state.pending_decision.payload_by_actor["A"][
                "declaration_costs"
            ]
            self.assertEqual(1, len(costs))

        assert_typed_cost(508010918)
        with patch(
            "quorune.engine.declaration_cost_specs",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_typed_cost(508010919)

    def test_declaration_components_are_closed_typed_fragments(self):
        base = self.db.lookup("Arcum Dagsson")
        record = replace(
            base,
            type_line="Creature — Goblin",
            oracle_text=(
                "This creature attacks each combat if able.\n"
                "This creature can't attack or block unless you pay {2}.\n"
                "This creature can't attack or block alone."
            ),
        )
        ir = compile_oracle_card(
            record,
            trusted_mechanics={
                "cr-508-declare-attackers-step",
                "cr-509-declare-blockers-step",
            },
            capability_registry=self.capabilities,
        )

        self.assertEqual("exact", ir.status)
        handlers = [node.handlers[0] for node in ir.faces[0].nodes]
        self.assertEqual(
            {
                DECLARATION_COST_FRAGMENT_HANDLER_ID,
                DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID,
                DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
            },
            {handler["handler_id"] for handler in handlers},
        )
        self.assertTrue(
            all(handler["event"] == "combat.declaration" for handler in handlers)
        )
        registered = {
            row["handler_id"]: row
            for row in default_ability_fragment_registry().inventory()
            if row["handler_id"]
            in {
                DECLARATION_COST_FRAGMENT_HANDLER_ID,
                DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID,
                DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
            }
        }
        self.assertEqual(
            {
                DECLARATION_COST_FRAGMENT_HANDLER_ID: (
                    "ability.static.declaration_cost"
                ),
                DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID: (
                    "ability.static.declaration_requirement"
                ),
                DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID: (
                    "ability.static.declaration_restriction"
                ),
            },
            {
                handler_id: row["family"]
                for handler_id, row in registered.items()
            },
        )
        self.assertTrue(
            all(
                row["capability_dependencies"]
                == ["combat.declaration.typed_components"]
                for row in registered.values()
            )
        )
        self.assertEqual(
            3,
            len(
                {
                    type(ability_fragment_from_dict(handler["fragment"]))
                    for handler in handlers
                }
            ),
        )

    def test_static_query_and_attachment_declaration_grants_compile_closed(self):
        cases = (
            (
                "All creatures attack each combat if able.",
                "continuous-fixed-query-declaration-grant-v1",
                "declaration_requirement",
                (),
            ),
            (
                "Black creatures can't attack or block.",
                "continuous-fixed-query-declaration-grant-v1",
                "declaration_restriction",
                (),
            ),
            (
                "Equipped creature can't be blocked except by Walls.",
                "continuous-attached-declaration-grant-v1",
                "declaration_restriction",
                (),
            ),
            (
                "Enchanted creature gets +2/+2 and attacks each combat if able.",
                "continuous-attached-characteristics-declaration-grant-v1",
                "declaration_requirement",
                (),
            ),
            (
                "All creatures have double strike and attack each combat if able.",
                "continuous-fixed-query-keywords-declaration-grant-v1",
                "declaration_requirement",
                ("Double Strike",),
            ),
            (
                "Equipped creature gets +1/+2, has reach, and can't be blocked "
                "by more than one creature.",
                "continuous-attached-characteristics-declaration-grant-v1",
                "declaration_restriction",
                ("Reach",),
            ),
        )
        for index, (
            text,
            template_id,
            fragment_kind,
            keywords,
        ) in enumerate(cases):
            with self.subTest(text=text):
                record = CardRecord(
                    oracle_id=(
                        f"00000000-0000-4000-8000-{508_020_000 + index:012d}"
                    ),
                    name="Static Declaration Grant Fixture",
                    mana_cost="{2}",
                    mana_value=2.0,
                    type_line="Enchantment — Aura",
                    oracle_text=text,
                    power=None,
                    toughness=None,
                    loyalty=None,
                    defense=None,
                    colors=(),
                    color_identity=(),
                    keywords=(),
                    produced_mana=(),
                    layout="normal",
                    released_at="2026-01-01",
                    legalities={"commander": "legal"},
                    faces=(),
                    raw={},
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(
                    fragment_kind,
                    node.handlers[0]["modifier"]["add_ability_fragments"][0][
                        "kind"
                    ],
                )
                self.assertEqual(
                    list(keywords),
                    node.handlers[0]["modifier"].get("add_abilities", []),
                )
                self.assertIn(
                    "combat.declaration.typed_components",
                    node.capability_dependencies,
                )

        self.assertIsNone(
            fixed_query_keyword_grant_handler(
                "All creatures have double strike and attack each combat if able."
            )
        )
        self.assertIsNone(
            attached_fixed_characteristics_handler(
                "Equipped creature gets +1/+2, has reach, and can't be blocked "
                "by more than one creature."
            )
        )

        for index, (text, fragment_kinds) in enumerate(
            (
                (
                    "This creature can't block and can't be blocked.",
                    ("declaration_restriction", "declaration_restriction"),
                ),
                (
                    "This creature attacks or blocks each combat if able.",
                    ("declaration_requirement", "declaration_requirement"),
                ),
            )
        ):
            with self.subTest(compound=text):
                record = CardRecord(
                    oracle_id=(
                        f"00000000-0000-4000-8000-{508_020_100 + index:012d}"
                    ),
                    name="Compound Declaration Fixture",
                    mana_cost="{2}",
                    mana_value=2.0,
                    type_line="Creature — Test",
                    oracle_text=text,
                    power="2",
                    toughness="2",
                    loyalty=None,
                    defense=None,
                    colors=(),
                    color_identity=(),
                    keywords=(),
                    produced_mana=(),
                    layout="normal",
                    released_at="2026-01-01",
                    legalities={"commander": "legal"},
                    faces=(),
                    raw={},
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "intrinsic-compound-declaration-fragments-v1",
                    node.template_id,
                )
                self.assertEqual(
                    fragment_kinds,
                    tuple(handler["fragment"]["kind"] for handler in node.handlers),
                )

        self.assertFalse(
            parse_declaration_restriction_line(
                "This creature can't block and can't be blocked."
            ).exact
        )
        self.assertIsNone(
            parse_declaration_requirement_line(
                "This creature attacks or blocks each combat if able."
            )
        )

        single = compile_oracle_card(
            replace(
                record,
                oracle_text="This creature can't attack alone.",
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", single.status, single.material_residuals)
        self.assertEqual(
            "intrinsic-attack-not-alone-v1",
            single.faces[0].nodes[0].template_id,
        )

        for text in (
            "Creatures enchanted player controls attack each combat if able.",
            "Black creatures attack each combat if able this turn.",
            "Enchanted creature gets +2/+2 and attacks if able.",
        ):
            with self.subTest(unsupported=text):
                self.assertIsNone(
                    fixed_static_declaration_grant_handler(
                        text,
                        source_name="Static Declaration Grant Fixture",
                    )
                )

    def test_static_declaration_grant_dependencies_fail_closed(self):
        registry_value = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "quorune"
                / "rules"
                / "capability-registry.json"
            ).read_text(encoding="utf-8")
        )
        cases = (
            (
                "All creatures attack each combat if able.",
                {
                    "combat.declaration.typed_components",
                    "continuous.ability.fixed_query_grant",
                },
            ),
            (
                "Enchanted creature gets +2/+2 and attacks each combat if able.",
                {
                    "combat.declaration.typed_components",
                    "continuous.attached.fixed_characteristics",
                },
            ),
            (
                (
                    "All creatures have double strike and attack each combat "
                    "if able."
                ),
                {
                    "combat.damage.participation.strike_steps",
                    "combat.declaration.typed_components",
                    "continuous.ability.fixed_query_grant",
                    "continuous.ability.fixed_query_keyword_grant",
                },
            ),
        )
        for case_index, (text, dependencies) in enumerate(cases):
            record = CardRecord(
                oracle_id=(
                    f"00000000-0000-4000-8000-{508_021_000 + case_index:012d}"
                ),
                name="Static Declaration Dependency Fixture",
                mana_cost="{2}",
                mana_value=2.0,
                type_line="Enchantment — Aura",
                oracle_text=text,
                power=None,
                toughness=None,
                loyalty=None,
                defense=None,
                colors=(),
                color_identity=(),
                keywords=(),
                produced_mana=(),
                layout="normal",
                released_at="2026-01-01",
                legalities={"commander": "legal"},
                faces=(),
                raw={},
            )
            baseline = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", baseline.status)
            self.assertGreaterEqual(
                set(baseline.faces[0].nodes[0].capability_dependencies),
                dependencies,
            )
            for capability_id in dependencies:
                with self.subTest(text=text, capability_id=capability_id):
                    mutated = json.loads(json.dumps(registry_value))
                    row = next(
                        item
                        for item in mutated["capabilities"]
                        if item["id"] == capability_id
                    )
                    row["status"] = "blocked"
                    row["blockers"] = ["focused dependency mutation"]
                    registry = CapabilityRegistry(mutated)
                    registry.mark_evidence_verified("0" * 64)
                    result = compile_oracle_card(
                        record,
                        capability_registry=registry,
                        capability_profile="commander_review",
                    )
                    self.assertNotEqual("exact", result.status)
                    self.assertTrue(result.material_residuals)

    def test_compound_self_restrictions_enforce_every_conjunct(self):
        session = self.make_combat_session(508_020_002, players=2)
        engine = session.engine
        compound = self.creature(
            engine,
            "A",
            "Compound Restriction Creature",
            oracle_text="This creature can't block and can't be blocked.",
            keywords=("Haste",),
        )
        ordinary_attacker = self.creature(
            engine,
            "A",
            "Ordinary Attacker",
            keywords=("Haste",),
        )
        defending_blocker = self.creature(engine, "B", "Defending Blocker")

        self.set_block_step(
            engine,
            [(compound, "B"), (ordinary_attacker, "B")],
        )
        engine._issue_next_blocker()
        domains = engine.state.pending_decision.payload_by_actor["B"][
            "declaration_constraints"
        ]["domains"]
        self.assertEqual([ordinary_attacker.ref], domains[defending_blocker.ref])

        engine.state.pending_decision = None
        engine.state.combat = CombatState()
        compound.attacking = None
        ordinary_attacker.attacking = None
        opposing_attacker = self.creature(
            engine,
            "B",
            "Opposing Attacker",
            keywords=("Haste",),
        )
        ordinary_blocker = self.creature(engine, "A", "Ordinary Blocker")
        engine.state.active_player = "B"
        self.set_block_step(engine, [(opposing_attacker, "A")])
        engine._issue_next_blocker()
        domains = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(compound.ref, domains)
        self.assertEqual([opposing_attacker.ref], domains[ordinary_blocker.ref])

    def test_static_query_declaration_grant_uses_offer_commit_authority(self):
        session = self.make_combat_session(508_020_003, players=2)
        engine = session.engine
        compiled = fixed_static_declaration_grant_handler(
            (
                "All creatures have double strike and attack each combat "
                "if able."
            ),
            source_name="Global Requirement Fixture",
        )
        self.assertIsNotNone(compiled)
        assert compiled is not None
        semantic_key = "test:static-declaration-grant:global"
        engine.semantics.put(
            SemanticProgram(
                key=semantic_key,
                label="Global attack requirement",
                oracle_id="test:static-declaration-grant",
                ability_id=f"static:{semantic_key}",
                active_zone="battlefield",
                event="characteristics.evaluate",
                handlers=[compiled[1]],
                trust_level="provisional",
            )
        )
        self.permanent(
            engine,
            "B",
            "Global Requirement Fixture",
            type_line="Token Enchantment",
            ability_fragments=[
                ability_fragment_to_dict(StaticComponentSpec(semantic_key))
            ],
        )
        required = self.creature(
            engine,
            "A",
            "Granted Requirement Attacker",
            keywords=("Haste",),
        )

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.assertIn(
                "Double Strike",
                engine._effective_card_data(required)["keywords"],
            )
            engine._issue_attackers()
            constraints = engine.state.pending_decision.payload_by_actor["A"][
                "declaration_constraints"
            ]
            self.assertEqual(1, constraints["maximum_requirements"])
            self.assertEqual(
                required.ref,
                constraints["requirements"][0]["variable"],
            )
            before = authoritative_state_hash(engine.state)
            rejected = session.act("pilot:A", {"a": "attack", "atk": {}})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
            accepted = session.act(
                "pilot:A",
                {"a": "attack", "atk": {required.ref: "B"}},
            )
            self.assertTrue(accepted.ok, accepted.summary)

    def test_static_query_restriction_revalidates_and_obeys_ability_removal(self):
        session = self.make_combat_session(508_020_004, players=2)
        engine = session.engine
        source = self.compiled_static_grant_source(
            engine,
            seat="B",
            name="Global Black Restriction Fixture",
            text="Black creatures can't attack or block.",
        )
        attacker = self.creature(
            engine,
            "A",
            "Live Query Attacker",
            keywords=("Haste",),
        )

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._issue_attackers()
            domains = engine.state.pending_decision.payload_by_actor["A"][
                "declaration_constraints"
            ]["domains"]
            self.assertIn(attacker.ref, domains)

            attacker.annotations["copy_overrides"] = {"colors": ["B"]}
            self.assertEqual(
                ["B"],
                engine._effective_card_data(attacker)["colors"],
            )
            self.assertTrue(
                any(
                    isinstance(fragment, DeclarationRestrictionTemplate)
                    for fragment in engine._effective_ability_fragments(
                        attacker,
                        error_type=AssertionError,
                    )
                )
            )
            before = authoritative_state_hash(engine.state)
            rejected = session.act(
                "pilot:A",
                {"a": "attack", "atk": {attacker.ref: "B"}},
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))

            commit_continuous_effect(
                engine.state,
                ContinuousEffect(
                    effect_id="test:remove-global-restriction-source",
                    source_id="test:remove-global-restriction-source",
                    layer=Layer.ABILITY,
                    sublayer="6",
                    timestamp=engine._next_zone_timestamp(),
                    operations=(
                        ContinuousOperation("remove_all_abilities"),
                    ),
                    origin=ContinuousEffectOrigin.RESOLUTION,
                    duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                    locked_objects=(
                        ContinuousObjectIdentity(
                            object_id=source.object_id,
                            logical_object_id=source.logical_object_id,
                        ),
                    ),
                ),
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.combat = CombatState()
            engine._issue_attackers()
            refreshed = engine.state.pending_decision.payload_by_actor["A"][
                "declaration_constraints"
            ]["domains"]
            self.assertIn(attacker.ref, refreshed)
            accepted = session.act(
                "pilot:A",
                {"a": "attack", "atk": {attacker.ref: "B"}},
            )
            self.assertTrue(accepted.ok, accepted.summary)

    def test_attached_characteristics_and_requirement_end_with_attachment(self):
        session = self.make_combat_session(508_020_005, players=2)
        engine = session.engine
        attacker = self.creature(
            engine,
            "A",
            "Attached Requirement Attacker",
            keywords=("Haste",),
        )
        aura = self.compiled_static_grant_source(
            engine,
            seat="B",
            name="Attached Requirement Fixture",
            text=(
                "Enchanted creature gets +2/+2 and attacks each combat "
                "if able."
            ),
            aura_target_ref=attacker.ref,
        )

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            enhanced = engine._effective_card_data(attacker)
            self.assertEqual((4, 4), (int(enhanced["power"]), int(enhanced["toughness"])))
            engine._issue_attackers()
            constraints = engine.state.pending_decision.payload_by_actor["A"][
                "declaration_constraints"
            ]
            self.assertEqual(1, constraints["maximum_requirements"])
            rejected = session.act("pilot:A", {"a": "attack", "atk": {}})
            self.assertFalse(rejected.ok)

            engine.move_card(aura.object_id, "graveyard", log=False)
            self.assertEqual(
                (2, 2),
                (
                    int(engine._effective_card_data(attacker)["power"]),
                    int(engine._effective_card_data(attacker)["toughness"]),
                ),
            )
            accepted = session.act("pilot:A", {"a": "attack", "atk": {}})
            self.assertTrue(accepted.ok, accepted.summary)

    def test_declaration_component_descriptors_reject_malformed_values(self):
        fragments = compiled_declaration_fragments(
            "Closed Fixture",
            (
                "This creature attacks each combat if able.\n"
                "This creature can't attack unless you pay {2}.\n"
                "This creature can't attack alone."
            ),
        )
        handler_ids = (
            DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID,
            DECLARATION_COST_FRAGMENT_HANDLER_ID,
            DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
        )
        registry = default_ability_fragment_registry()
        for handler_id, fragment in zip(handler_ids, fragments, strict=True):
            malformed = dict(fragment)
            malformed["value"] = dict(fragment["value"])
            malformed["value"].pop(next(iter(malformed["value"])))
            with self.subTest(handler_id=handler_id), self.assertRaises(
                SemanticNodeError
            ):
                registry.validate(
                    {
                        "handler_id": handler_id,
                        "schema_version": 1,
                        "event": "combat.declaration",
                        "fragment": malformed,
                    }
                )

    def test_oracle_ir_uses_runtime_restriction_grammar(self):
        def restriction_fragment(node):
            self.assertEqual((), node.effects)
            self.assertEqual(1, len(node.handlers))
            handler = node.handlers[0]
            self.assertEqual(
                DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
                handler["handler_id"],
            )
            self.assertEqual("combat.declaration", handler["event"])
            self.assertEqual(
                "declaration_restriction",
                handler["fragment"]["kind"],
            )
            return handler["fragment"]["value"]

        base = self.db.lookup("Arcum Dagsson")
        exact = replace(
            base,
            type_line="Creature — Goblin",
            oracle_text="This creature can't attack or block alone.",
        )
        ir = compile_oracle_card(
            exact,
            trusted_mechanics={
                "cr-508-declare-attackers-step",
                "cr-509-declare-blockers-step",
            },
            capability_registry=self.capabilities,
        )

        self.assertEqual("exact", ir.status)
        node = ir.faces[0].nodes[0]
        self.assertEqual("intrinsic-attack-block-not-alone-v1", node.template_id)
        restriction_fragment(node)

        evasion = compile_oracle_card(
            replace(
                exact,
                oracle_text=(
                    "This creature can't be blocked except by artifact "
                    "creatures and/or white creatures."
                ),
            ),
            trusted_mechanics={"cr-509-declare-blockers-step"},
            capability_registry=self.capabilities,
        )
        self.assertEqual("exact", evasion.status)
        self.assertEqual(
            "intrinsic-allowed-blocker-filter-v1",
            evasion.faces[0].nodes[0].template_id,
        )

        conditional = compile_oracle_card(
            replace(
                exact,
                oracle_text=(
                    "This creature can't attack unless defending player "
                    "controls an Island."
                ),
            ),
            trusted_mechanics={"cr-508-declare-attackers-step"},
            capability_registry=self.capabilities,
        )
        self.assertEqual("exact", conditional.status)
        conditional_node = conditional.faces[0].nodes[0]
        self.assertEqual(
            "intrinsic-defending-player-battlefield-attack-unless-v1",
            conditional_node.template_id,
        )
        self.assertEqual(
            "defending_player",
            restriction_fragment(conditional_node)["condition"]["player"],
        )

        companion = compile_oracle_card(
            replace(
                exact,
                oracle_text=(
                    "This creature can't attack unless a black or green "
                    "creature also attacks."
                ),
            ),
            trusted_mechanics={"cr-508-declare-attackers-step"},
            capability_registry=self.capabilities,
        )
        self.assertEqual("exact", companion.status)
        companion_fragment = restriction_fragment(companion.faces[0].nodes[0])
        self.assertEqual(
            "minimum_matching_selections", companion_fragment["mode"]
        )
        self.assertEqual(
            ["B", "G"], companion_fragment["matching"]["colors_any"]
        )

        target_scope = compile_oracle_card(
            replace(exact, oracle_text="Creatures can't attack you."),
            trusted_mechanics={"cr-508-declare-attackers-step"},
            capability_registry=self.capabilities,
        )
        self.assertEqual("exact", target_scope.status)
        self.assertEqual(
            "source_controller",
            restriction_fragment(target_scope.faces[0].nodes[0])[
                "option_relation"
            ],
        )

        history_condition = compile_oracle_card(
            replace(
                exact,
                oracle_text=(
                    "This creature can't attack unless you've cast a creature "
                    "spell this turn."
                ),
            ),
            trusted_mechanics={"cr-508-declare-attackers-step"},
            capability_registry=self.capabilities,
        )
        self.assertEqual("exact", history_condition.status)
        self.assertFalse(history_condition.material_residuals)
        self.assertEqual(
            {
                "kind": "turn_history",
                "fact": "cast_creature_spell",
                "player": "source_controller",
            },
            restriction_fragment(history_condition.faces[0].nodes[0])[
                "condition"
            ],
        )


if __name__ == "__main__":
    unittest.main()
