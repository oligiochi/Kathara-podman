from typing import Optional, Dict, Any

from ...foundation.setting.SettingsAddon import SettingsAddon
from ...types import SharedCollisionDomainsOption

DEFAULTS = {
    "hosthome_mount": False,
    "shared_mount": True,
    "image_update_policy": "Prompt",
    "shared_cds": SharedCollisionDomainsOption.NOT_SHARED,
    "api_socket_url": None,
}


class PodmanSettingsAddon(SettingsAddon):
    __slots__ = ['hosthome_mount', 'shared_mount', 'image_update_policy', 'shared_cds', 'api_socket_url']

    def __init__(self) -> None:
        self.hosthome_mount: bool = False
        self.shared_mount: bool = True
        self.image_update_policy: str = 'Prompt'
        self.shared_cds: int = SharedCollisionDomainsOption.NOT_SHARED
        # URL of the Podman service socket (e.g. unix:///run/podman/podman.sock or a `podman machine`/SSH URL).
        # If None, PodmanManager resolves the default rootful/rootless local socket.
        self.api_socket_url: Optional[str] = None

    def _to_dict(self) -> Dict[str, Any]:
        return {
            'hosthome_mount': self.hosthome_mount,
            'shared_mount': self.shared_mount,
            'image_update_policy': self.image_update_policy,
            'shared_cds': self.shared_cds,
            'api_socket_url': self.api_socket_url
        }
