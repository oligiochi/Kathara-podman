import logging
from typing import Union, List, Set

import podman.domain.images
from podman import PodmanClient
from podman.errors import APIError, ImageNotFound

from ... import utils
from ...event.EventDispatcher import EventDispatcher
from ...exceptions import InvalidImageArchitectureError, DockerImageNotFoundError


class PodmanImage(object):
    """Class responsible for interacting with Podman Images."""
    __slots__ = ['client']

    def __init__(self, client: PodmanClient) -> None:
        self.client: PodmanClient = client

    def get_local(self, image_name: str) -> podman.domain.images.Image:
        """Return the specified Podman Image.

        Args:
            image_name (str): The name of a Podman Image.

        Returns:
            podman.domain.images.Image: A Podman Image
        """
        return self.client.images.get(image_name)

    def pull(self, image_name: str) -> None:
        """Pull the specified Podman Image.

        Args:
            image_name (str): The name of a Podman Image.

        Returns:
            None
        """
        # If no tag or sha key is specified, we add "latest"
        if (':' or '@') not in image_name:
            image_name = "%s:latest" % image_name

        EventDispatcher.get_instance().dispatch("docker_pull_started")
        logging.info("Pulling image `%s`... This may take a while." % image_name)
        self.client.images.pull(image_name, stream=False)
        EventDispatcher.get_instance().dispatch("docker_pull_ended")

    def check_for_updates(self, image_name: str) -> None:
        """Check if a newer version of the specified image is available.

        Not supported: `ImagesManager.get_registry_data` is a local-only shim in podman-py
        (it just re-wraps the already pulled local image, it does not query the registry),
        so there is no way to compare local and remote digests without pulling. See
        SPIKE-REPORT.md S8.

        Args:
            image_name (str): The name of a Podman Image.

        Returns:
            None
        """
        logging.debug(
            f"Cannot check updates for `{image_name}`: Podman does not expose a registry inspection endpoint."
        )

    def check(self, image_name: str) -> None:
        """Check the existence of the specified image.

        Args:
            image_name (str): The name of a Podman Image.

        Returns:
            None

        Raises:
            ConnectionError: If the image is not locally available and there is no connection to a remote image repository.
            DockerImageNotFoundError: If the Podman image is not available neither on the registry nor locally.
        """
        self._check_and_pull(image_name, pull=False)

    def check_from_list(self, images: Union[List[str], Set[str]]) -> None:
        """Check a list of specified images.

        Args:
            images (Union[List[str], Set[str]]): A list of Podman images name to pull.

        Returns:
            None
        """
        for image in images:
            self._check_and_pull(image)

    def _check_and_pull(self, image_name: str, pull: bool = True) -> None:
        """Check and pull of the specified image.

        Args:
            image_name (str): The name of a Podman Image.
            pull (bool): If True, pull the image if it's not already available locally.

        Returns:
            None

        Raises:
            ConnectionError: If there is a connection error while pulling the Podman image.
            DockerImageNotFoundError: If the Podman image is not available neither on the registry
                nor in local repository.
            InvalidImageArchitectureError: If the Podman image is not compatible with the host architecture.
        """
        try:
            # Tries to get the image from the local Podman storage.
            image = self.get_local(image_name)
            self._check_image_architecture(image_name, image)
            self.check_for_updates(image_name)
        except InvalidImageArchitectureError as e:
            raise e
        except (ImageNotFound, APIError):
            # Not found locally: the only way to verify it exists remotely is to actually pull it,
            # since podman-py has no registry-inspection endpoint (see check_for_updates above).
            if not pull:
                raise DockerImageNotFoundError(image_name)

            try:
                self.pull(image_name)
                image = self.get_local(image_name)
                self._check_image_architecture(image_name, image)
            except (ImageNotFound, APIError) as e:
                if isinstance(e, APIError) and 'dial tcp' in str(e):
                    raise ConnectionError(
                        f"Podman Image `{image_name}` is not available in local repository and "
                        "no Internet connection is available to pull it."
                    )
                else:
                    raise DockerImageNotFoundError(image_name)

    @staticmethod
    def _check_image_architecture(image_name: str, image: podman.domain.images.Image) -> None:
        """Check if the specified image is compatible with the host architecture.

        Args:
            image_name (str): The name of the Podman Image to check.
            image (podman.domain.images.Image): Podman Image object.

        Returns:
            None

        Raises:
            InvalidImageArchitectureError: If the Podman image is not compatible with the architecture.
        """
        host_arch = utils.get_architecture()

        # amd64 images are compatible on macOS using Rosetta.
        compatible_archs = utils.exec_by_platform(
            lambda: {host_arch}, lambda: {host_arch}, lambda: {host_arch, "amd64"}
        )

        logging.debug(f"Platform compatible architectures: {compatible_archs}")

        image_arch = image.attrs.get('Architecture')
        if image_arch is None:
            # Architecture metadata is missing (e.g. locally built images without full manifest): skip the check.
            return

        if image_arch not in compatible_archs:
            raise InvalidImageArchitectureError(image_name, host_arch)
