"""Plan 05 / T5：渠道配置加密写入/读取测试。

Spec §8.1：渠道配置（webhook/key/收件地址）必须 **加密存储**（Fernet，config_json =
{"ct": "<密文>"} + key_version 单列），明文不入库不入日志。写入路径必须与 Plan 04
Notifier._decrypt_config 的读取路径对齐——Notifier 读 raw['ct'] + ch_row.key_version，
故写入必须把密文塞进 {"ct": ...} 且 key_version 记真实加密版本号，否则推送时解密失败
导致「中奖静默漏通知」（spec §10）。

IDOR：每用户只能 CRUD 自己的渠道（spec §6.3），current_user 依赖 + WHERE user_id 保证。
"""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import NotificationChannel, User


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """注入有效 env（CRYPTO_KEY_V1 须是真实 Fernet key，CryptoService 构造即校验）。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def _client(db_engine):
    def _override():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[get_session_dep] = _override
    return TestClient(app)


def _auth_client(db_engine, uid: int) -> TestClient:
    client = _client(db_engine)
    token = create_session_token(user_id=uid, role='user')
    client.cookies.set(COOKIE_NAME, token)
    return client


def _make_user(db_engine, username: str) -> int:
    with Session(db_engine) as s:
        u = User(username=username, password_hash='x', role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


# POST /channels 是已登录 state-changing 路由——挂 verify_csrf（spec §4.3），故 happy-path
# 写入测试统一用此 helper 设齐 csrf cookie + header（double-submit）。
_CSRF_TOKEN = 'csrf-double-submit-token'


def _auth_csrf_client(db_engine, uid: int) -> TestClient:
    """已登录 + 已带 csrf cookie 的 TestClient（double-submit 双件齐全）。"""
    client = _auth_client(db_engine, uid)
    client.cookies.set('csrf_token', _CSRF_TOKEN)
    client.headers[CSRF_HEADER] = _CSRF_TOKEN
    return client


def test_save_bark_channel_encrypts(db_engine):
    """POST /channels：config 明文不入库，存成 {"ct": <密文>} 且 key_version >= 1。"""
    uid = _make_user(db_engine, 'u1')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'secret-key-abc', 'url': 'https://api.day.app'}},
    )
    assert r.status_code == 201, r.text
    with Session(db_engine) as s:
        ch = s.exec(
            select(NotificationChannel).where(NotificationChannel.user_id == uid)
        ).first()
        assert ch is not None
        stored = json.loads(ch.config_json)
        assert 'ct' in stored
        # 明文不入库：原始 key 串绝不可出现在存储的 config_json 中。
        assert 'secret-key-abc' not in ch.config_json
        assert ch.key_version >= 1


def test_list_channels_returns_plaintext(db_engine):
    """GET /channels：读取时解密回明文（与 Notifier._decrypt_config 同路径）。"""
    uid = _make_user(db_engine, 'u2')
    client = _auth_csrf_client(db_engine, uid)
    client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'secret-key-abc', 'url': 'https://api.day.app'}},
    )
    r = client.get('/channels')
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    cfg = rows[0]['config']
    assert cfg['key'] == 'secret-key-abc'  # 解密回明文
    assert cfg['url'] == 'https://api.day.app'


def test_channel_isolated_by_user(db_engine):
    """IDOR（spec §6.3）：u2 读不到 u1 的渠道。"""
    u1 = _make_user(db_engine, 'alice')
    u2 = _make_user(db_engine, 'bob')
    c1 = _auth_csrf_client(db_engine, u1)
    c1.post('/channels', json={'type': 'bark', 'config': {'key': 'k1', 'url': 'u'}})
    c2 = _auth_client(db_engine, u2)
    assert c2.get('/channels').json() == []


def test_list_channels_requires_auth(db_engine):
    """未登录访问 /channels → 401（current_user 依赖强制）。"""
    client = _client(db_engine)
    assert client.get('/channels').status_code == 401


def test_save_roundtrip_decrypts_to_same_config(db_engine):
    """写入→读取往返一致：存什么取什么（加密/解密对称，不丢字段）。"""
    uid = _make_user(db_engine, 'u3')
    client = _auth_csrf_client(db_engine, uid)
    payload = {
        'type': 'feishu',
        'config': {'webhook': 'https://open.feishu.cn/hook/x', 'secret': 'sign-secret'},
    }
    r = client.post('/channels', json=payload)
    assert r.status_code == 201, r.text
    rows = client.get('/channels').json()
    assert rows[0]['type'] == 'feishu'
    assert rows[0]['config']['webhook'] == 'https://open.feishu.cn/hook/x'
    assert rows[0]['config']['secret'] == 'sign-secret'
    assert rows[0]['enabled'] is True


def test_list_skips_corrupt_row_without_500(db_engine, caplog):
    """GET /channels：单行密文损坏/key_version 失配不应让整个列表 500。

    回归 Notifier._decrypt_config 契约（notifier.py:273-287 try/except→跳过+WARNING）：
    GET 路径同样必须 try/except per-row decrypt，损坏行跳过且记 WARNING，健康行照常返回。
    否则一条坏数据让用户整张渠道列表无法加载，且运维只能从前端 500 报错里察觉。
    """
    import logging

    uid = _make_user(db_engine, 'u4')
    client = _auth_csrf_client(db_engine, uid)
    # 先存一条健康 bark 渠道。
    client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'healthy-key', 'url': 'https://api.day.app'}},
    )
    # 直接插入一条「密文损坏」行：模拟手改 DB / 旧 key 轮换错位。
    with Session(db_engine) as s:
        s.add(
            NotificationChannel(
                user_id=uid,
                type='bark',
                # 不是合法 Fernet token，decrypt 必抛 InvalidToken。
                config_json=json.dumps({'ct': 'not-a-valid-fernet-ciphertext!!!'}),
                enabled=True,
                key_version=1,
            )
        )
        s.commit()

    with caplog.at_level(logging.WARNING):
        r = client.get('/channels')

    # 关键：坏行被跳过，列表照常 200，健康行仍在，不返回 500。
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1, f'应只剩健康行，实际 {rows}'
    assert rows[0]['config']['key'] == 'healthy-key'
    # 损坏行被记 WARNING（含 channel 标识便于运维定位），不静默。
    assert any('decrypt' in rec.message.lower() for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]


# ---------------------------------------------------------------------------
# Review round 2 修复：
#   1. POST /channels 必须挂 verify_csrf（与 /auth/logout 同为已登录 state-changing）。
#   2. ChannelIn.config 边界校验（bark 需 key，feishu 需 webhook，email 需 address）。
#   3. save_channel/list_channels 返回结构化 ChannelOut（OpenAPI schema 一致）。
#   4. list_channels 逐行 JSON 解析/解密统一 try/except——单行坏 config_json 不让整
#      张列表 500（回归测试：模拟 config_json 非 JSON 字符串）。
# ---------------------------------------------------------------------------

def test_save_channel_rejected_without_csrf_token(db_engine):
    """POST /channels 不带 X-CSRF-Token header → 403（spec §4.3）。

    CSRF 伪造可让攻击者把用户 webhook 改指向攻击者端点劫持中奖通知、或污染配置，
    与 /auth/logout 同为已登录 state-changing 路由，须强制 double-submit。
    """
    uid = _make_user(db_engine, 'csrf-victim')
    client = _auth_client(db_engine, uid)
    # 已登录（带 session cookie）但无 csrf cookie/header。
    r = client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'k', 'url': 'https://api.day.app'}},
    )
    assert r.status_code == 403, r.text


def test_save_channel_allowed_with_valid_csrf_token(db_engine):
    """POST /channels 带 X-CSRF-Token header 与 csrf_token cookie 一致 → 放行（201）。"""
    uid = _make_user(db_engine, 'csrf-ok')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'k', 'url': 'https://api.day.app'}},
    )
    assert r.status_code == 201, r.text


def test_save_bark_rejected_when_config_missing_key(db_engine):
    """bark 渠道 config 缺 key → 400（边界校验，把坏配置挡在落库前）。

    BarkChannel.send 直接取 config['key']，缺 key 会推一个不可用渠道——延迟到推送
    阶段才暴露即 spec §10「静默漏通知」。须在写入时 fail-fast。
    """
    uid = _make_user(db_engine, 'cfg-bark')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/channels', json={'type': 'bark', 'config': {'url': 'https://api.day.app'}})
    assert r.status_code == 400, r.text


def test_save_feishu_rejected_when_config_missing_webhook(db_engine):
    """feishu 渠道 config 缺 webhook → 400（FeishuChannel.send 必取 config['webhook']）。"""
    uid = _make_user(db_engine, 'cfg-feishu')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/channels', json={'type': 'feishu', 'config': {'secret': 's'}})
    assert r.status_code == 400, r.text


def test_save_email_rejected_when_config_missing_address(db_engine):
    """email 渠道 config 缺 address → 400（EmailChannel.send 必取 config['address']）。"""
    uid = _make_user(db_engine, 'cfg-email')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/channels', json={'type': 'email', 'config': {}})
    assert r.status_code == 400, r.text


def test_save_channel_response_has_full_schema(db_engine):
    """POST /channels 返回结构化 ChannelOut（id/type/config/enabled），OpenAPI schema 一致。

    替换原 dict[str, object] 宽松返回——前端/Plan 06 可依赖稳定字段名。
    """
    uid = _make_user(db_engine, 'schema')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'k', 'url': 'https://api.day.app'}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body.keys()) >= {'id', 'type', 'enabled'}
    assert body['type'] == 'bark'
    assert body['enabled'] is True


def test_list_channels_skips_row_with_non_json_config_without_500(db_engine, caplog):
    """GET /channels：单行 config_json 非合法 JSON / 结构异常 → 跳过 + WARNING，不让整列表 500。

    回归 hunter finding：逐行的 JSON 解析、结构校验、解密须统一包进同一 try/except，
    单行坏数据（如手改 DB 写成非 JSON 字符串、或 {'ct':...} 结构缺失）记 WARNING 后
    continue，健康行照常返回。
    """
    import logging

    uid = _make_user(db_engine, 'row-bad')
    client = _auth_csrf_client(db_engine, uid)
    # 先存一条健康 bark 渠道。
    client.post(
        '/channels',
        json={'type': 'bark', 'config': {'key': 'healthy-key', 'url': 'https://api.day.app'}},
    )
    # 直接插入「config_json 非合法 JSON」行：模拟手改 DB / 损坏写入。
    with Session(db_engine) as s:
        s.add(
            NotificationChannel(
                user_id=uid,
                type='bark',
                # 不是合法 JSON，json.loads 必抛 JSONDecodeError。
                config_json='this-is-not-json{{{',
                enabled=True,
                key_version=1,
            )
        )
        s.commit()

    with caplog.at_level(logging.WARNING):
        r = client.get('/channels')

    # 关键：坏行被跳过，列表照常 200，健康行仍在，不返回 500。
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1, f'应只剩健康行，实际 {rows}'
    assert rows[0]['config']['key'] == 'healthy-key'
