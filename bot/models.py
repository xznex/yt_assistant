from sqlalchemy import Column, Integer, String, BigInteger, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship

from database import Base


# TODO: добавить поля, генерируемые ChatGPT
class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, index=True)
    channel_description = Column(String)
    channel_idea = Column(String)

    analytics_channel_description = Column(Text)
    analytics_channel_audience = Column(Text)
    analytics_channel_goals = Column(Text)
    analytics_words = Column(Text)
    analytics_links = Column(Text)
    analytics_channel_characteristics = Column(Text)

    naming_free_uses = Column(Integer, default=2)
    shorts_free_uses = Column(Integer, default=2)
    video_free_uses = Column(Integer, default=2)
    seo_free_uses = Column(Integer, default=2)

    analytics_attempts = Column(Integer, default=0)

    subscriptions = relationship("Subscription", back_populates="user")  # Добавляем отношение к подпискам


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.id'))  # Внешний ключ для связи с таблицей пользователей
    order_id = Column(BigInteger, nullable=False)
    subscription_id = Column(BigInteger, nullable=False)
    tariff = Column(String, default="demo")
    email = Column(String, nullable=False)
    expiration_date = Column(DateTime, nullable=False)  # Дата истечения подписки
    user = relationship("User", back_populates="subscriptions")  # Обратное отношение к пользователю
