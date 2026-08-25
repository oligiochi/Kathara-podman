import json
import sys
from unittest.mock import Mock

sys.path.insert(0, './')

from src.Kathara.manager.podman import podman_exec


def _api_mock(json_body=None, content=b""):
    api = Mock()
    resp = Mock()
    resp.json.return_value = json_body or {}
    resp.content = content
    api.post.return_value = resp
    api.get.return_value = resp
    return api, resp


def test_exec_create_builds_expected_payload():
    api, resp = _api_mock(json_body={"Id": "exec123"})

    exec_id = podman_exec.exec_create(api, "container1", ["ls", "-la"], tty=True, privileged=True, user="root")

    assert exec_id == "exec123"
    _, kwargs = api.post.call_args
    assert kwargs["headers"]["Content-Type"] == "application/json"
    payload = json.loads(kwargs["data"])
    assert payload["Cmd"] == ["ls", "-la"]
    assert payload["Tty"] is True
    assert payload["Privileged"] is True
    assert payload["User"] == "root"
    resp.raise_for_status.assert_called_once()


def test_exec_create_converts_environment_dict():
    api, _ = _api_mock(json_body={"Id": "exec123"})

    podman_exec.exec_create(api, "container1", "ls", environment={"A": "1", "B": "2"})

    _, kwargs = api.post.call_args
    payload = json.loads(kwargs["data"])
    assert set(payload["Env"]) == {"A=1", "B=2"}


def test_exec_start_buffered_returns_content():
    api, resp = _api_mock(content=b"hello")

    output = podman_exec.exec_start(api, "exec123", tty=False, demux=False, stream=False)

    assert output == b"hello"
    resp.raise_for_status.assert_called_once()


def test_exec_inspect_returns_exit_code():
    api, _ = _api_mock(json_body={"ExitCode": 0, "Running": False})

    result = podman_exec.exec_inspect(api, "exec123")

    assert result["ExitCode"] == 0
    api.get.assert_called_once_with("/exec/exec123/json")


def test_exec_resize_uses_h_w_params():
    api = Mock()
    resp = Mock()
    api.post.return_value = resp

    podman_exec.exec_resize(api, "exec123", cols=80, rows=24)

    api.post.assert_called_once_with("/exec/exec123/resize", params={"h": 24, "w": 80})
    resp.raise_for_status.assert_called_once()
