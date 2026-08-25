"""Temporary shim for Podman exec operations not yet exposed as low-level API by podman-py.

podman-py only exposes a high-level `Container.exec_run()` that bundles create+start+inspect
into a single call and never hands back the exec id, which Kathara needs to poll the exit code
after a streamed/detached exec and to resize a TTY mid-session. These helpers talk to the
`/exec/*` and `/containers/{id}/exec` libpod endpoints directly through the same low-level
`podman.api.client.APIClient` (`PodmanClient.api`) that `Container.exec_run()` itself uses
internally, reusing its `stream_frames`/`demux_output` helpers for output framing.

Meant to shrink to nothing once podman-py exposes exec_create/exec_start/exec_resize/exec_inspect
publicly (see containers/podman-py#648).
"""
import json
from typing import Any, Dict, List, Optional, Union

from podman import api as podman_api
from podman.api.output_utils import demux_output


def exec_create(api: Any, container_id: str, cmd: Union[str, List[str]], stdout: bool = True, stderr: bool = True,
                 stdin: bool = False, tty: bool = False, privileged: bool = False, user: str = '',
                 environment: Optional[Union[Dict[str, str], List[str]]] = None,
                 workdir: Optional[str] = None) -> str:
    """Create an exec instance on a container and return its id."""
    if isinstance(environment, dict):
        environment = [f"{k}={v}" for k, v in environment.items()]

    payload = {
        "AttachStdin": stdin, "AttachStdout": stdout, "AttachStderr": stderr,
        "Tty": tty, "Cmd": cmd if isinstance(cmd, list) else [cmd], "Privileged": privileged,
        "Env": environment, "WorkingDir": workdir,
    }
    if user:
        payload["User"] = user

    resp = api.post(
        f"/containers/{container_id}/exec",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
    )
    resp.raise_for_status()
    return resp.json()["Id"]


def exec_start(api: Any, exec_id: str, tty: bool = False, detach: bool = False, stream: bool = False,
                demux: bool = False):
    """Start a previously created exec instance and return its (buffered or streamed) output."""
    stream = stream and not detach

    resp = api.post(
        f"/exec/{exec_id}/start",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"Detach": detach, "Tty": tty}),
        stream=stream,
    )
    resp.raise_for_status()

    if stream:
        return podman_api.stream_frames(resp, demux=demux)
    if demux:
        return demux_output(resp.content)
    return resp.content


def exec_start_hijack(api: Any, exec_id: str, tty: bool = True) -> Any:
    """Start an exec instance in interactive mode and hijack the underlying raw socket."""
    resp = api.post(
        f"/exec/{exec_id}/start",
        headers={
            "Content-Type": "application/json",
            "Connection": "Upgrade", "Upgrade": "tcp",
        },
        data=json.dumps({"Detach": False, "Tty": tty}),
        stream=True,
    )
    sock = resp.raw.connection.sock
    sock._hijacked_response = resp  # prevents urllib3 from reclaiming the connection
    return sock


def exec_inspect(api: Any, exec_id: str) -> Dict[str, Any]:
    """Return the current state of an exec instance (including its ExitCode)."""
    resp = api.get(f"/exec/{exec_id}/json")
    resp.raise_for_status()
    return resp.json()


def exec_resize(api: Any, exec_id: str, cols: int, rows: int) -> None:
    """Resize the TTY of a running exec instance."""
    api.post(f"/exec/{exec_id}/resize", params={"h": rows, "w": cols}).raise_for_status()
