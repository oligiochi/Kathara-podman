import sys
from unittest import mock
from unittest.mock import Mock

import pytest

sys.path.insert(0, './')

from src.Kathara.manager.podman.PodmanMachine import PodmanMachine, IFACES_LABEL, CPU_PERIOD, IFACE_SYSCTL_RE
from src.Kathara.model.Lab import Lab
from src.Kathara.model.Link import Link
from src.Kathara.model.Machine import Machine
from src.Kathara.exceptions import NotSupportedError
from src.Kathara.types import SharedCollisionDomainsOption


#
# FIXTURE
#
@pytest.fixture()
@mock.patch("src.Kathara.manager.podman.PodmanImage.PodmanImage")
@mock.patch("podman.PodmanClient")
def podman_machine(mock_podman_client, mock_podman_image):
    return PodmanMachine(mock_podman_client, mock_podman_image)


@pytest.fixture()
@mock.patch("podman.domain.containers.Container")
def default_device(mock_podman_container):
    device = Machine(Lab('Default scenario'), "test_device")
    device.add_meta("mem", "64m")
    device.add_meta("cpus", "2")
    device.add_meta("image", "kathara/test")
    device.add_meta("bridged", False)
    device.api_object = mock_podman_container
    device.api_object.id = "device_id"
    device.api_object.attrs = {"NetworkSettings": {"Networks": {}}}
    device.api_object.labels = {"user": "user", "name": "test_device", "lab_hash": "lab_hash", "shell": "/bin/bash"}
    return device


@pytest.fixture()
def default_link(default_device):
    link = Link(default_device.lab, "A")
    link.api_object = Mock()
    link.api_object.name = "podman_link_a"
    link.api_object.connect = Mock(return_value=True)
    return link


def _setting_mock(**overrides):
    setting_mock = Mock()
    setting_mock.configure_mock(**{
        'shared_cds': SharedCollisionDomainsOption.NOT_SHARED,
        'device_prefix': 'dev_prefix',
        'device_shell': '/bin/bash',
        'enable_ipv6': False,
        'hosthome_mount': False,
        'shared_mount': False,
        **overrides
    })
    return setting_mock


#
# TEST: create
#
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.get_machines_api_objects_by_filters")
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.copy_files")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.utils.get_current_user_name")
def test_create_no_interfaces(mock_get_current_user_name, mock_setting_get_instance, mock_copy_files,
                              mock_get_machines_api_objects_by_filters, podman_machine, default_device):
    mock_get_machines_api_objects_by_filters.return_value = []
    mock_get_current_user_name.return_value = "test-user"
    mock_setting_get_instance.return_value = _setting_mock()

    podman_machine.create(default_device)

    _, kwargs = podman_machine.client.containers.create.call_args
    assert kwargs['image'] == 'kathara/test'
    assert kwargs['hostname'] == 'test_device'
    assert kwargs['privileged'] is False
    assert kwargs['network_mode'] == 'none'
    assert 'networks' not in kwargs
    # No container-level MAC address kwarg: MACs are set per-interface (see `_get_network_options` /
    # `connect_interface`), not through podman-py's `containers.create(mac_address=...)`.
    assert 'mac_address' not in kwargs
    assert kwargs['mem_limit'] == '64m'
    # cpus=2 -> cpu_quota = 2 * CPU_PERIOD, and cpu_period must be set alongside it
    assert kwargs['cpu_quota'] == 2 * CPU_PERIOD
    assert kwargs['cpu_period'] == CPU_PERIOD
    assert kwargs['ports'] == {}
    assert kwargs['volumes'] == {}
    # Anonymous-volume fix: both declared VOLUME paths must be tmpfs-mounted since nothing else covers them.
    mount_targets = {m['target'] for m in kwargs['mounts']}
    assert mount_targets == {'/hosthome', '/shared'}
    assert all(m['type'] == 'tmpfs' for m in kwargs['mounts'])
    assert kwargs['ulimits'] == []
    assert kwargs['entrypoint'] is None
    assert kwargs['command'] is None
    assert kwargs['labels']['name'] == 'test_device'
    assert kwargs['labels']['user'] == 'test-user'
    assert kwargs['labels']['app'] == 'kathara'
    assert IFACES_LABEL not in kwargs['labels']

    assert not mock_copy_files.called


@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.get_machines_api_objects_by_filters")
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.copy_files")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.utils.get_current_user_name")
def test_create_with_first_interface(mock_get_current_user_name, mock_setting_get_instance, mock_copy_files,
                                     mock_get_machines_api_objects_by_filters, podman_machine, default_device,
                                     default_link):
    mock_get_machines_api_objects_by_filters.return_value = []
    mock_get_current_user_name.return_value = "test-user"
    mock_setting_get_instance.return_value = _setting_mock()

    default_device.add_interface(default_link, mac_address="00:00:00:00:00:01")

    podman_machine.create(default_device)

    _, kwargs = podman_machine.client.containers.create.call_args
    # libpod requires an explicit `bridge` netns mode when `networks` carry per-network options
    # such as a static MAC address (`networks=` and `network_mode="none"` are mutually exclusive).
    assert kwargs['network_mode'] == 'bridge'
    # The interface name, static MAC and per-interface sysctls (prefixed for the Kathará network
    # plugin) travel as per-network options, not as container-level kwargs.
    assert kwargs['networks'] == {
        'podman_link_a': {
            'interface_name': 'eth0',
            'static_mac': '00:00:00:00:00:01',
            'options': {
                'sysctl.net.ipv4.conf.eth0.rp_filter': '0',
                'sysctl.net.ipv6.conf.eth0.disable_ipv6': '1',
            }
        }
    }
    assert 'mac_address' not in kwargs
    # `sysctls=` only carries device-wide sysctls: per-interface ones are filtered out (they went
    # into the network options above instead).
    assert kwargs['sysctls']['net.ipv4.conf.all.rp_filter'] == '0'
    assert kwargs['sysctls']['net.ipv4.ip_forward'] == '1'
    assert all(not IFACE_SYSCTL_RE.match(k) for k in kwargs['sysctls'])
    # Sysctl values must all be strings for the Podman API.
    assert all(isinstance(v, str) for v in kwargs['sysctls'].values())

    import json
    ifaces = json.loads(kwargs['labels'][IFACES_LABEL])
    assert ifaces == {"A": {"num": 0, "mac_address": "00:00:00:00:00:01"}}


@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.get_machines_api_objects_by_filters")
@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.copy_files")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.utils.get_current_user_name")
def test_create_first_interface_custom_sysctl_in_network_options_not_in_sysctls(
        mock_get_current_user_name, mock_setting_get_instance, mock_copy_files,
        mock_get_machines_api_objects_by_filters, podman_machine, default_device, default_link):
    mock_get_machines_api_objects_by_filters.return_value = []
    mock_get_current_user_name.return_value = "test-user"
    mock_setting_get_instance.return_value = _setting_mock()

    # A user override for eth0's rp_filter (per-interface) and a device-wide sysctl.
    default_device.add_meta("sysctl", "net.ipv4.conf.eth0.rp_filter=1")
    default_device.add_meta("sysctl", "net.ipv4.tcp_syncookies=1")
    default_device.add_interface(default_link, number=0)

    podman_machine.create(default_device)

    _, kwargs = podman_machine.client.containers.create.call_args
    # The per-interface override lands in eth0's network options, with the user value...
    assert kwargs['networks']['podman_link_a']['options']['sysctl.net.ipv4.conf.eth0.rp_filter'] == '1'
    # ...and never in the device-wide sysctls, which still carry the unrelated device-wide sysctl.
    assert 'net.ipv4.conf.eth0.rp_filter' not in kwargs['sysctls']
    assert kwargs['sysctls']['net.ipv4.tcp_syncookies'] == '1'
    assert all(not IFACE_SYSCTL_RE.match(k) for k in kwargs['sysctls'])


@mock.patch("src.Kathara.manager.podman.PodmanMachine.PodmanMachine.get_machines_api_objects_by_filters")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
@mock.patch("src.Kathara.utils.get_current_user_name")
def test_create_privileged_not_supported(mock_get_current_user_name, mock_setting_get_instance,
                                         mock_get_machines_api_objects_by_filters, podman_machine, default_device):
    mock_get_machines_api_objects_by_filters.return_value = []
    mock_get_current_user_name.return_value = "test-user"
    mock_setting_get_instance.return_value = _setting_mock()

    default_device.add_meta("privileged", True)

    with pytest.raises(NotSupportedError, match="Privileged devices"):
        podman_machine.create(default_device)

    assert not podman_machine.client.containers.create.called


#
# TEST: connect_interface / disconnect_from_link
#
@mock.patch("src.Kathara.manager.podman.libpod_compat.LibpodCompat.network_connect")
@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
def test_connect_interface(mock_setting_get_instance, mock_network_connect, podman_machine, default_device,
                           default_link):
    mock_setting_get_instance.return_value = _setting_mock()
    default_device.api_object.attrs = {"NetworkSettings": {"Networks": {}}}
    interface = default_device.add_interface(default_link, number=0)

    podman_machine.connect_interface(default_device, interface)

    # Hot-connect goes through the libpod compat layer (podman-py's native `Network.connect()` cannot
    # set interface name, static MAC or per-interface sysctls), applying the same per-interface
    # sysctls computed for a first-interface connection at create time.
    mock_network_connect.assert_called_once_with(
        default_link.api_object, default_device.api_object, "eth0",
        mac_address=interface.mac_address,
        sysctls={
            "net.ipv4.conf.eth0.rp_filter": 0,
            "net.ipv6.conf.eth0.disable_ipv6": 1,
        }
    )


@mock.patch("src.Kathara.manager.podman.libpod_compat.LibpodCompat.network_connect")
def test_connect_interface_already_attached(mock_network_connect, podman_machine, default_device, default_link):
    default_device.api_object.attrs = {"NetworkSettings": {"Networks": {"podman_link_a": {}}}}
    interface = default_device.add_interface(default_link, number=0)

    podman_machine.connect_interface(default_device, interface)

    assert not mock_network_connect.called


def test_disconnect_from_link(default_device, default_link):
    default_device.api_object.attrs = {"NetworkSettings": {"Networks": {"podman_link_a": {}}}}

    PodmanMachine.disconnect_from_link(default_device, default_link)

    default_link.api_object.disconnect.assert_called_once_with(default_device.api_object)


#
# TEST: get_machines_stats
#
def test_get_machines_stats_all_users_not_supported(podman_machine):
    machines_stats = podman_machine.get_machines_stats()

    with pytest.raises(NotSupportedError, match="Statistics of all users"):
        next(machines_stats)


#
# TEST: get_container_name
#
def test_get_container_name():
    with mock.patch("src.Kathara.setting.Setting.Setting.get_instance") as mock_setting_get_instance, \
            mock.patch("src.Kathara.utils.get_current_user_name") as mock_get_current_user_name:
        mock_setting_get_instance.return_value = _setting_mock()
        mock_get_current_user_name.return_value = "test-user"

        name = PodmanMachine.get_container_name("pc1", "lab_hash")
        assert name == "dev_prefix_test-user_pc1_lab_hash"
