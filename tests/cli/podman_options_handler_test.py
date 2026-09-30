import sys
from unittest import mock

sys.path.insert(0, './')

from src.Kathara.cli.ui.setting.SettingsMenuFactory import SettingsMenuFactory  # noqa: F401 (avoids a circular import)
from src.Kathara.cli.ui.setting.PodmanOptionsHandler import PodmanOptionsHandler
from src.Kathara.trdparty.consolemenu import ConsoleMenu, MenuFormatBuilder


@mock.patch("src.Kathara.setting.Setting.Setting.get_instance")
def test_add_items_network_plugin(mock_setting_get_instance):
    mock_setting_get_instance.return_value.network_plugin = "katharanp_vde"
    menu = ConsoleMenu("Settings")
    handler = PodmanOptionsHandler()
    handler.set_menu_factory(mock.Mock())

    handler.add(menu, MenuFormatBuilder())

    assert len(menu.items) == 1
    plugin_item = menu.items[0]
    assert plugin_item.text == "Choose Podman Network Plugin"
    choices = plugin_item.submenu.items
    assert [c.text for c in choices[:2]] == ["katharanp_vde", "katharanp"]
    assert [c.args for c in choices[:2]] == [["network_plugin", "katharanp_vde"], ["network_plugin", "katharanp"]]
