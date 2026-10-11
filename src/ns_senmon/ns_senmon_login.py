"""ns-senmon.com (Simple Membership) にログインしたrequests.Sessionを返す。

環境変数:
    NS_SENMON_USER  ユーザー名またはメールアドレス
    NS_SENMON_PASS  パスワード
"""

import os

import requests

BASE_URL = "https://ns-senmon.com"
LOGIN_URL = f"{BASE_URL}/?page_id=144"
TIMEOUT_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# 未ログイン時のページには、bodyクラスにこの文言が入る
LOGGED_OUT_MARKER = "ログインしていません"


def is_logged_in(session):
    response = session.get(f"{BASE_URL}/", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return LOGGED_OUT_MARKER not in response.text


def login(session, username, password):
    response = session.post(
        LOGIN_URL,
        data={
            "swpm_login_origination_flag": "1",
            "swpm_user_name": username,
            "swpm_password": password,
            "swpm-login": "Log In",
        },
        timeout=TIMEOUT_SECONDS,
        allow_redirects=True,
    )
    response.raise_for_status()
    print(f"ログイン応答: {response.status_code} {response.url}")

    if not is_logged_in(session):
        raise RuntimeError("ログインに失敗しました（ユーザー名またはパスワードを確認してください）")
    return session


def create_logged_in_session():
    username = os.environ.get("NS_SENMON_USER")
    password = os.environ.get("NS_SENMON_PASS")
    if not username or not password:
        raise RuntimeError("NS_SENMON_USER / NS_SENMON_PASS 環境変数を設定してください")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return login(session, username, password)


if __name__ == "__main__":
    create_logged_in_session()
    print("ログイン成功")
