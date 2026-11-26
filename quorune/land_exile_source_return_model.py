"""One public exile may permit the original ability source to return."""

from collections.abc import Mapping


LAND_EXILE_RETURN_SCOPE = "targeted_exiled_land_source_return"
LAND_EXILE_RETURN_VERSION = 6
LAND_EXILE_RETURN_CAPABILITY = "resolution.effect.exiled_land_optional_source_return"
LAND_EXILE_RETURN_MECHANIC = "fixed-exiled-land-source-return"
_FIELDS = {"op", "schema_version", "player", "scope", "supplier", "source"}


def land_exile_source_return_instruction():
    return {
        "op": "exile_fixed_library_tops_and_cast", "schema_version": LAND_EXILE_RETURN_VERSION,
        "player": "$controller", "scope": LAND_EXILE_RETURN_SCOPE,
        "supplier": "$target.0", "source": "$source.zone_object",
    }


def uses_land_exile_source_return(value):
    return (isinstance(value, Mapping) and value.get("op") == "exile_fixed_library_tops_and_cast"
            and value.get("scope") == LAND_EXILE_RETURN_SCOPE)


def _resolved_ref(value):
    return type(value) is str and bool(value) and not value.startswith("$")


def validate_land_exile_source_return(value, *, resolved=False):
    if (not uses_land_exile_source_return(value) or set(value) != _FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != LAND_EXILE_RETURN_VERSION):
        raise ValueError("Land-conditioned return requires the original closed public exile instruction")
    if resolved:
        if (not _resolved_ref(value["player"]) or not _resolved_ref(value["supplier"])
                or value["player"] == value["supplier"]
                or value["source"] is not None and not _resolved_ref(value["source"])):
            raise ValueError("Land-conditioned return requires the original opponent and resolved source reference")
    elif dict(value) != land_exile_source_return_instruction():
        raise ValueError("Land-conditioned return lost its original target or source role")
    return LAND_EXILE_RETURN_SCOPE
