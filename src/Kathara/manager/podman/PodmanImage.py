import logging
from typing import Union, List, Set

import podman.domain.images
from podman import PodmanClient
from podman.errors import APIError

from .podman_registry import _inspect_distribution
from ... import utils
from ...event.EventDispatcher import EventDispatcher
from ...exceptions import InvalidImageArchitectureError, PodmanImageNotFoundError


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

    def get_remote(self, image_name: str) -> dict:
        """Gets the registry distribution data for an image, without pulling it.

        Args:
            image_name (str): The name of the image.

        Returns:
            dict: The distribution data (Descriptor/Platforms), as returned by the registry.

        Raises:
            `podman.errors.APIError`: If the server returns an error.
        """
        return _inspect_distribution(self.client.api, image_name)

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
        response = self.client.images.pull(image_name, stream=True, decode=True)
        for progress in response:
            EventDispatcher.get_instance().dispatch("docker_pull_progress", progress=progress)
        EventDispatcher.get_instance().dispatch("docker_pull_ended")

    def check_for_updates(self, image_name: str) -> None:
        """Update the specified image.

        Args:
            image_name (str): The name of a Podman Image.

        Returns:
            None
        """
        logging.debug(f"Checking updates for {image_name}...")

        if '@' in image_name:
            logging.debug(f"No need to check image digest of {image_name}.")
            return

        local_image_info = self.get_local(image_name)
        # Image has been built locally, so there's nothing to compare.
        local_repo_digests = local_image_info.attrs["RepoDigests"]
        if not local_repo_digests:
            logging.debug(f"Image {image_name} is built locally.")
            return

        remote_image_info = self.get_remote(image_name)['Descriptor']
        local_repo_digest = local_repo_digests[0]
        remote_image_digest = remote_image_info["digest"]

        # Format is image_name@sha256, so we strip the first part.
        (_, local_image_digest) = local_repo_digest.split("@")
        # We only need to update tagged images, not the ones with digests.
        if remote_image_digest != local_image_digest:
            EventDispatcher.get_instance().dispatch("docker_image_update_found",
                                                    docker_image=self,
                                                    image_name=image_name)

    def check(self, image_name: str) -> None:
        """Check the existence of the specified image.

        Args:
            image_name (str): The name of a Podman Image.

        Returns:
            None

        Raises:
            ConnectionError: If there is a connection error while pulling the Podman image from the registry.
            PodmanImageNotFoundError: If the Podman image is not available neither on the registry nor in local
                repository.
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
            pull (bool): If True, pull the image from the registry.

        Returns:
            None

        Raises:
            ConnectionError: If there is a connection error while pulling the Podman image from the registry.
            PodmanImageNotFoundError: If the Podman image is not available neither on the registry
                nor in local repository.
            InvalidImageArchitectureError: If the Podman image is not compatible with the host architecture.
        """
        try:
            # Tries to get the image from the local Podman repository.
            image = self.get_local(image_name)
            self._check_image_architecture(image_name, image)
            try:
                if pull:
                    self.check_for_updates(image_name)
            except APIError:
                logging.debug("Cannot check updates, skipping...")
        except InvalidImageArchitectureError as e:
            raise e
        except APIError:
            # If not found, tries on the remote registry.
            try:
                # If the image exists on the registry, pulls it.
                registry_data = self.get_remote(image_name)
                self._check_image_architecture(image_name, registry_data)
                if pull:
                    self.pull(image_name)
            except APIError as e:
                if e.response is not None and e.response.status_code == 500 and 'dial tcp' in (e.explanation or ''):
                    raise ConnectionError(
                        f"Podman Image `{image_name}` is not available in local repository and "
                        "no Internet connection is available to pull it from the registry."
                    )
                else:
                    raise PodmanImageNotFoundError(image_name)
            except InvalidImageArchitectureError as e:
                raise e

    @staticmethod
    def _check_image_architecture(image_name: str, image: Union[podman.domain.images.Image, dict]) -> None:
        """Check if the specified image is compatible with the host architecture.

        Args:
            image_name (str): The name of the Podman Image to check.
            image (Union[podman.domain.images.Image, dict]): Podman Image object, or raw registry
                distribution data as returned by get_remote().

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

        is_compatible = False
        if isinstance(image, podman.domain.images.Image):
            is_compatible = image.attrs['Architecture'] in compatible_archs
        elif isinstance(image, dict):
            image_archs = list(
                filter(lambda x: x['architecture'] in compatible_archs, image.get('Platforms', []))
            )
            is_compatible = len(image_archs) > 0
            logging.debug(f"Found compatible architectures: {image_archs}")

        if not is_compatible:
            raise InvalidImageArchitectureError(image_name, host_arch)
