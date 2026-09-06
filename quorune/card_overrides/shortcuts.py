from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import CommanderEngine

SOULTRADER = "ace86e56-efde-4eb7-8815-71456a4c3abe"
GRAVECRAWLER = "09ff28b1-b6c9-48e6-b12e-2f0e644f709f"
ZULAPORT = "76b003e0-15af-4f22-bdf2-1ade5430964a"
MISHRA = "d3438037-3efd-4ce0-88ec-6d48ab521992"
GONTI_HEART = "69428825-3c40-486d-b051-14e97a598ce6"


def _controlled(engine: "CommanderEngine", seat: str, oracle_id: str):
    return [
        engine.state.cards[object_id]
        for object_id in engine.state.players[seat].zones["battlefield"]
        if engine.state.cards[object_id].controller == seat
        and engine.state.cards[object_id].oracle_id == oracle_id
    ]


def execute_shortcut(
    engine: "CommanderEngine",
    seat: str,
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a demonstrated loop and derive its aggregate effects.

    The proposal supplies existing action IDs and a stop condition, never raw
    life/zone/resource mutations.
    """

    signature = str(proposal.get("signature") or "")
    sequence = [str(value) for value in proposal.get("sequence") or []]
    repeat_count = int(proposal.get("repeat_count", 0))
    if repeat_count <= 0:
        raise ValueError("Shortcut repeat_count must be positive")
    if not sequence:
        raise ValueError("Shortcut requires a demonstrated legal action sequence")
    opponents = [value for value in engine.active_seats if value != seat]
    responses = {
        str(item.get("seat")): str(item.get("response"))
        for item in proposal.get("opponent_responses") or []
        if isinstance(item, Mapping)
    }
    if any(responses.get(opponent) != "pass" for opponent in opponents):
        raise ValueError("Every active opponent must receive priority and pass before shortcut acceptance")
    if signature == "soultrader-gravecrawler-zulaport":
        soultraders = _controlled(engine, seat, SOULTRADER)
        cutthroats = _controlled(engine, seat, ZULAPORT)
        crawlers = [
            engine.state.cards[object_id]
            for object_id in engine.state.players[seat].zones["graveyard"]
            if engine.state.cards[object_id].oracle_id == GRAVECRAWLER
        ]
        if not soultraders or not cutthroats or not crawlers:
            raise ValueError("The demonstrated Soultrader loop pieces are not in the required zones")
        crawler = crawlers[0]
        soultrader = soultraders[0]
        expected = [
            f"cast:{crawler.ref}",
            f"activate:{soultrader.ref}:ab1",
        ]
        if sequence != expected:
            raise ValueError("Shortcut sequence does not match the demonstrated legal iteration")
        spend_context = "creature_spell"
        if engine._spendable_mana_pool(seat, spend_context)["B"] < 1:
            raise ValueError("The first Gravecrawler cast requires one available black mana")
        engine._apply_mana_spend(seat, {"B": 1}, spend_context)
        for opponent in opponents:
            engine.state.players[opponent].life -= repeat_count
        # Soultrader's life payment and Zulaport's gain cancel for the
        # controller. Intermediate Treasure tokens pay later iterations; one
        # Treasure remains after the final sacrifice.
        treasure = engine.create_token(
            seat,
            name="Treasure",
            characteristics={
                "type_line": "Token Artifact — Treasure",
                "oracle_text": "{T}, Sacrifice this token: Add one mana of any color.",
                "activated_ability_profile": "tap_sac_any_color_mana_v1",
            },
            reason="validated deterministic shortcut",
        )[0]
        aggregate = {
            "controller_life_delta": 0,
            "opponent_life_delta": {
                opponent: -repeat_count for opponent in opponents
            },
            "black_mana_delta": -1,
            "ending_gravecrawler_zone": "graveyard",
            "treasure_created": treasure,
        }
    elif signature == "mishra-gonti-heart":
        if not _controlled(engine, seat, MISHRA) or not _controlled(
            engine, seat, GONTI_HEART
        ):
            raise ValueError("Mishra and Gonti's Aether Heart must be controlled")
        expected = ["trigger:mishra-warform:gonti-heart"]
        if sequence != expected:
            raise ValueError("Shortcut does not demonstrate the Mishra/Heart iteration")
        energy_gained = 4 * repeat_count
        engine.state.players[seat].energy += energy_gained
        extra_turn = False
        if proposal.get("take_extra_turn"):
            if engine.state.players[seat].energy < 8:
                raise ValueError("Gonti's Aether Heart requires eight energy")
            heart = _controlled(engine, seat, GONTI_HEART)[0]
            engine.state.players[seat].energy -= 8
            engine.move_card(
                heart.object_id,
                "exile",
                reason="Gonti's Aether Heart shortcut cost",
            )
            engine.schedule_extra_turn(seat, source=heart.ref)
            extra_turn = True
        aggregate = {
            "energy_gained": energy_gained,
            "energy_remaining": engine.state.players[seat].energy,
            "extra_turn_scheduled": extra_turn,
            "infinite": False,
        }
    else:
        raise ValueError(f"Unknown deterministic shortcut signature {signature!r}")
    semantic_versions = {
        key: engine.semantics.get(key).version
        for key in engine.semantics.keys()
        if engine.semantics.get(key)
        and engine.semantics.get(key).oracle_id
        in {SOULTRADER, GRAVECRAWLER, ZULAPORT, MISHRA, GONTI_HEART}
    }
    engine._log(
        seat,
        "loop.shortcut",
        f"{seat} applied {signature} for {repeat_count} iteration(s).",
        {
            "loop_signature": signature,
            "demonstrated_iteration": sequence,
            "repeat_count": repeat_count,
            "stop_condition": proposal.get("stop_condition"),
            "aggregate": aggregate,
            "semantic_versions": semantic_versions,
            "opponent_responses": list(proposal.get("opponent_responses") or []),
        },
        importance=3,
        changed_players=list(engine.active_seats),
    )
    engine._stabilize()
    return aggregate
