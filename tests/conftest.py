import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from mythweaver.config import Settings
from mythweaver.db import Store
from mythweaver.service import Service


@pytest.fixture
def store():
    settings = Settings()
    schema = "mw_test_" + uuid4().hex
    assert re.fullmatch(r"mw_test_[a-f0-9]+", schema)
    root = create_engine(settings.database_url)
    with root.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    value = Store(settings.database_url)
    value.engine.dispose()
    value.engine = create_engine(settings.database_url, connect_args={"options": f"-csearch_path={schema}"})
    value.session.configure(bind=value.engine)
    value.initialize()
    yield value
    value.engine.dispose()
    with root.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    root.dispose()


@pytest.fixture
def service(store):
    return Service(store)


@pytest.fixture
def game(service):
    return service.create_game("空白测试", "无战斗、背包、骰子或阶段")

