from sqlalchemy import Column, Integer, String, BigInteger, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from database import Base


# TODO: добавить поля, генерируемые ChatGPT
class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, index=True)
    channel_description = Column(String)
    channel_idea = Column(String)
    naming_free_uses = Column(Integer, default=2)
    shorts_free_uses = Column(Integer, default=2)
    video_free_uses = Column(Integer, default=2)
    seo_free_uses = Column(Integer, default=2)
    subscriptions = relationship("Subscription", back_populates="user")  # Добавляем отношение к подпискам


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.id'))  # Внешний ключ для связи с таблицей пользователей
    order_id = Column(String, nullable=False)
    status = Column(String, default="demo")
    expiration_date = Column(DateTime, nullable=True)  # Дата истечения подписки
    user = relationship("User", back_populates="subscriptions")  # Обратное отношение к пользователю
