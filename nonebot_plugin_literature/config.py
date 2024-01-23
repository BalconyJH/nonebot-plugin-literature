import re

from nonebot import get_driver
from nonebot_plugin_localstore import get_data_dir
from pydantic import Extra, Field, HttpUrl, BaseModel, validator

DATA_DIR = get_data_dir("nonebot_plugin_literature")


class Config(BaseModel, extra=Extra.ignore):
    proxy: HttpUrl = Field(None, description="HTTP proxy to use for requests.")
    timeout: int = Field(30, description="Timeout for web requests in seconds.")
    literature_render: str = Field("htmlrender", description="Render type for literature.")

    @validator("literature_render")
    def check_literature_render(cls, values):
        if values["literature_render"] not in ["htmlrender", "PIL"]:
            raise ValueError("literature_render must be one of 'htmlrender' or 'PIL'")
        return values

    @validator("proxy")
    def check_proxy(cls, values):
        if not re.match(r"^http://[^\s:]+:[0-9]+$", values):
            raise ValueError("proxy 必须是 http://xxx:xxx 格式")
        return {"http": values, "https": values}


global_config = get_driver().config
plugin_config = Config.parse_obj(global_config)
