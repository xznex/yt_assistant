from sqlalchemy import Column, Integer, String

from database import Base


# TODO: добавить поля, генерируемые ChatGPT
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    channel_description = Column(String)
    channel_idea = Column(String)
