from database import engine, Base
from models import User

Base.metadata.create_all(bind=engine)
