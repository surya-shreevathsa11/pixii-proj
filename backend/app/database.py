from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

_url = settings.database_url
_sqlite = _url.startswith("sqlite")

_engine_kwargs: dict = {}
if _sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    _engine_kwargs["pool_pre_ping"] = False
else:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(_url, **_engine_kwargs)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
