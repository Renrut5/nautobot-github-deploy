"""Nautobot configuration for this deployment.

Nautobot's defaults (nautobot.core.settings) already read most settings from
environment variables, e.g. NAUTOBOT_DB_HOST, NAUTOBOT_REDIS_HOST and
NAUTOBOT_ALLOWED_HOSTS. Only override settings here that can't be set that way.

Docs: https://docs.nautobot.com/projects/core/en/stable/user-guide/administration/configuration/settings/
"""

from nautobot.core.settings import *  # noqa: F403

#
# Apps
#
PLUGINS = ["example_app"]

PLUGINS_CONFIG = {}
