"""localpibox.stack — LocalPibox stack operations library.

Module layout:

  gitutil   — git helpers (plain + GitHub-auth aware)
  repos     — path constants, single 6-repo stack map (workspace repos +
              config repo) and everything derived from it
  version   — pipeline detection, VERSION discovery + bump math, stack env,
              expected branches/pins
  workspace — workspace sync/clone + settings.json pin helpers
  validate  — full-stack alignment validation
  release   — stable-release promotion engine (dev → stable)

Import the public API from this package or from the owning module.
"""

from .gitutil import git, git_auth, git_remote
from .repos import (
    AGENT_GIT,
    CONFIG_REPO,
    DEFAULT_AGENT_DIR,
    DEFAULT_REMOTE,
    DEFAULT_REF,
    LPB_EXTENSION_REPOS,
    MIGRATE_KEEP,
    MEMORY_CONFIG_PATH,
    MEMORY_CONFIG_TEMPLATE,
    NPM_EXTENSION_PACKAGES,
    TAG_REPOS,
    VERSION_RE,
    WORKSPACE_REPOS,
    WORKSPACE_ROOT,
    migrate_legacy_layout,
    repo_path,
    stack_repos,
)
from .version import (
    bump_version,
    detect_pipeline,
    expected_branch,
    expected_pin_version,
    get_stack_env,
    get_version,
    parse_version,
)
from .workspace import (
    cmd_workspace_status,
    cmd_workspace_sync,
    cmd_workspace_sync_pins,
)
from .validate import cmd_validate
from .release import cmd_release_promote, cmd_release_status

__all__ = [
    "AGENT_GIT",
    "CONFIG_REPO",
    "DEFAULT_AGENT_DIR",
    "DEFAULT_REMOTE",
    "DEFAULT_REF",
    "LPB_EXTENSION_REPOS",
    "MIGRATE_KEEP",
    "MEMORY_CONFIG_PATH",
    "MEMORY_CONFIG_TEMPLATE",
    "NPM_EXTENSION_PACKAGES",
    "TAG_REPOS",
    "VERSION_RE",
    "WORKSPACE_REPOS",
    "WORKSPACE_ROOT",
    "bump_version",
    "cmd_release_promote",
    "cmd_release_status",
    "cmd_validate",
    "cmd_workspace_status",
    "cmd_workspace_sync",
    "cmd_workspace_sync_pins",
    "detect_pipeline",
    "expected_branch",
    "expected_pin_version",
    "get_stack_env",
    "get_version",
    "git",
    "git_auth",
    "git_remote",
    "migrate_legacy_layout",
    "parse_version",
    "repo_path",
    "stack_repos",
]
