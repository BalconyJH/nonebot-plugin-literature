import time
from pathlib import Path

from nonebot import require, on_command
from nonebot.internal.adapter import Event
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

from .utils import load_xml, atom_parser

require("nonebot_plugin_saa")
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_localstore")

import nonebot_plugin_saa as saa
from nonebot_plugin_htmlrender import template_to_pic

from .config import Config, plugin_config  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="nonebot_plugin_literature",
    description="Nonebot plugin for literature.",
    usage="""\
    """,
    type="application",
    supported_adapters=inherit_supported_adapters("nonebot_plugin_saa"),
    config=Config,
)

literature = on_command("literature", aliases={"文献", "文献查询"}, priority=5)


@literature.handle()
async def _(event: Event):
    xml = Path(__file__).parent / "response.xml"
    data = await atom_parser(await load_xml(xml))
    template_path = str(Path(__file__).parent / "templates")
    template_name = "test.html.jinja"
    start_time = time.time()
    pic = await template_to_pic(
        template_path=template_path,
        template_name=template_name,
        templates={"feed": data},
        pages={
            "viewport": {"width": 600, "height": 300},
            "base_url": f"file://{template_path}",
        },
        wait=2,
    )
    end_time = time.time()
    duration = end_time - start_time  # 计算持续时间

    msg_factory = saa.MessageFactory(saa.Image(pic))
    await literature.send(str(duration))
    await msg_factory.finish()
