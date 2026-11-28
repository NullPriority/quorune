from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.errors import GameRuleError
from quorune.mana_restrictions import (
    ability_mana_spend_context, exile_play_mana_caster, exile_play_mana_spend_context,
    mana_restriction_allows, spell_mana_spend_context,
)
from quorune.opponent_library_exile import (
    LEGACY_FREE_EXILE_CAST_FIELD, PLAY_PERMISSIONS_FIELD, apply_opponent_library_exile_play,
    current_opponent_library_exile_play_permission, expire_opponent_library_exile_play_permissions,
    opponent_library_exile_spend_context,
)
from quorune.opponent_library_exile_model import (
    OPPONENT_LIBRARY_EXILE_PLAY_OPERATION, OpponentLibraryExileOccurrence,
    each_opponent_top_exile_effect, opponent_library_exile_play_effect,
    validate_each_opponent_top_exile, validate_opponent_library_exile_play,
)
from quorune.semantic_runtime.mana_spending import current_mana_spending_policy


class OpponentLibraryExileCodecTests(unittest.TestCase):
    def occurrence(self, *objects):
        return OpponentLibraryExileOccurrence("A", "B", tuple(
            ContinuousObjectIdentity(object_id, f"{object_id}@1") for object_id in objects
        ))

    def effect(self, *objects):
        return {**opponent_library_exile_play_effect(), "player": "A", "occurrence": self.occurrence(*objects).to_dict()}

    def test_closed_descriptor_and_exact_occurrence_round_trip(self):
        occurrence = self.occurrence("one", "two")
        self.assertEqual(occurrence, OpponentLibraryExileOccurrence.from_dict(occurrence.to_dict()))
        self.assertEqual({"effect_controller": "A", "opponent_library_exile": occurrence.to_dict()}, occurrence.event_context())
        validate_each_opponent_top_exile(each_opponent_top_exile_effect())
        self.assertEqual(occurrence, validate_opponent_library_exile_play(self.effect("one", "two"), actor="A"))
        with self.assertRaises(ValueError):
            validate_each_opponent_top_exile({**each_opponent_top_exile_effect(), "count": 2})
        with self.assertRaises(ValueError):
            validate_opponent_library_exile_play({**self.effect("one"), "player": "C"}, actor="C")

    def test_malformed_occurrence_rejects_empty_duplicate_and_incorrect_identities(self):
        initial = self.occurrence("one").to_dict()
        variants = (
            {**initial, "schema_version": True},
            {**initial, "library_owner": "A"},
            {**initial, "objects": []},
            {**initial, "objects": [*initial["objects"], *initial["objects"]]},
            {**initial, "objects": [{"object_id": "one", "logical_object_id": "two@1"}]},
            {**initial, "objects": [{"object_id": "one", "logical_object_id": "one@0"}]},
            {**initial, "objects": [{**initial["objects"][0], "name": "Injected identity"}]},
        )
        for altered in variants:
            with self.subTest(value=altered), self.assertRaises(ValueError):
                OpponentLibraryExileOccurrence.from_dict(altered)

    @staticmethod
    def host_with_cards():
        def card(object_id, *, face_down=False, owner="B", incarnation=1):
            return SimpleNamespace(
                owner=owner, zone="exile", face_down=face_down, is_card_object=True,
                object_id=object_id, logical_object_id=f"{object_id}@{incarnation}", ref=f"ref-{object_id}",
                annotations={}, known_to={"B"} if face_down else set(),
            )

        class Host:
            def __init__(self):
                self.state = SimpleNamespace(
                    players={seat: SimpleNamespace(turns_begun=1, zones={"exile": []}) for seat in "ABC"},
                    cards={},
                )
                self.logs = []

            def _require_seat(self, seat, **kwargs):
                if seat not in self.state.players:
                    raise GameRuleError("Unsupported seat")

            def _log(self, *args, **kwargs):
                self.logs.append((args, kwargs))

        host = Host()
        for value in (card("public"), card("hidden", face_down=True), card("stale", incarnation=2), card("wrong", owner="C")):
            host.state.cards[value.object_id] = value
            host.state.players[value.owner].zones["exile"].append(value.object_id)
        return host

    def apply(self, host, *objects):
        return apply_opponent_library_exile_play(
            host, self.effect(*objects), actor="A", operation=OPPONENT_LIBRARY_EXILE_PLAY_OPERATION, reason="codec witness",
        )

    def test_invalid_owner_rejects_atomically_before_valid_siblings_are_written(self):
        host = self.host_with_cards()
        before = deepcopy(host.state.cards)
        with self.assertRaisesRegex(GameRuleError, "original library owner"):
            self.apply(host, "public", "wrong")
        self.assertEqual(before, host.state.cards)
        self.assertEqual([], host.logs)

    def test_hidden_stale_and_independent_grants_keep_separate_authority(self):
        host = self.host_with_cards()
        public = host.state.cards["public"]
        hidden = host.state.cards["hidden"]
        legacy = {"player": "C", "zone": "exile", "turn_sequence": 1, "allow_spell": True}
        public.annotations["temporary_play_permission"] = legacy
        self.assertEqual(1, self.apply(host, "public", "hidden", "stale"))
        self.assertEqual(legacy, public.annotations["temporary_play_permission"])
        self.assertTrue(current_opponent_library_exile_play_permission(host.state, "A", public)["allow_land"])
        self.assertIsNone(current_opponent_library_exile_play_permission(host.state, "C", public))
        self.assertEqual({"B"}, hidden.known_to)
        self.assertIsNone(current_opponent_library_exile_play_permission(host.state, "A", hidden))
        self.assertNotIn(hidden.ref, repr(host.logs))
        self.assertNotIn(PLAY_PERMISSIONS_FIELD, host.state.cards["stale"].annotations)
        expire_opponent_library_exile_play_permissions(host.state, active_player="B")
        host.state.players["A"].turns_begun = 2
        self.assertIsNotNone(current_opponent_library_exile_play_permission(host.state, "A", public))
        self.assertEqual((public.object_id,), expire_opponent_library_exile_play_permissions(host.state, active_player="A"))
        self.assertIsNone(current_opponent_library_exile_play_permission(host.state, "A", public))
        self.assertEqual(legacy, public.annotations["temporary_play_permission"])
        self.assertEqual({"B"}, hidden.known_to)

    def test_spending_permission_retains_type_restrictions_and_actual_mana(self):
        host = self.host_with_cards()
        source = host.state.cards["public"]
        self.apply(host, "public")
        generic_context = spell_mana_spend_context("Artifact")
        scoped = opponent_library_exile_spend_context(host.state, "A", source, generic_context)
        self.assertEqual("A", exile_play_mana_caster(scoped))
        self.assertTrue(mana_restriction_allows("artifact_spell_only", scoped))
        self.assertFalse(mana_restriction_allows("creature_spell_only", scoped))
        with patch("quorune.semantic_runtime.mana_spending.controller_action_permissions", return_value=()):
            policy = current_mana_spending_policy(host, "A", scoped)
            _, spent = policy.pay({"W": 1, "C": 1}, {"C": 1, "R": 1}, {"W": 1, "C": 1})
            self.assertEqual({"W": 1, "C": 1}, {color: count for color, count in spent.items() if count})
            self.assertIsNone(current_mana_spending_policy(host, "B", scoped))
            self.assertIsNone(current_mana_spending_policy(host, "A", generic_context))
        source.logical_object_id = "public@3"
        self.assertEqual(generic_context, opponent_library_exile_spend_context(host.state, "A", source, generic_context))
        self.assertIsNone(exile_play_mana_caster(scoped + ";exile-play-caster=42"))
        with self.assertRaises(ValueError):
            exile_play_mana_spend_context(ability_mana_spend_context("Artifact"), "A")

    def test_paid_and_legacy_free_kickers_have_distinct_cost_option_ids(self):
        from quorune.rules.casting.option_expansion import expand_optional_mana_costs

        host = SimpleNamespace(_mana_vector=lambda values: dict(values or {}))
        card = SimpleNamespace(zone="exile")
        options = (
            {"id": "normal", "requirements": {"R": 1}},
            {"id": "without_mana_cost", "label": "Cast without paying its mana cost", "requirements": {"R": 0},
             LEGACY_FREE_EXILE_CAST_FIELD: 1},
        )
        schema = {"optional_costs": [{"id": "kicked", "kind": "kicker", "label": "Kicker {R}", "requirements": {"R": 1}}]}
        expanded = expand_optional_mana_costs(host, card, options, schema)
        self.assertEqual(4, len({option["id"] for option in expanded}))
        kicked = {option["id"]: option for option in expanded if option.get("kind") == "kicker"}
        self.assertEqual({"R": 2}, kicked["kicked"]["requirements"])
        self.assertEqual({"R": 1}, kicked["without_mana_cost+kicked"]["requirements"])
        self.assertEqual(1, kicked["without_mana_cost+kicked"][LEGACY_FREE_EXILE_CAST_FIELD])
