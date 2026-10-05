from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from property_budget import property_transitions
from scripts.build_test_database import build_fixture_database
from quorune import damage_results as damage_results_module
from quorune.ability_fragments import ToxicSpec, ability_fragment_to_dict
from quorune.carddb import CardDatabase
from quorune.counter_placement import (
    CounterPlacementCommitPlan,
    CounterPlacementError,
    plan_resolved_counter_placement_commit,
)
from quorune.counter_removal import CounterRemovalPlan
from quorune.damage import (
    DamageError,
    commit_prepared_damage_batch,
    damage_proposal,
    prepare_damage_batch,
)
from quorune.damage_modifier_state import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    GainLifePreventionAftermath,
    PreventionMode,
)
from quorune.damage_results import (
    PreparedDamageResults,
    commit_damage_result_plan,
    plan_damage_result_commit,
    prepare_damage_results,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, CombatState, StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import (
    ReplaceableEvent,
    ReplacementClass,
    ReplacementEffect,
    ReplacementChoiceRequired,
    resolve_replacements,
)
from quorune.semantic_runtime.damage_results import (
    DamageResultReplacementSourceContext,
    collect_damage_result_replacement_effects,
)
from quorune.semantic_runtime.life_replacements import (
    LifeGainMultiplierHandler,
    LifeReplacementSourceContext,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram


def result_effect(
    effect_id: str,
    event_kind: str,
    operation: dict,
    *,
    conditions: dict | None = None,
) -> ReplacementEffect:
    return ReplacementEffect(
        effect_id=effect_id,
        source_id=f"source:{effect_id}",
        event_kind=event_kind,
        replacement_class=ReplacementClass.OTHER,
        conditions=conditions or {},
        operations=(operation,),
        label=effect_id,
    )


class DamageResultEventTests(unittest.TestCase):
    """CR 120.3/120.4c damage-result materialization and commit witnesses."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "damage-results.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "damage-result-cards.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
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

    @classmethod
    def tearDownClass(cls):
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def permanent(self, engine, seat: str, name: str, *, ref: str):
        record = self.db.lookup(name)
        if name == "Boon Reflection" or "Toxic" in record.keywords:
            register_generated_programs(
                self.db,
                engine.semantics,
                (record,),
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
                promote_exact_runtime_handlers=True,
            )
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
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def token(
        self,
        engine,
        seat: str,
        name: str,
        *,
        type_line: str = "Token Creature — Test",
        keywords: tuple[str, ...] = (),
        oracle_text: str = "",
        toxic_values: tuple[int, ...] = (),
        power: int = 3,
        toughness: int = 3,
        loyalty: int | None = None,
        defense: int | None = None,
        battle_protector: str | None = None,
    ):
        characteristics = {
            "type_line": type_line,
            "power": str(power),
            "toughness": str(toughness),
            "keywords": list(keywords),
            "oracle_text": oracle_text,
            "colors": ["G"],
            "ability_fragments": [
                ability_fragment_to_dict(ToxicSpec(value=value))
                for value in toxic_values
            ],
        }
        if loyalty is not None:
            characteristics["loyalty"] = str(loyalty)
        if defense is not None:
            characteristics["defense"] = str(defense)
        creation = {
            "name": name,
            "characteristics": characteristics,
            "battle_protector": battle_protector,
        }
        before = authoritative_state_hash(engine.state)
        try:
            refs = engine.create_token(seat, **creation)
        except ReplacementChoiceRequired as pending:
            # This isolated damage fixture chooses the order of its two
            # intrinsic entry instructions, rather than bypassing entry.
            self.assertEqual(before, authoritative_state_hash(engine.state))
            options = pending.pending.choice.options
            self.assertEqual(2, len(options))
            self.assertTrue(all(
                option.startswith("replacement.intrinsic-entry-counter:")
                for option in options
            ))
            refs = engine.create_token(
                seat, **creation, replacement_selections=(options[0],)
            )
        ref = refs[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def proposal(
        engine,
        source,
        target,
        amount: int,
        *,
        event_id: str,
        combat: bool = False,
    ):
        return damage_proposal(
            engine,
            proposal_id=event_id,
            actor=source.controller,
            source_ref=source.ref,
            target=target.ref if hasattr(target, "ref") else target,
            amount=amount,
            combat=combat,
            reason="damage-result witness",
        )

    def commit(self, engine, *proposals):
        prepared = prepare_damage_batch(engine, proposals)
        return commit_prepared_damage_batch(engine, prepared)

    def test_infect_player_damage_is_poison_not_life_loss(self):
        engine = self.session(120_300_001).engine
        source = self.token(engine, "A", "Infect Source", keywords=("Infect",))
        life_before = engine.state.players["B"].life

        result = self.commit(
            engine,
            self.proposal(
                engine, source, "B", 3, event_id="damage:infect-player"
            ),
        )

        self.assertEqual(life_before, engine.state.players["B"].life)
        self.assertEqual(3, engine.state.players["B"].poison)
        poison = [
            event
            for event in result.result_events
            if event.kind == "counter.place"
        ]
        self.assertEqual(1, len(poison))
        self.assertEqual("poison", poison[0].counter_name)
        self.assertEqual("A", poison[0].source_controller)

    def test_source_pinned_keyword_witnesses_follow_damage_results(self):
        engine = self.session(120_300_009).engine
        infect = self.permanent(
            engine, "A", "Phyrexian Crusader", ref="a-crusader"
        )
        wither = self.permanent(
            engine, "A", "Boggart Ram-Gang", ref="a-ram-gang"
        )
        lifelink = self.permanent(
            engine, "A", "Healer's Hawk", ref="a-hawk"
        )
        toxic = self.permanent(
            engine, "A", "Crawling Chorus", ref="a-chorus"
        )
        target = self.token(engine, "B", "Target", toughness=8)
        engine.state.players["A"].life = 20
        life_before = engine.state.players["B"].life

        self.commit(
            engine,
            self.proposal(
                engine, infect, "B", 2, event_id="damage:crusader"
            ),
            self.proposal(
                engine, wither, target, 3, event_id="damage:ram-gang"
            ),
            self.proposal(
                engine, lifelink, "B", 1, event_id="damage:hawk"
            ),
            self.proposal(
                engine,
                toxic,
                "B",
                1,
                event_id="damage:chorus",
                combat=True,
            ),
        )

        self.assertEqual(3, engine.state.players["B"].poison)
        self.assertEqual(life_before - 2, engine.state.players["B"].life)
        self.assertEqual(3, target.counters["-1/-1"])
        self.assertEqual(21, engine.state.players["A"].life)

    def test_infect_and_wither_use_minus_counters_only_on_creatures(self):
        for keyword in ("Infect", "Wither"):
            with self.subTest(keyword=keyword):
                engine = self.session(120_300_002).engine
                source = self.token(
                    engine, "A", f"{keyword} Source", keywords=(keyword,)
                )
                target = self.token(engine, "B", "Target", toughness=5)
                life_before = engine.state.players["B"].life

                self.commit(
                    engine,
                    self.proposal(
                        engine,
                        source,
                        target,
                        2,
                        event_id=f"damage:{keyword.casefold()}-creature",
                    ),
                    self.proposal(
                        engine,
                        source,
                        "B",
                        1,
                        event_id=f"damage:{keyword.casefold()}-player",
                    ),
                )

                self.assertEqual(0, target.marked_damage)
                self.assertEqual(2, target.counters["-1/-1"])
                if keyword == "Infect":
                    self.assertEqual(life_before, engine.state.players["B"].life)
                    self.assertEqual(1, engine.state.players["B"].poison)
                else:
                    self.assertEqual(
                        life_before - 1, engine.state.players["B"].life
                    )
                    self.assertEqual(0, engine.state.players["B"].poison)

    def test_lifelink_gains_final_damage_across_multiple_recipients(self):
        engine = self.session(120_300_003).engine
        source = self.token(
            engine, "A", "Lifelink Source", keywords=("Lifelink",)
        )
        target = self.token(engine, "B", "Target", toughness=6)
        engine.state.players["A"].life = 20

        result = self.commit(
            engine,
            self.proposal(
                engine, source, "B", 2, event_id="damage:lifelink-player"
            ),
            self.proposal(
                engine, source, target, 3, event_id="damage:lifelink-creature"
            ),
        )

        self.assertEqual(25, engine.state.players["A"].life)
        self.assertEqual(3, target.marked_damage)
        self.assertEqual(1, len(result.lifelink_gains))
        self.assertEqual(5, result.lifelink_gains[0].amount)

    def test_keyword_source_snapshot_survives_zone_and_control_change(self):
        engine = self.session(702_150_001).engine
        source = self.permanent(
            engine, "A", "Healer's Hawk", ref="a-lki-hawk"
        )
        source.temporary_keywords.append("Infect")
        engine.state.players["A"].life = 20
        life_before = engine.state.players["B"].life
        prepared = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source,
                    "B",
                    3,
                    event_id="damage:keyword-lki",
                ),
            ),
        )

        engine.move_card(
            source.object_id,
            "graveyard",
            reason="keyword LKI witness leaves before result commit",
            log=False,
            semantic_events=False,
        )
        source.controller = "C"
        result = commit_prepared_damage_batch(engine, prepared)

        self.assertEqual(life_before, engine.state.players["B"].life)
        self.assertEqual(3, engine.state.players["B"].poison)
        self.assertEqual(23, engine.state.players["A"].life)
        self.assertEqual("A", result.events[0].source_controller)

        off_zone_lifelink = self.permanent(
            engine, "A", "Healer's Hawk", ref="a-graveyard-hawk"
        )
        engine.move_card(
            off_zone_lifelink.object_id,
            "graveyard",
            reason="off-zone lifelink witness",
            log=False,
            semantic_events=False,
        )
        self.commit(
            engine,
            self.proposal(
                engine,
                off_zone_lifelink,
                "B",
                1,
                event_id="damage:off-zone-lifelink",
            ),
        )
        self.assertEqual(24, engine.state.players["A"].life)

        wither = self.permanent(
            engine, "A", "Boggart Ram-Gang", ref="a-graveyard-wither"
        )
        lki_target = self.token(engine, "B", "Wither LKI Target", toughness=5)
        wither_prepared = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    wither,
                    lki_target,
                    1,
                    event_id="damage:wither-lki",
                ),
            ),
        )
        engine.move_card(
            wither.object_id,
            "graveyard",
            reason="off-zone keyword witness",
            log=False,
            semantic_events=False,
        )
        commit_prepared_damage_batch(engine, wither_prepared)
        self.assertEqual(1, lki_target.counters["-1/-1"])

        target = self.token(engine, "B", "Off-zone Target", toughness=5)
        self.commit(
            engine,
            self.proposal(
                engine,
                wither,
                target,
                2,
                event_id="damage:off-zone-wither",
            ),
        )
        self.assertEqual(2, target.counters["-1/-1"])
        self.assertEqual(0, target.marked_damage)

        infect = self.permanent(
            engine, "A", "Phyrexian Crusader", ref="a-graveyard-infect"
        )
        engine.move_card(
            infect.object_id,
            "graveyard",
            reason="off-zone infect witness",
            log=False,
            semantic_events=False,
        )
        self.commit(
            engine,
            self.proposal(
                engine,
                infect,
                "B",
                1,
                event_id="damage:off-zone-infect",
            ),
        )
        self.assertEqual(4, engine.state.players["B"].poison)

    def test_multiple_sources_and_duplicate_keyword_instances_are_distinct(self):
        engine = self.session(702_150_002).engine
        engine.state.players["A"].life = 20
        first = self.token(
            engine,
            "A",
            "Duplicate Lifelink Source",
            keywords=("Lifelink", "Lifelink"),
        )
        second = self.token(
            engine, "A", "Second Lifelink Source", keywords=("Lifelink",)
        )
        result = self.commit(
            engine,
            self.proposal(
                engine, first, "B", 2, event_id="damage:lifelink-first"
            ),
            self.proposal(
                engine, second, "B", 3, event_id="damage:lifelink-second"
            ),
        )
        self.assertEqual(25, engine.state.players["A"].life)
        self.assertEqual(
            [2, 3], sorted(gain.amount for gain in result.lifelink_gains)
        )

        infect = self.token(
            engine,
            "A",
            "Duplicate Infect Source",
            keywords=("Infect", "Infect"),
        )
        wither = self.token(
            engine,
            "A",
            "Duplicate Wither Source",
            keywords=("Wither", "Wither"),
        )
        infect_target = self.token(
            engine, "B", "Duplicate Infect Target", toughness=5
        )
        wither_target = self.token(
            engine, "B", "Duplicate Wither Target", toughness=5
        )
        self.commit(
            engine,
            self.proposal(
                engine,
                infect,
                infect_target,
                2,
                event_id="damage:duplicate-infect",
            ),
            self.proposal(
                engine,
                wither,
                wither_target,
                2,
                event_id="damage:duplicate-wither",
            ),
        )
        self.assertEqual(2, infect_target.counters["-1/-1"])
        self.assertEqual(2, wither_target.counters["-1/-1"])

    def test_toxic_is_additional_only_for_creature_combat_damage_to_player(self):
        engine = self.session(120_300_004).engine
        source = self.token(
            engine,
            "A",
            "Toxic Source",
            keywords=("Toxic",),
            toxic_values=(2,),
        )
        life_before = engine.state.players["B"].life

        self.commit(
            engine,
            self.proposal(
                engine,
                source,
                "B",
                3,
                event_id="damage:toxic-combat",
                combat=True,
            ),
        )
        self.assertEqual(life_before - 3, engine.state.players["B"].life)
        self.assertEqual(2, engine.state.players["B"].poison)

        self.commit(
            engine,
            self.proposal(
                engine,
                source,
                "B",
                1,
                event_id="damage:toxic-noncombat",
                combat=False,
            ),
        )
        self.assertEqual(2, engine.state.players["B"].poison)

    def test_multiple_toxic_abilities_are_cumulative(self):
        engine = self.session(120_300_005).engine
        source = self.token(
            engine,
            "A",
            "Multiple Toxic Source",
            keywords=("Toxic",),
            toxic_values=(1, 2),
        )
        source.temporary_keywords.append("Toxic 3")

        self.commit(
            engine,
            self.proposal(
                engine,
                source,
                "B",
                1,
                event_id="damage:multiple-toxic",
                combat=True,
            ),
        )
        self.assertEqual(6, engine.state.players["B"].poison)

    def test_toxic_uses_pinned_fragment_not_runtime_oracle_text(self):
        engine = self.session(120_300_105).engine
        source = self.token(
            engine,
            "A",
            "Pinned Toxic Source",
            keywords=("Toxic",),
            oracle_text="Toxic 99",
            toxic_values=(2,),
        )

        self.commit(
            engine,
            self.proposal(
                engine,
                source,
                "B",
                1,
                event_id="damage:pinned-toxic",
                combat=True,
            ),
        )

        self.assertEqual(2, engine.state.players["B"].poison)

    def test_four_player_keyword_results_keep_source_attribution(self):
        engine = self.session(120_300_012, players=4).engine
        source_a = self.token(
            engine,
            "A",
            "A Infect Toxic Lifelink Source",
            keywords=("Infect", "Lifelink", "Toxic"),
            toxic_values=(1,),
        )
        source_c = self.token(
            engine,
            "C",
            "C Toxic Lifelink Source",
            keywords=("Lifelink", "Toxic"),
            toxic_values=(2,),
        )
        engine.state.players["A"].life = 20
        engine.state.players["C"].life = 20

        result = self.commit(
            engine,
            self.proposal(
                engine,
                source_a,
                "B",
                2,
                event_id="damage:four-player-keyword-a",
                combat=True,
            ),
            self.proposal(
                engine,
                source_c,
                "D",
                3,
                event_id="damage:four-player-keyword-c",
                combat=True,
            ),
        )

        self.assertEqual(3, engine.state.players["B"].poison)
        self.assertEqual(2, engine.state.players["D"].poison)
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(37, engine.state.players["D"].life)
        self.assertEqual(22, engine.state.players["A"].life)
        self.assertEqual(23, engine.state.players["C"].life)
        poison_attribution = {
            (event.player, event.cause, event.source_controller)
            for event in result.result_events
            if event.kind == "counter.place"
            and event.counter_name == "poison"
        }
        self.assertEqual(
            {
                ("B", "infect", "A"),
                ("B", "toxic", "A"),
                ("D", "toxic", "C"),
            },
            poison_attribution,
        )

    def test_unresolved_toxic_value_fails_before_mutation(self):
        engine = self.session(120_300_006).engine
        source = self.token(
            engine, "A", "Unresolved Toxic Source", keywords=("Toxic",)
        )
        before = authoritative_state_hash(engine.state)
        proposal = self.proposal(
            engine,
            source,
            "B",
            1,
            event_id="damage:unresolved-toxic",
            combat=True,
        )

        with self.assertRaisesRegex(DamageError, "unresolved total toxic"):
            prepare_damage_batch(engine, (proposal,))
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_prevented_infect_and_toxic_create_no_counter_results(self):
        engine = self.session(120_300_010).engine
        source = self.token(
            engine,
            "A",
            "Prevented Infect Toxic Source",
            keywords=("Infect", "Toxic"),
            toxic_values=(2,),
        )
        engine.state.players["B"].stats[
            "protection_from_everything_until_next_turn"
        ] = True
        life_before = engine.state.players["B"].life

        result = self.commit(
            engine,
            self.proposal(
                engine,
                source,
                "B",
                3,
                event_id="damage:prevented-infect-toxic",
                combat=True,
            ),
        )

        self.assertEqual(0, result.events[0].dealt_amount)
        self.assertEqual(3, result.events[0].prevented_amount)
        self.assertEqual((), result.result_events)
        self.assertEqual(0, engine.state.players["B"].poison)
        self.assertEqual(life_before, engine.state.players["B"].life)

    def test_multitype_permanent_receives_every_applicable_result(self):
        engine = self.session(120_300_007).engine
        source = self.token(engine, "A", "Infect Source", keywords=("Infect",))
        target = self.token(
            engine,
            "B",
            "Every Damageable Type",
            type_line="Token Creature Planeswalker Battle — Siege",
            toughness=8,
            loyalty=5,
            defense=4,
            battle_protector="A",
        )

        self.commit(
            engine,
            self.proposal(
                engine, source, target, 2, event_id="damage:multitype"
            ),
        )

        self.assertEqual(2, target.counters["-1/-1"])
        self.assertEqual(3, target.counters["loyalty"])
        self.assertEqual(2, target.counters["defense"])
        self.assertEqual(0, target.marked_damage)

    def test_damage_counter_results_use_canonical_typed_owners(self):
        engine = self.session(120_300_027).engine
        source = self.token(engine, "A", "Infect Source", keywords=("Infect",))
        toxic_source = self.token(
            engine,
            "A",
            "Toxic Source",
            keywords=("Toxic",),
            toxic_values=(2,),
        )
        wither_source = self.token(
            engine,
            "A",
            "Wither Source",
            keywords=("Wither",),
        )
        target = self.token(
            engine,
            "B",
            "Every Damageable Type",
            type_line="Token Creature Planeswalker Battle — Siege",
            toughness=8,
            loyalty=5,
            defense=4,
            battle_protector="A",
        )
        wither_target = self.token(
            engine,
            "B",
            "Wither Counter Subject",
            toughness=8,
        )
        prepared_damage = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source,
                    target,
                    2,
                    event_id="damage:typed-counter-owners",
                ),
                self.proposal(
                    engine,
                    toxic_source,
                    "B",
                    1,
                    event_id="damage:typed-player-counter-owner",
                    combat=True,
                ),
                self.proposal(
                    engine,
                    wither_source,
                    wither_target,
                    1,
                    event_id="damage:typed-wither-counter-owner",
                ),
            ),
        )
        prepared_results = PreparedDamageResults(
            events=prepared_damage.result_events,
            effects=prepared_damage.result_effects,
            journal=prepared_damage.result_journal,
        )

        plan = plan_damage_result_commit(engine, prepared_results)

        self.assertIsInstance(
            plan.counter_placements,
            CounterPlacementCommitPlan,
        )
        self.assertIsInstance(plan.counter_removals, CounterRemovalPlan)
        self.assertEqual(
            {"-1/-1", "poison"},
            {row.counter_name for row in plan.counter_placements.rows},
        )
        self.assertEqual(
            ("defense", "loyalty"),
            tuple(
                removal.counter_name
                for removal in plan.counter_removals.removals
            ),
        )
        self.assertEqual(0, target.counters.get("-1/-1", 0))
        self.assertEqual(0, wither_target.counters.get("-1/-1", 0))
        self.assertEqual(5, target.counters["loyalty"])
        self.assertEqual(4, target.counters["defense"])
        self.assertEqual(0, engine.state.players["B"].poison)

        commit_damage_result_plan(engine, plan)

        self.assertEqual(2, target.counters["-1/-1"])
        self.assertEqual(1, wither_target.counters["-1/-1"])
        self.assertEqual(3, target.counters["loyalty"])
        self.assertEqual(2, target.counters["defense"])
        self.assertEqual(2, engine.state.players["B"].poison)

    def test_stale_damage_counter_subject_rolls_back_every_result(self):
        engine = self.session(120_300_028).engine
        source = self.token(
            engine,
            "A",
            "Infect Lifelink Source",
            keywords=("Infect", "Lifelink"),
        )
        target = self.token(engine, "B", "Counter Subject", toughness=8)
        engine.state.players["A"].life = 20
        prepared_damage = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source,
                    target,
                    2,
                    event_id="damage:stale-counter-owner",
                ),
            ),
        )
        plan = plan_damage_result_commit(
            engine,
            PreparedDamageResults(
                events=prepared_damage.result_events,
                effects=prepared_damage.result_effects,
                journal=prepared_damage.result_journal,
            ),
        )
        target.zone_change_counter += 1

        with self.assertRaisesRegex(ValueError, "changed object identity"):
            commit_damage_result_plan(engine, plan)

        self.assertEqual(20, engine.state.players["A"].life)
        self.assertNotIn("-1/-1", target.counters)

    def test_damage_counter_replacement_is_not_rediscovered_at_commit(self):
        engine = self.session(120_300_029).engine
        source = self.token(engine, "A", "Infect Source", keywords=("Infect",))
        target = self.token(engine, "B", "Counter Subject", toughness=8)
        damage = self.proposal(
            engine,
            source,
            target,
            2,
            event_id="damage:single-counter-replacement",
        ).event()
        replacement = result_effect(
            "double-counter-result",
            "counter.place",
            {"op": "multiply", "field": "amount", "factor": 2},
        )

        prepared = prepare_damage_results(
            engine,
            (damage,),
            effects=(replacement,),
        )
        plan = plan_damage_result_commit(engine, prepared)

        self.assertEqual(4, plan.counter_placements.rows[0].amount)
        commit_damage_result_plan(engine, plan)
        self.assertEqual(4, target.counters["-1/-1"])

    def test_resolved_counter_owner_rejects_nonplacement_event(self):
        engine = self.session(120_300_030).engine
        invalid = ReplaceableEvent(
            event_id="not-a-counter-placement",
            kind="life.change",
            affected_player="B",
            payload={
                "target_kind": "player",
                "player": "B",
                "direction": "loss",
                "amount": 1,
                "requested_amount": 1,
            },
        )

        with self.assertRaisesRegex(
            CounterPlacementError,
            "resolved placement leaves",
        ):
            plan_resolved_counter_placement_commit(engine, (invalid,))

    def test_damage_counter_owner_mutants_are_killed(self):
        engine = self.session(120_300_031).engine
        source = self.token(engine, "A", "Infect Source", keywords=("Infect",))
        target = self.token(
            engine,
            "B",
            "Every Damageable Type",
            type_line="Token Creature Planeswalker Battle — Siege",
            toughness=8,
            loyalty=5,
            defense=4,
            battle_protector="A",
        )
        invocation = 0

        def assert_typed_owner_results() -> None:
            nonlocal invocation
            invocation += 1
            target.counters.clear()
            target.counters.update({"loyalty": 5, "defense": 4})
            prepared_damage = prepare_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source,
                        target,
                        2,
                        event_id=f"damage:counter-owner-mutation:{invocation}",
                    ),
                ),
            )
            plan = plan_damage_result_commit(
                engine,
                PreparedDamageResults(
                    events=prepared_damage.result_events,
                    effects=prepared_damage.result_effects,
                    journal=prepared_damage.result_journal,
                ),
            )
            commit_damage_result_plan(engine, plan)
            self.assertEqual(2, target.counters["-1/-1"])
            self.assertEqual(3, target.counters["loyalty"])
            self.assertEqual(2, target.counters["defense"])

        assert_typed_owner_results()
        with patch.object(
            damage_results_module,
            "commit_counter_placement_plan",
            lambda *_args, **_kwargs: (),
        ):
            with self.assertRaises((AssertionError, KeyError)):
                assert_typed_owner_results()
        with patch.object(
            damage_results_module,
            "commit_counter_removals",
            lambda *_args, **_kwargs: (),
        ):
            with self.assertRaises(AssertionError):
                assert_typed_owner_results()

    def test_wither_damage_still_records_deathtouch_result(self):
        engine = self.session(120_300_008).engine
        source = self.token(
            engine,
            "A",
            "Wither Source",
            keywords=("Wither", "Deathtouch"),
        )
        target = self.token(engine, "B", "Target", toughness=8)

        self.commit(
            engine,
            self.proposal(
                engine,
                source,
                target,
                1,
                event_id="damage:wither-deathtouch",
            ),
        )

        self.assertEqual(1, target.counters["-1/-1"])
        self.assertEqual(0, target.marked_damage)
        self.assertTrue(target.deathtouch_damage)

    def test_noncombat_deathtouch_uses_pinned_source_lki(self):
        engine = self.session(120_300_018).engine
        source = self.token(
            engine,
            "A",
            "Departing Deathtouch Source",
            keywords=("Deathtouch", "DEATHTOUCH"),
        )
        target = self.token(engine, "B", "LKI Target", toughness=8)
        proposal = self.proposal(
            engine,
            source,
            target,
            1,
            event_id="damage:deathtouch-lki",
            combat=False,
        )
        source.controller = "B"
        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )

        event = proposal.event()
        result = self.commit(engine, proposal)

        self.assertTrue(event.payload["deathtouch"])
        self.assertEqual("A", event.payload["source_controller"])
        self.assertEqual(["deathtouch"], list(event.payload["source_keywords"]))
        self.assertTrue(target.deathtouch_damage)
        self.assertIn(
            "damage.deathtouch",
            {record.kind for record in result.result_events},
        )

    def test_legacy_event_flag_cannot_grant_deathtouch(self):
        engine = self.session(120_300_019).engine
        source = self.token(engine, "A", "Ordinary Source")
        target = self.token(engine, "B", "Ordinary Target", toughness=8)
        event = self.proposal(
            engine,
            source,
            target,
            1,
            event_id="damage:forged-deathtouch-flag",
        ).event()
        forged = ReplaceableEvent(
            event_id=event.event_id,
            kind=event.kind,
            affected_player=event.affected_player,
            affected_object=event.affected_object,
            payload={**dict(event.payload), "deathtouch": True},
        )

        prepared = prepare_damage_results(engine, (forged,), effects=())
        plan = plan_damage_result_commit(engine, prepared)
        commit = commit_damage_result_plan(engine, plan)

        self.assertFalse(target.deathtouch_damage)
        self.assertNotIn(
            "damage.deathtouch",
            {record.kind for record in commit.records},
        )

    def test_containing_result_replacement_precedes_life_gain_replacement(self):
        engine = self.session(120_400_001).engine
        source = self.token(engine, "A", "Damage Source")
        self.token(engine, "B", "Worship Creature")
        engine.state.players["B"].life = 5
        damage = self.proposal(
            engine, source, "B", 5, event_id="damage:worship-example"
        ).event()
        gain = ReplaceableEvent(
            event_id="damage:worship-example/gain",
            kind="life.change",
            affected_player="B",
            payload={
                "target_kind": "player",
                "player": "B",
                "direction": "gain",
                "amount": 5,
                "requested_amount": 5,
                "source": source.ref,
                "source_controller": "A",
                "cause": "damage-replacement",
            },
        )
        damage = ReplaceableEvent(
            event_id=damage.event_id,
            kind=damage.kind,
            affected_player=damage.affected_player,
            affected_object=damage.affected_object,
            payload=damage.payload,
            children=(gain,),
        )
        worship = result_effect(
            "worship",
            "damage.results",
            {"op": "cap_result_life_loss", "minimum": 1},
            conditions={
                "subject_kind": {"eq": "player"},
                "life_loss_amount": {"not_in": [0]},
                "life_after_without_replacement": {"lt": 1},
                "controls_creature": {"eq": True},
            },
        )
        boon = result_effect(
            "boon",
            "life.change",
            {"op": "multiply", "field": "amount", "factor": 2},
            conditions={"direction": {"eq": "gain"}},
        )

        prepared = prepare_damage_results(
            engine, (damage,), effects=(boon, worship)
        )
        self.assertEqual((1,), prepared.journal[0].path)
        plan = plan_damage_result_commit(engine, prepared)
        commit_damage_result_plan(engine, plan)

        self.assertEqual(10, engine.state.players["B"].life)
        self.assertEqual("boon", prepared.journal[0].effect_id)

    def test_life_floor_caps_damage_result_without_an_offsetting_gain(self):
        engine = self.session(120_400_002).engine
        source = self.token(engine, "A", "Damage Source")
        self.token(engine, "B", "Worship Creature")
        engine.state.players["B"].life = 5
        damage = self.proposal(
            engine, source, "B", 5, event_id="damage:worship-floor"
        ).event()
        worship = result_effect(
            "worship",
            "damage.results",
            {"op": "cap_result_life_loss", "minimum": 1},
            conditions={
                "subject_kind": {"eq": "player"},
                "life_loss_amount": {"not_in": [0]},
                "life_after_without_replacement": {"lt": 1},
                "controls_creature": {"eq": True},
            },
        )

        prepared = prepare_damage_results(engine, (damage,), effects=(worship,))
        plan = plan_damage_result_commit(engine, prepared)
        commit_damage_result_plan(engine, plan)

        self.assertEqual(1, engine.state.players["B"].life)
        self.assertEqual(4, plan.records[0].amount)

    def test_boon_reflection_doubles_lifelink_result(self):
        engine = self.session(120_400_005).engine
        self.permanent(engine, "A", "Boon Reflection", ref="a-boon")
        source = self.token(
            engine, "A", "Lifelink Source", keywords=("Lifelink",)
        )
        engine.state.players["A"].life = 20

        prepared = prepare_damage_batch(
            engine,
            (self.proposal(
                engine, source, "B", 3, event_id="damage:boon-lifelink"
            ),),
        )
        result = commit_prepared_damage_batch(engine, prepared)

        self.assertEqual(26, engine.state.players["A"].life)
        self.assertEqual(6, result.lifelink_gains[0].amount)
        self.assertEqual(1, len(prepared.result_journal))
        self.assertIn(
            "replacement.life.gain.multiplier.v1",
            prepared.result_journal[0].effect_id,
        )

    def test_boon_reflection_doubles_prevention_aftermath_life_gain(self):
        engine = self.session(120_400_009).engine
        self.permanent(engine, "B", "Boon Reflection", ref="b-boon")
        source = self.token(engine, "A", "Damage Source")
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="reverse-damage-with-boon",
                source_id="fixture:reverse-damage",
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.AMOUNT,
                remaining=2,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                aftermath=(
                    GainLifePreventionAftermath(player="B", per_prevented=1),
                ),
            )
        )

        result = self.commit(
            engine,
            self.proposal(
                engine,
                source,
                "B",
                2,
                event_id="damage:boon-prevention-aftermath",
            ),
        )

        self.assertEqual(44, engine.state.players["B"].life)
        self.assertEqual(4, result.aftermath_events[0].applied_amount)
        self.assertTrue(
            any(
                event.code == "replacement.apply"
                and "life.gain.multiplier"
                in str(event.details.get("effect_id"))
                for event in engine.state.events
            )
        )

    def test_life_gain_multiplier_ignores_losses_and_rejects_bad_shape(self):
        handler = LifeGainMultiplierHandler()
        descriptor = {
            "handler_id": "replacement.life.gain.multiplier.v1",
            "schema_version": 1,
            "event": "life.change",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"multiplier": 2},
        }
        effect = handler.replacement_effect(
            descriptor,
            LifeReplacementSourceContext(
                source_ref="boon-negative-witness",
                source_controller="A",
            ),
        )
        loss = ReplaceableEvent(
            event_id="life:loss:boon-negative",
            kind="life.change",
            affected_player="A",
            payload={"direction": "loss", "amount": 3},
        )

        resolved = resolve_replacements(loss, (effect,), selections=())
        self.assertEqual(loss, resolved)

        malformed = {
            **descriptor,
            "modification": {"multiplier": 1},
        }
        with self.assertRaisesRegex(SemanticNodeError, "at least 2"):
            handler.replacement_effect(
                malformed,
                LifeReplacementSourceContext(
                    source_ref="boon-negative-witness",
                    source_controller="A",
                ),
            )

    def test_worship_caps_damage_result_at_one(self):
        engine = self.session(120_400_006).engine
        self.permanent(engine, "B", "Worship", ref="b-worship")
        self.token(engine, "B", "Worship Creature")
        source = self.token(engine, "A", "Damage Source")
        engine.state.players["B"].life = 5

        result = self.commit(
            engine,
            self.proposal(
                engine, source, "B", 5, event_id="damage:worship-semantic"
            ),
        )

        self.assertEqual(1, engine.state.players["B"].life)
        self.assertEqual(5, result.events[0].dealt_amount)
        life_loss = next(
            event
            for event in result.result_events
            if event.kind == "life.change" and event.direction == "loss"
        )
        self.assertEqual(4, life_loss.amount)

    def test_worship_and_boon_follow_containing_event_order(self):
        engine = self.session(120_400_007).engine
        self.permanent(engine, "B", "Worship", ref="b-worship")
        self.permanent(engine, "B", "Boon Reflection", ref="b-boon")
        self.token(engine, "B", "Worship Creature")
        source = self.token(engine, "A", "Damage Source")
        engine.state.players["B"].life = 5
        damage = self.proposal(
            engine, source, "B", 5, event_id="damage:official-result-order"
        ).event()
        gain = ReplaceableEvent(
            event_id="damage:official-result-order/gain",
            kind="life.change",
            affected_player="B",
            payload={
                "target_kind": "player",
                "player": "B",
                "direction": "gain",
                "amount": 5,
                "requested_amount": 5,
                "source": source.ref,
                "source_controller": "A",
                "cause": "damage-replacement",
            },
        )
        damage = ReplaceableEvent(
            event_id=damage.event_id,
            kind=damage.kind,
            affected_player=damage.affected_player,
            affected_object=damage.affected_object,
            payload=damage.payload,
            children=(gain,),
        )
        sources = engine._semantic_event_sources()
        effects = collect_damage_result_replacement_effects(
            engine,
            sources=sources,
            source_zones={card.object_id: card.zone for card in sources},
        )

        prepared = prepare_damage_results(engine, (damage,), effects=effects)
        plan = plan_damage_result_commit(engine, prepared)
        commit_damage_result_plan(engine, plan)

        self.assertEqual(10, engine.state.players["B"].life)
        self.assertEqual(1, len(prepared.journal))
        self.assertIn("life.gain.multiplier", prepared.journal[0].effect_id)
        self.assertNotIn(
            "damage.result.life_floor",
            {entry.effect_id for entry in prepared.journal},
        )

    def test_four_player_result_choices_follow_apnap_and_replay_exactly(self):
        engine = self.session(120_400_003, players=4).engine
        source = self.token(engine, "A", "Damage Source")
        events = (
            self.proposal(
                engine, source, "B", 2, event_id="damage:result-choice-b"
            ).event(),
            self.proposal(
                engine, source, "C", 2, event_id="damage:result-choice-c"
            ).event(),
        )
        effects = (
            result_effect(
                "add-one",
                "life.change",
                {"op": "add", "field": "amount", "amount": 1},
                conditions={"direction": {"eq": "loss"}},
            ),
            result_effect(
                "double",
                "life.change",
                {"op": "multiply", "field": "amount", "factor": 2},
                conditions={"direction": {"eq": "loss"}},
            ),
        )

        first = prepare_damage_results(engine, events, effects=effects)
        self.assertIsNotNone(first.pending)
        assert first.pending is not None
        self.assertEqual("B", first.pending.choice.chooser)
        second = prepare_damage_results(
            engine, events, effects=effects, selections=("add-one",)
        )
        self.assertIsNotNone(second.pending)
        assert second.pending is not None
        self.assertEqual("C", second.pending.choice.chooser)
        selections = ("add-one", "double")
        complete = prepare_damage_results(
            engine, events, effects=effects, selections=selections
        )
        replayed = prepare_damage_results(
            engine, events, effects=tuple(reversed(effects)), selections=selections
        )

        self.assertIsNone(complete.pending)
        self.assertEqual(complete.events, replayed.events)
        self.assertEqual(complete.journal, replayed.journal)
        self.assertEqual(["B", "B", "C", "C"], [
            entry.chooser for entry in complete.journal
        ])

    def test_keyword_damage_results_replay_from_combat_assignment(self):
        session = self.session(120_300_011)
        engine = session.engine
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        infect = self.token(
            engine,
            "A",
            "Infect Toxic Lifelink Attacker",
            power=4,
            toughness=4,
            keywords=("Infect", "Lifelink", "Toxic"),
            toxic_values=(2,),
        )
        wither = self.token(
            engine,
            "A",
            "Wither Attacker",
            power=2,
            toughness=4,
            keywords=("Wither",),
        )
        trampler = self.token(
            engine,
            "A",
            "Ordinary Trample Attacker",
            power=2,
            toughness=4,
            keywords=("Trample",),
        )
        wither_blocker = self.token(
            engine, "B", "Wither Blocker", power=0, toughness=5
        )
        trample_blocker = self.token(
            engine, "B", "Trample Blocker", power=0, toughness=1
        )
        infect.attacking = "B"
        wither.attacking = "B"
        trampler.attacking = "B"
        wither_blocker.blocking = wither.object_id
        trample_blocker.blocking = trampler.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={
                infect.object_id: "B",
                wither.object_id: "B",
                trampler.object_id: "B",
            },
            defending_players=["B"],
            blockers={
                wither.object_id: [wither_blocker.object_id],
                trampler.object_id: [trample_blocker.object_id],
            },
        )
        engine._begin_combat_damage()
        self.assertEqual("combat.damage", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {"source": infect.ref, "target": "B", "amount": 4},
                    {
                        "source": wither.ref,
                        "target": wither_blocker.ref,
                        "amount": 2,
                    },
                    {
                        "source": trampler.ref,
                        "target": trample_blocker.ref,
                        "amount": 1,
                    },
                    {"source": trampler.ref, "target": "B", "amount": 1},
                ],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(6, engine.state.players["B"].poison)
        self.assertEqual(39, engine.state.players["B"].life)
        self.assertEqual(44, engine.state.players["A"].life)
        self.assertEqual(2, wither_blocker.counters["-1/-1"])

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "keyword-damage-result-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_damage_result_replacement_choice_is_private_and_replays(self):
        session = self.session(120_400_008)
        engine = session.engine
        self.permanent(engine, "A", "Boon Reflection", ref="a-boon-one")
        self.permanent(engine, "A", "Boon Reflection", ref="a-boon-two")
        source = self.token(
            engine, "A", "Lifelink Source", keywords=("Lifelink",)
        )
        engine.state.players["A"].life = 20
        program = SemanticProgram(
            key="test:damage-result-replacement-replay",
            label="Replay a damage-result replacement choice",
            effects=[
                {
                    "op": "damage",
                    "source": "$source",
                    "target": "B",
                    "amount": 3,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="damage-result-replacement-replay",
                ref="S-damage-result-replacement-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                source_object_id=source.object_id,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)

        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        self.assertIsNotNone(projected_a)
        self.assertIsNone(projector._decision("pilot:B"))
        serialized = json.dumps(projected_a, sort_keys=True)
        for forbidden in (
            "replacement_batch",
            "replacement_effects",
            "life_before",
            "damage.results",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn(source.object_id, serialized)

        selected = projected_a["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(32, engine.state.players["A"].life)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "damage-result-replacement-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(3, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_invalid_result_leaf_keeps_the_whole_batch_atomic(self):
        engine = self.session(120_400_004).engine
        source = self.token(engine, "A", "Damage Source")
        prepared = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine, source, "B", 2, event_id="damage:atomic"
                ),
            ),
        )
        root = prepared.result_events[0]
        bad_child = ReplaceableEvent(
            event_id="damage.result:unsupported",
            kind="unsupported.result",
            affected_player="B",
            payload={"amount": 1, "requested_amount": 1},
        )
        invalid = PreparedDamageResults(
            events=(
                ReplaceableEvent(
                    event_id=root.event_id,
                    kind=root.kind,
                    affected_player=root.affected_player,
                    affected_object=root.affected_object,
                    payload=root.payload,
                    children=(*root.children, bad_child),
                ),
            ),
            effects=(),
            journal=(),
        )
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(ValueError, "Unsupported resolved"):
            plan_damage_result_commit(engine, invalid)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_stale_life_prevents_counter_and_life_commit_atomically(self):
        engine = self.session(120_400_006).engine
        source = self.token(
            engine,
            "A",
            "Infect Lifelink Source",
            keywords=("Infect", "Lifelink"),
        )
        engine.state.players["A"].life = 20
        prepared_damage = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source,
                    "B",
                    2,
                    event_id="damage:life-counter-atomic",
                ),
            ),
        )
        plan = plan_damage_result_commit(
            engine,
            PreparedDamageResults(
                events=prepared_damage.result_events,
                effects=prepared_damage.result_effects,
                journal=prepared_damage.result_journal,
            ),
        )
        engine.state.players["A"].life = 19

        with self.assertRaisesRegex(ValueError, "Life plan is stale"):
            commit_damage_result_plan(engine, plan)
        self.assertEqual(19, engine.state.players["A"].life)
        self.assertEqual(0, engine.state.players["B"].poison)

    def test_damage_results_property_1000_deterministic_transitions(self):
        engine = self.session(120_400_005).engine
        for index in range(property_transitions()):
            amount = index % 7 + 1
            infect = index % 2 == 0
            lifelink = index % 3 == 0
            toxic = index % 5 == 0
            keywords = [
                keyword
                for enabled, keyword in (
                    (infect, "infect"),
                    (lifelink, "lifelink"),
                    (toxic, "toxic"),
                )
                if enabled
            ]
            event = ReplaceableEvent(
                event_id=f"damage:property:{index}",
                kind="damage",
                affected_player="B",
                payload={
                    "amount": amount,
                    "target_kind": "player",
                    "target": "B",
                    "source": "S-property",
                    "source_controller": "A",
                    "source_logical_object_id": "logical:property",
                    "source_types": ["creature"],
                    "source_keywords": keywords,
                    "source_toxic_value": 2 if toxic else 0,
                    "combat": toxic,
                },
            )
            before = (
                engine.state.players["A"].life,
                engine.state.players["B"].life,
                engine.state.players["B"].poison,
            )
            prepared = prepare_damage_results(engine, (event,), effects=())
            plan = plan_damage_result_commit(engine, prepared)
            self.assertEqual(
                before,
                (
                    engine.state.players["A"].life,
                    engine.state.players["B"].life,
                    engine.state.players["B"].poison,
                ),
            )
            commit_damage_result_plan(engine, plan)
            self.assertEqual(
                before[0] + (amount if lifelink else 0),
                engine.state.players["A"].life,
            )
            self.assertEqual(
                before[1] - (0 if infect else amount),
                engine.state.players["B"].life,
            )
            self.assertEqual(
                before[2] + (amount if infect else 0) + (2 if toxic else 0),
                engine.state.players["B"].poison,
            )


if __name__ == "__main__":
    unittest.main()
