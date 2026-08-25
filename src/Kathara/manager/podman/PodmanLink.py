import logging
from multiprocessing.dummy import Pool
from typing import List, Dict, Generator, Set, Optional

import podman.domain.networks
from podman import PodmanClient
from podman.domain.ipam import IPAMConfig
from podman.errors import NotFound

from .stats.PodmanLinkStats import PodmanLinkStats
from ... import utils
from ...event.EventDispatcher import EventDispatcher
from ...exceptions import PrivilegeError, InvocationError, NotSupportedError
from ...model.Lab import Lab
from ...model.Link import BRIDGE_LINK_NAME, Link
from ...setting.Setting import Setting
from ...types import SharedCollisionDomainsOption

# The default bridge network Podman creates for containers not attached to a user-defined network.
# Used for Kathará's "bridged" device option, mirroring DockerLink.get_docker_bridge().
DEFAULT_BRIDGE_NETWORK_NAME = "podman"


class PodmanLink(object):
    """The class responsible for deploying Kathara collision domains as Podman networks and interact with them."""
    __slots__ = ['client']

    def __init__(self, client: PodmanClient) -> None:
        self.client: PodmanClient = client

    def deploy_links(self, lab: Lab, selected_links: Set[str] = None, excluded_links: Set[str] = None) -> None:
        """Deploy all the network scenario collision domains as Podman networks.

        Args:
            lab (Kathara.model.Lab.Lab): A Kathara network scenario.
            selected_links (Set[str]): A set containing the name of the collision domains to deploy.
            excluded_links (Set[str]): A set containing the name of the collision domains to exclude.

        Returns:
            None

        Raises:
            InvocationError: If both `selected_links` and `excluded_links` are specified.
        """
        if selected_links and excluded_links:
            raise InvocationError(f"You can either specify `selected_links` or `excluded_links`.")

        links = lab.links.items()
        if selected_links:
            links = {
                k: v for k, v in links if k in selected_links
            }.items()
        elif excluded_links:
            links = {
                k: v for k, v in links if k not in excluded_links
            }.items()

        if len(links) > 0:
            pool_size = utils.get_pool_size()
            items = utils.chunk_list(links, pool_size)

            EventDispatcher.get_instance().dispatch("links_deploy_started", items=links)

            with Pool(pool_size) as links_pool:
                for chunk in items:
                    links_pool.map(func=self._deploy_link, iterable=chunk)

            EventDispatcher.get_instance().dispatch("links_deploy_ended")

        # Create a podman bridge link in the lab object and assign the Podman Network object associated to it.
        podman_bridge = self.get_podman_bridge()
        if podman_bridge:
            link = lab.get_or_new_link(BRIDGE_LINK_NAME)
            link.api_object = podman_bridge

    def _deploy_link(self, link_item: (str, Link)) -> None:
        """Deploy the collision domain contained in the link_item as a Podman network.

        Args:
            link_item (Tuple[str, Link]): A tuple composed by the name of the collision domain and a Link object

        Returns:
            None
        """
        (_, link) = link_item

        if link.name == BRIDGE_LINK_NAME:
            return

        self.create(link)

        EventDispatcher.get_instance().dispatch("link_deployed", item=link)

    def create(self, link: Link) -> None:
        """Create a Podman network representing the collision domain object and assign it to link.api_object.

        Args:
            link (Kathara.model.Link.Link): A Kathara collision domain.

        Returns:
            None

        Raises:
            NotSupportedError: If the link is attached to external interfaces.
        """
        # Reserved name for bridged connections, ignore.
        if link.name == BRIDGE_LINK_NAME:
            return

        # If a network with the same name exists, return it instead of creating a new one.
        filter_lab_hash = None
        filter_user = None
        if Setting.get_instance().shared_cds == SharedCollisionDomainsOption.NOT_SHARED:
            filter_lab_hash = link.lab.hash
        if Setting.get_instance().shared_cds != SharedCollisionDomainsOption.USERS:
            filter_user = utils.get_current_user_name()

        networks = self.get_links_api_objects_by_filters(
            link_name=link.name, lab_hash=filter_lab_hash, user=filter_user
        )
        if networks:
            link.api_object = networks.pop()
        else:
            if link.external:
                raise NotSupportedError("External collision domains are not supported on the Podman backend yet.")

            link_name = self.get_network_name(link)
            additional_labels = {}
            if Setting.get_instance().shared_cds != SharedCollisionDomainsOption.USERS:
                additional_labels["user"] = utils.get_current_user_name()
            if Setting.get_instance().shared_cds == SharedCollisionDomainsOption.NOT_SHARED:
                additional_labels["lab_hash"] = link.lab.hash

            # Podman/netavark's default (host-local) IPAM is harmless but cosmetic for Kathará, which
            # always assigns interface addresses itself (see SPIKE-REPORT.md S6): unlike Docker's compat
            # API, libpod's native network create lets us opt out of it entirely with `ipam_driver=none`.
            link.api_object = self.client.networks.create(
                name=link_name,
                driver="bridge",
                internal=True,
                ipam=IPAMConfig(driver="none"),
                labels={
                    "name": link.name,
                    "app": "kathara",
                    "external": ";".join([x.get_full_name() for x in link.external]),
                    **additional_labels
                }
            )

    def undeploy(self, lab_hash: str, selected_links: Optional[Set[str]] = None) -> None:
        """Undeploy all the collision domains of the scenario specified by lab_hash.

        Args:
            lab_hash (str): The hash of the network scenario to undeploy.
            selected_links (Set[str]): If specified, delete only the collision domains contained in the set.

        Returns:
            None
        """
        networks = self.get_links_api_objects_by_filters(lab_hash=lab_hash)
        if selected_links is not None:
            networks = [item for item in networks if item.attrs["labels"]["name"] in selected_links]

        for item in networks:
            item.reload()
        networks = [item for item in networks if len(item.containers) <= 0]

        if len(networks) > 0:
            pool_size = utils.get_pool_size()
            items = utils.chunk_list(networks, pool_size)

            EventDispatcher.get_instance().dispatch("links_undeploy_started", items=networks)

            with Pool(pool_size) as links_pool:
                for chunk in items:
                    links_pool.map(func=self._undeploy_link, iterable=chunk)

            EventDispatcher.get_instance().dispatch("links_undeploy_ended")

    def wipe(self, user: str = None) -> None:
        """Undeploy all the Podman networks of the specified user. If user is None, it undeploy all the Podman networks.

        Args:
            user (str): The name of a current user on the host

        Returns:
            None
        """
        user_label = user if Setting.get_instance().shared_cds != SharedCollisionDomainsOption.USERS else None
        networks = self.get_links_api_objects_by_filters(user=user_label)
        for item in networks:
            item.reload()
        networks = [item for item in networks if len(item.containers) <= 0]

        pool_size = utils.get_pool_size()
        items = utils.chunk_list(networks, pool_size)

        with Pool(pool_size) as links_pool:
            for chunk in items:
                links_pool.map(func=self._undeploy_link, iterable=chunk)

    def _undeploy_link(self, network: podman.domain.networks.Network) -> None:
        """Undeploy a Podman network.

        Args:
            network (podman.domain.networks.Network): The Podman network to undeploy.

        Returns:
            None
        """
        self._delete_link(network)

        EventDispatcher.get_instance().dispatch("link_undeployed", item=network)

    def get_podman_bridge(self) -> Optional[podman.domain.networks.Network]:
        """Return the Podman default bridge network.

        Returns:
            Optional[podman.domain.networks.Network]: The Podman default bridge network if it exists, else None.
        """
        try:
            return self.client.networks.get(DEFAULT_BRIDGE_NETWORK_NAME)
        except NotFound:
            logging.debug(f"Default Podman bridge network `{DEFAULT_BRIDGE_NETWORK_NAME}` not found.")
            return None

    def get_links_api_objects_by_filters(self, lab_hash: str = None, link_name: str = None, user: str = None) -> \
            List[podman.domain.networks.Network]:
        """Return the Podman networks specified by lab_hash and user.

        Args:
            lab_hash (str): The hash of a network scenario. If specified, return all the networks in the scenario.
            link_name (str): The name of a network. If specified, return the specified network of the scenario.
            user (str): The name of a user on the host. If specified, return only the networks of the user.

        Returns:
            List[podman.domain.networks.Network]: A list of Podman networks.
        """
        filters = {"label": ["app=kathara"]}
        if user:
            filters["label"].append(f"user={user}")
        if lab_hash:
            filters["label"].append(f"lab_hash={lab_hash}")
        if link_name:
            filters["label"].append(f"name={link_name}")

        return self.client.networks.list(filters=filters)

    def get_links_stats(self, lab_hash: str = None, link_name: str = None, user: str = None) -> \
            Generator[Dict[str, PodmanLinkStats], None, None]:
        """Return a generator containing the Podman networks' stats.

        Args:
           lab_hash (str): The hash of a network scenario. If specified, return all the stats of the networks in the
           scenario.
           link_name (str): The name of a device. If specified, return the specified network stats.
           user (str): The name of a user on the host. If specified, return only the stats of the specified user.

        Returns:
           Generator[Dict[str, PodmanLinkStats], None, None]: A generator containing network names as keys and
           PodmanLinkStats as values.

        Raises:
            PrivilegeError: If user param is None and the user does not have root privileges.
        """
        if user is None and not utils.is_admin():
            raise PrivilegeError("You must be root to get networks statistics of all users.")

        networks_stats = {}

        def load_link_stats(network):
            if network.name not in networks_stats:
                networks_stats[network.name] = PodmanLinkStats(network)

        while True:
            networks = self.get_links_api_objects_by_filters(lab_hash=lab_hash, link_name=link_name, user=user)
            if not networks:
                yield dict()

            pool_size = utils.get_pool_size()
            items = utils.chunk_list(networks, pool_size)
            with Pool(pool_size) as links_pool:
                for chunk in items:
                    links_pool.map(func=load_link_stats, iterable=chunk)

            networks_to_remove = []
            for network_id, network_stats in networks_stats.items():
                try:
                    network_stats.update()
                except StopIteration:
                    networks_to_remove.append(network_id)
                    continue

            for k in networks_to_remove:
                networks_stats.pop(k, None)

            yield networks_stats

    def _delete_link(self, network: podman.domain.networks.Network) -> None:
        """Delete a Podman network.

        Args:
            network (podman.domain.networks.Network): A Podman network.

        Returns:
            None
        """
        network.remove()

    @staticmethod
    def get_network_name(link: Link) -> str:
        """Return the name of a Podman network.

        Args:
            link (Kathara.model.Link): A Kathara collision domain.

        Returns:
            str: The name of the Podman network in the format "|net_prefix|_|username_prefix|_|name|".
                If shared collision domains, the format is: "|net_prefix|_|lab_hash|".
        """
        if Setting.get_instance().shared_cds == SharedCollisionDomainsOption.LABS:
            return f"{Setting.get_instance().net_prefix}_{utils.get_current_user_name()}_{link.name}"
        elif Setting.get_instance().shared_cds == SharedCollisionDomainsOption.USERS:
            return f"{Setting.get_instance().net_prefix}_{link.name}"
        elif Setting.get_instance().shared_cds == SharedCollisionDomainsOption.NOT_SHARED:
            return f"{Setting.get_instance().net_prefix}_{utils.get_current_user_name()}_{link.name}_{link.lab.hash}"
