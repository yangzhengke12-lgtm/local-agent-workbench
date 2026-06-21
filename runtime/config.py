"""Runtime 配置：环境变量、provider 注册、模型路由常量。

特性：
- import 不要求任何 API key 存在；缺 key 时只在真实 LLM 调用处报明确错误
- provider client 懒加载——只在首次使用时创建
- 厂商路由只依赖配置表，不做实际连接
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── 环境变量 ────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = "https://api.minimax.chat/v1"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

# ── 模型路由表 ──────────────────────────────────────────
MODEL_TIERS = {
    "simple":   ("deepseek", "deepseek-v4-pro[1M]"),
    "normal":   ("deepseek", "deepseek-v4-pro[1M]"),
    "complex":  ("deepseek", "deepseek-v4-pro[1M]"),
    "major":    ("gpt", "gpt-5.5"),
}

UPGRADE_TARGET = ("gpt", "gpt-5.4")

FALLBACK_COMPLEX = ("dashscope", "qwen-plus")
FALLBACK_MAJOR = ("minimax", "MiniMax-M2.7")

MANAGER_DEFAULT_MODEL = ("deepseek", "deepseek-v4-pro[1M]")
MANAGER_COMPLEX_MODEL = ("deepseek", "deepseek-v4-pro[1M]")
MANAGER_MAJOR_MODEL = ("gpt", "gpt-5.5")

DEFAULT_MODEL = "deepseek-v4-pro[1M]"
DEFAULT_API_KEY = DEEPSEEK_API_KEY
DEFAULT_BASE_URL = DEEPSEEK_BASE_URL

APP_VERSION = "v4.2"
APP_RUNTIME_NAME = "Agentic Workflow Runtime"

# ── Provider 注册表（懒初始化） ─────────────────────────
PROVIDERS: dict[str, dict] = {}


class MissingProviderClient:
    """占位 client：让桌面端可启动，并在真实调用时给出清晰错误。"""

    def __init__(self, provider_name: str, env_var: str):
        self.provider_name = provider_name
        self.env_var = env_var
        self.messages = self
        self.chat = self
        self.completions = self

    def create(self, *args, **kwargs):
        raise RuntimeError(
            f"{self.provider_name} provider 未配置：缺少环境变量 {self.env_var}。"
            "请在 .env 或系统环境变量中配置后重新启动桌面端。"
        )


def _init_providers() -> dict:
    """懒初始化所有 provider client。模块 import 时不会创建任何连接。

    只在以下条件创建 provider：
    - DeepSeek: ANTHROPIC_API_KEY 非空
    - DashScope: DASHSCOPE_API_KEY 非空
    - MiniMax: MINIMAX_API_KEY 非空
    - GPT: OPENAI_API_KEY 和 OPENAI_BASE_URL 均非空
    """
    global PROVIDERS
    if PROVIDERS:
        return PROVIDERS

    if DEEPSEEK_API_KEY:
        from anthropic import Anthropic

        PROVIDERS["deepseek"] = {
            "type": "anthropic",
            "client": Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL),
            "base_url": DEEPSEEK_BASE_URL or "",
        }

    if DASHSCOPE_API_KEY:
        try:
            from openai import OpenAI
            PROVIDERS["dashscope"] = {
                "type": "openai",
                "client": OpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL),
                "base_url": DASHSCOPE_BASE_URL,
            }
        except ImportError:
            pass

    if MINIMAX_API_KEY:
        try:
            from openai import OpenAI
            PROVIDERS["minimax"] = {
                "type": "openai",
                "client": OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL),
                "base_url": MINIMAX_BASE_URL,
            }
        except ImportError:
            pass

    if OPENAI_API_KEY and OPENAI_BASE_URL:
        try:
            from openai import OpenAI
            PROVIDERS["gpt"] = {
                "type": "openai",
                "client": OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL),
                "base_url": OPENAI_BASE_URL,
            }
        except ImportError:
            pass

    return PROVIDERS


def get_provider(key: str) -> dict | None:
    """获取指定 provider，不存在返回 None。"""
    _init_providers()
    return PROVIDERS.get(key)


def get_default_client():
    """获取默认 DeepSeek client。"""
    _init_providers()
    provider = PROVIDERS.get("deepseek")
    if provider:
        return provider["client"]
    return MissingProviderClient("DeepSeek/Anthropic", "ANTHROPIC_API_KEY")


def get_provider_status() -> dict:
    """返回可展示给桌面端的 provider 配置状态，不泄露 API key。"""
    _init_providers()
    return {
        "deepseek": {
            "configured": bool(DEEPSEEK_API_KEY),
            "base_url": DEEPSEEK_BASE_URL or "",
            "default_model": DEFAULT_MODEL,
            "env": "ANTHROPIC_API_KEY",
        },
        "dashscope": {
            "configured": bool(DASHSCOPE_API_KEY),
            "base_url": DASHSCOPE_BASE_URL,
            "default_model": FALLBACK_COMPLEX[1],
            "env": "DASHSCOPE_API_KEY",
        },
        "minimax": {
            "configured": bool(MINIMAX_API_KEY),
            "base_url": MINIMAX_BASE_URL,
            "default_model": FALLBACK_MAJOR[1],
            "env": "MINIMAX_API_KEY",
        },
        "gpt": {
            "configured": bool(OPENAI_API_KEY and OPENAI_BASE_URL),
            "base_url": OPENAI_BASE_URL or "",
            "default_model": MANAGER_MAJOR_MODEL[1],
            "env": "OPENAI_API_KEY + OPENAI_BASE_URL",
        },
    }
