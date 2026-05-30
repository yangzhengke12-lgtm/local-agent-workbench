#!/usr/bin/env python3
"""
校园网自动认证脚本 — 锐捷 RG-SAM+ Portal 系统
放在 Windows 任务计划程序「启动时」触发，开机自动联网。
"""
import time
import urllib.parse
import urllib.request
from datetime import datetime

AUTH_HOST = "172.21.2.10:8080"
MAX_RETRIES = 3
RETRY_INTERVAL = 5


def tprint(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def is_online():
    """
    用 HTTP 检测是否已联网。
    访问 detectportal.firefox.com —— 如果正常返回 success 说明已联网，
    如果被 302 重定向到 portal 说明需要认证。
    """
    url = "http://detectportal.firefox.com/success.txt"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        # 请求成功 = 没被重定向 = 已联网
        body = resp.read().decode()
        if "success" in body.lower():
            return True, None
        return True, None
    except urllib.error.HTTPError as e:
        # 被重定向到 portal → 需要认证
        location = e.headers.get("Location", "")
        if location:
            tprint(f"需要认证，portal: {location[:80]}...")
            return False, location
        return True, None
    except Exception as e:
        tprint(f"网络检测异常: {e}")
        return False, None


def login():
    for attempt in range(1, MAX_RETRIES + 1):
        tprint(f"--- 第 {attempt}/{MAX_RETRIES} 次 ---")

        online, portal_url = is_online()
        if online:
            tprint("已联网，无需认证")
            return True

        if portal_url is None:
            tprint("无法获取 portal URL，等待重试...")
            time.sleep(RETRY_INTERVAL)
            continue

        # 提取 queryString
        parsed = urllib.parse.urlparse(portal_url)
        query_string = parsed.query
        if not query_string:
            tprint("portal URL 无参数，等待重试...")
            time.sleep(RETRY_INTERVAL)
            continue

        # 构造认证请求
        auth_url = (
            f"http://{AUTH_HOST}/eportal/InterFace.do"
            f"?method=login"
            f"&queryString={urllib.parse.quote(query_string)}"
        )

        req = urllib.request.Request(
            auth_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": portal_url,
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            body = resp.read().decode("utf-8", errors="replace")
            tprint(f"响应: {body[:200]}")

            if "success" in body.lower():
                tprint("认证成功")
                time.sleep(3)
                if is_online()[0]:
                    tprint("网络已通")
                    return True
            else:
                tprint("认证未确认成功")
        except Exception as e:
            tprint(f"请求失败: {e}")

        if attempt < MAX_RETRIES:
            tprint(f"等待 {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)

    return False


if __name__ == "__main__":
    tprint("校园网自动认证启动")
    if login():
        tprint("完成")
    else:
        tprint("认证失败，请手动连接")
