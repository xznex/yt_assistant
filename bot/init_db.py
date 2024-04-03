from database import engine, Base
from models import User, Subscription

Base.metadata.create_all(bind=engine)
