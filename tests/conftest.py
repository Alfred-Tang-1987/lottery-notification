import pytest
from pydantic_settings import SettingsConfigDict
from sqlmodel import SQLModel


@pytest.fixture(autouse=True)
def _cookie_secure_off(monkeypatch):
    """全局：测试环境关 cookie secure（TestClient 走 http，secure=True 会导致 cookie 不回传）。

    生产默认 cookie_secure=True（config.py），测试统一经此 fixture 降到 false。
    """
    monkeypatch.setenv('COOKIE_SECURE', 'false')


@pytest.fixture(autouse=True)
def _reset_settings_and_env(monkeypatch):
    """每次测试前：禁用 .env 加载并重置 Settings 缓存，确保测试对 Settings() 的调用只基于当前环境。

    pydantic-settings 默认会读取 .env，当 .env 里存在 JWT_SECRET / CRYPTO_KEY_V1 时
    会污染那些故意测试「缺少必填项」的用例。本 fixture 在测试设置前把 Settings 的
    env_file 指向 None，清空 get_settings 缓存，并删除常见环境变量。
    """
    from app import config as config_mod
    from app.config import reset_settings_cache

    # 禁用 Settings 的 .env 加载（单次覆盖 model_config；后续实例化不读文件）。
    original_model_config = config_mod.Settings.model_config
    config_mod.Settings.model_config = SettingsConfigDict(
        env_file=None, extra='ignore'
    )
    for key in (
        'JWT_SECRET',
        'CRYPTO_KEY_V1',
        'CRYPTO_KEY_V2',
        'SMTP_HOST',
        'SMTP_USER',
        'SMTP_PASS',
        'SMTP_FROM',
        'ADMIN_BARK_KEY',
    ):
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    yield
    # restore
    config_mod.Settings.model_config = original_model_config
    reset_settings_cache()


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """每个测试一个临时 SQLite，建全表；并注入 get_engine 单例指向该引擎，
    让 lifespan 等直接使用 get_engine() 的代码命中隔离测试数据库。
    """
    import app.models  # noqa: F401  注册全部表
    from app.db.engine import apply_sqlite_pragmas, build_engine
    from app.db import session as session_mod

    eng = build_engine(f'sqlite:///{tmp_path}/test.db')
    apply_sqlite_pragmas(eng)
    SQLModel.metadata.create_all(eng)

    # 强制 lifespan / get_db_for_health 等使用测试引擎，防止读到 ./data/lottery.db。
    monkeypatch.setattr(session_mod, '_engine', eng)
    return eng
