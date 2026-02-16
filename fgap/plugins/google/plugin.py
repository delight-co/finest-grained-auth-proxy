from fgap.plugins.base import Plugin


class GooglePlugin(Plugin):
    """Google plugin: gog CLI execution for Google Workspace."""

    @property
    def name(self) -> str:
        return "google"

    @property
    def tools(self) -> list[str]:
        return ["gog"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)
