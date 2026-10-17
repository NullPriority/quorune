from __future__ import annotations

"""Immutable Landwalk variant data shared by domain values and rule owners."""

BASIC_LANDWALK_TYPES = (
    ("plainswalk", "plains"),
    ("islandwalk", "island"),
    ("swampwalk", "swamp"),
    ("mountainwalk", "mountain"),
    ("forestwalk", "forest"),
)
BASIC_LAND_TYPES = frozenset(
    land_type for _, land_type in BASIC_LANDWALK_TYPES
)
QUALIFIED_LANDWALK_REQUIREMENTS = (
    ("desertwalk", "desert", None, None, None),
    ("artifact landwalk", None, "artifact", None, None),
    ("legendary landwalk", None, None, "legendary", None),
    ("nonbasic landwalk", None, None, None, "basic"),
    ("snow landwalk", None, None, "snow", None),
    *(("snow " + keyword, land_type, None, "snow", None)
        for keyword, land_type in BASIC_LANDWALK_TYPES),
)
QUALIFIED_LANDWALK_KEYWORDS = tuple(row[0] for row in QUALIFIED_LANDWALK_REQUIREMENTS)
LANDWALK_REQUIREMENTS = tuple(
    (keyword, land_type, None, None, None)
    for keyword, land_type in BASIC_LANDWALK_TYPES
) + QUALIFIED_LANDWALK_REQUIREMENTS
LANDWALK_KEYWORDS = tuple(row[0] for row in LANDWALK_REQUIREMENTS)
