"""Architecture profiles: pre-canned shapes that turn intent into config.

Importing this package registers every built-in profile by side-effect so
``get_profile("scaleout")`` works without explicit module imports.
"""

from ignition_stack.profiles import hub_and_spoke, mcp_n8n, scaleout, standalone  # noqa: F401
from ignition_stack.profiles.advisory import Advisory, spoke_advisory
from ignition_stack.profiles.base import (
    Profile,
    ProfileOptions,
    apply_iiot,
    build_profile,
    can_host_redundant_role,
    get_profile,
    list_profiles,
    mark_redundant,
)
from ignition_stack.profiles.hub_and_spoke import ProfileError

__all__ = [
    "Advisory",
    "Profile",
    "ProfileError",
    "ProfileOptions",
    "apply_iiot",
    "build_profile",
    "can_host_redundant_role",
    "get_profile",
    "list_profiles",
    "mark_redundant",
    "spoke_advisory",
]
