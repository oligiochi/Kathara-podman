from collections.abc import Iterator
from typing import Any, Generator

from podman.api.client import APIClient

from ..podman_exec import exec_inspect
from ....foundation.manager.exec_stream.IExecStream import IExecStream


class PodmanExecStream(IExecStream):
    """Podman-specific class for handling the commands stream exec.

    Attributes:
        _stream (Generator): The generator yielding the output of the stream exec.
        _stream_api_object (str): The Podman exec id backing this stream.
        _client (APIClient): The podman-py low-level API client to interact with the stream.
    """
    __slots__ = ['_client']

    def __init__(self, stream: Generator, stream_api_object: str, client: APIClient) -> None:
        super().__init__(stream, stream_api_object)

        self._client: APIClient = client

    def stream_next(self) -> Iterator:
        """Return the next element from the stream.

        Returns:
            Iterator: The output iterator from the stream.
        """
        return next(self._stream)

    def exit_code(self) -> int:
        """Return the exit code of the execution.

        Returns:
            int: The exit code of the execution.
        """
        return int(exec_inspect(self._client, self._stream_api_object)['ExitCode'])
