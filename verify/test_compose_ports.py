"""docker-compose.yml 可配置宿主端口的回归测试（无需 Docker 守护进程）。

回归点：
- api 容器固定监听 8000、web 固定监听 80，宿主端口分别由
  API_PORT（默认 8000）与 WEB_PORT（默认 8080）插值；
- 覆盖环境变量时宿主端口随之改变、容器端口不变；
- verify 一次性验收服务仍依赖 api 健康与 web 启动。

直接解析 YAML 并模拟 compose 对 ${VAR:-default} 的插值，
不依赖本机是否安装 docker / docker compose。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"

_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(text: str, env: dict[str, str]) -> str:
    """模拟 compose 的 ${VAR:-default} 简单插值（本文件只用到这一种形式）。"""

    def replace(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        if name in env:
            return env[name]
        return default if default is not None else ""

    return _INTERPOLATION.sub(replace, text)


def _rendered_compose(monkeypatch: pytest.MonkeyPatch, *, api_port=None, web_port=None):
    """按给定宿主端口环境渲染 compose 文件并解析为 dict。"""
    monkeypatch.delenv("API_PORT", raising=False)
    monkeypatch.delenv("WEB_PORT", raising=False)
    if api_port is not None:
        monkeypatch.setenv("API_PORT", api_port)
    if web_port is not None:
        monkeypatch.setenv("WEB_PORT", web_port)
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    rendered = _interpolate(raw, dict(os.environ))
    return yaml.safe_load(rendered)


def _host_container_ports(service: dict) -> list[tuple[str, str]]:
    """从 ports 配置取出 (宿主端口, 容器端口)，兼容短语法与长语法。"""
    pairs: list[tuple[str, str]] = []
    for port in service.get("ports", []):
        if isinstance(port, str):
            host, container = port.split(":")
            pairs.append((host, container))
        else:
            pairs.append((str(port["published"]), str(port["target"])))
    return pairs


def test_default_host_ports(monkeypatch: pytest.MonkeyPatch):
    compose = _rendered_compose(monkeypatch)
    assert _host_container_ports(compose["services"]["api"]) == [("8000", "8000")]
    assert _host_container_ports(compose["services"]["web"]) == [("8080", "80")]


def test_host_ports_are_configurable_via_env(monkeypatch: pytest.MonkeyPatch):
    compose = _rendered_compose(monkeypatch, api_port="9001", web_port="9000")
    assert _host_container_ports(compose["services"]["api"]) == [("9001", "8000")]
    assert _host_container_ports(compose["services"]["web"]) == [("9000", "80")]


def test_verify_service_dependencies_unchanged(monkeypatch: pytest.MonkeyPatch):
    compose = _rendered_compose(monkeypatch)
    verify = compose["services"]["verify"]
    assert verify["restart"] == "no"
    depends = verify["depends_on"]
    # 长语法：等待 api 健康、web 启动后再跑一次性验收
    assert depends["api"]["condition"] == "service_healthy"
    assert depends["web"]["condition"] == "service_started"
