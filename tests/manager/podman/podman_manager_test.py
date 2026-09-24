import sys
from unittest import mock
from unittest.mock import Mock

import pytest
from podman.errors import APIError

sys.path.insert(0, './')

from src.Kathara.manager.podman.PodmanManager import PodmanManager, default_podman_socket
from src.Kathara.exceptions import ContainerEngineConnectionError, NotSupportedError


def _setting_mock(**overrides):
    setting_mock = Mock()
    setting_mock.configure_mock(**{'api_socket_url': None, **overrides})
    return setting_mock


@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.manager.podman.PodmanManager.PodmanClient")
def test_init_raises_on_ping_failure(mock_podman_client_cls, mock_setting_get_instance):
    mock_setting_get_instance.return_value = _setting_mock()
    client_instance = mock_podman_client_cls.return_value
    client_instance.ping.side_effect = APIError("boom")

    with pytest.raises(ContainerEngineConnectionError):
        PodmanManager()


@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.manager.podman.PodmanManager.PodmanClient")
def test_init_raises_when_ping_returns_false(mock_podman_client_cls, mock_setting_get_instance):
    mock_setting_get_instance.return_value = _setting_mock()
    client_instance = mock_podman_client_cls.return_value
    client_instance.ping.return_value = False

    with pytest.raises(ContainerEngineConnectionError):
        PodmanManager()


@mock.patch("src.Kathara.manager.podman.PodmanLink.PodmanLink")
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine")
@mock.patch("src.Kathara.manager.podman.PodmanImage.PodmanImage")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.manager.podman.PodmanManager.PodmanClient")
def test_init_success(mock_podman_client_cls, mock_setting_get_instance, mock_image, mock_machine, mock_link):
    mock_setting_get_instance.return_value = _setting_mock()
    client_instance = mock_podman_client_cls.return_value
    client_instance.ping.return_value = True

    manager = PodmanManager()

    assert manager.client == client_instance


def test_get_formatted_manager_name():
    assert PodmanManager.get_formatted_manager_name() == "Podman (Kathara)"


@mock.patch("src.Kathara.manager.podman.PodmanLink.PodmanLink")
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine")
@mock.patch("src.Kathara.manager.podman.PodmanImage.PodmanImage")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.manager.podman.PodmanManager.PodmanClient")
def test_wipe_all_users_not_supported(mock_podman_client_cls, mock_setting_get_instance, mock_image, mock_machine,
                                      mock_link):
    mock_setting_get_instance.return_value = _setting_mock()
    client_instance = mock_podman_client_cls.return_value
    client_instance.ping.return_value = True

    manager = PodmanManager()

    with pytest.raises(NotSupportedError, match="Wiping the devices of all users"):
        manager.wipe(all_users=True)

    assert not mock_machine.return_value.wipe.called
    assert not mock_link.return_value.wipe.called


def test_default_podman_socket_rootless(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: False)
    monkeypatch.setattr("src.Kathara.manager.podman.PodmanManager.get_runtime_dir", lambda: "/run/user/1000")

    assert default_podman_socket() == "unix:///run/user/1000/podman/podman.sock"


def test_default_podman_socket_rootful(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: path == "/run/podman/podman.sock")

    assert default_podman_socket() == "unix:///run/podman/podman.sock"


@mock.patch("src.Kathara.manager.podman.PodmanLink.PodmanLink")
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine")
@mock.patch("src.Kathara.manager.podman.PodmanImage.PodmanImage")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.manager.podman.PodmanManager.PodmanClient")
def test_get_release_version_nested_shape(mock_podman_client_cls, mock_setting_get_instance, mock_image,
                                          mock_machine, mock_link):
    mock_setting_get_instance.return_value = _setting_mock()
    client_instance = mock_podman_client_cls.return_value
    client_instance.ping.return_value = True
    # libpod's native /version nests the version string, unlike Docker's flat shape.
    client_instance.version.return_value = {"Version": {"Version": "4.9.3", "APIVersion": "4.9.3"}}

    manager = PodmanManager()

    assert manager.get_release_version() == "4.9.3"
