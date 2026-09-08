import os
import sys
from pathlib import Path


no_proxy_hosts = []
for variable in ("NO_PROXY", "no_proxy"):
    no_proxy_hosts.extend(item.strip() for item in os.environ.get(variable, "").split(",") if item.strip())
for host in ("127.0.0.1", "localhost", "::1"):
    if host not in no_proxy_hosts:
        no_proxy_hosts.append(host)
no_proxy_value = ",".join(no_proxy_hosts)
os.environ["NO_PROXY"] = no_proxy_value
os.environ["no_proxy"] = no_proxy_value


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from prompt_studio_ui import capture_prompt_component, inject_inline_before_negative, on_app_started, on_ui_tabs
from modules import script_callbacks

script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_app_started(on_app_started)
script_callbacks.on_after_component(capture_prompt_component)
script_callbacks.on_before_component(inject_inline_before_negative)
