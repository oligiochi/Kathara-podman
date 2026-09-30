from . import utils as setting_utils
from ....foundation.cli.ui.setting.OptionsHandler import OptionsHandler
from ....setting.addon.PodmanSettingsAddon import DEFAULTS
from ....trdparty.consolemenu import *
from ....trdparty.consolemenu.items import *


class PodmanOptionsHandler(OptionsHandler):
    def add_items(self, current_menu: ConsoleMenu, menu_formatter: MenuFormatBuilder) -> None:
        # Network Plugin Option
        network_plugin_string = "Choose Podman Network Plugin"
        network_plugin_menu = SelectionMenu(
            strings=[],
            title=network_plugin_string,
            subtitle=setting_utils.current_string("network_plugin"),
            prologue_text="""Choose the Podman Network Plugin used to create collision domains.
                          
                          `katharanp_vde` plugin is based on VDE switches: a userspace switch that forwards every frame.
                          
                          `katharanp` plugin is based on Linux bridges: the data plane is in the kernel, """
                          """but LACP frames cannot cross a Linux bridge.
                          
                          Default is `%s`.""" %
                          DEFAULTS['network_plugin'],
            formatter=menu_formatter
        )

        network_plugin_menu.append_item(
            FunctionItem(
                text="katharanp_vde",
                function=setting_utils.update_setting_value,
                args=["network_plugin", "katharanp_vde"],
                should_exit=True
            )
        )
        network_plugin_menu.append_item(
            FunctionItem(
                text="katharanp",
                function=setting_utils.update_setting_value,
                args=["network_plugin", "katharanp"],
                should_exit=True
            )
        )

        network_plugin_item = SubmenuItem(network_plugin_string, network_plugin_menu, current_menu)

        current_menu.append_item(network_plugin_item)
