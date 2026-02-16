from fgap.plugins.base import Plugin


class GitHubPlugin(Plugin):
    """GitHub plugin: gh CLI execution and git smart HTTP proxy."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def tools(self) -> list[str]:
        return ["gh"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    def get_routes(self, config: dict) -> list[tuple[str, str, callable]]:
        from .git_proxy import make_routes

        return make_routes(self.select_credential, config)

    def get_commands(self) -> dict[str, callable]:
        from .commands.discussion import execute as execute_discussion
        from .commands.issue import execute as execute_issue
        from .commands.sub_issue import execute as execute_sub_issue

        return {
            "issue": execute_issue,
            "discussion": execute_discussion,
            "sub-issue": execute_sub_issue,
        }
