from sqlalchemy import create_engine # to create a connection to the database
from sqlalchemy.orm import declarative_base, sessionmaker # to create a base class for our models and to create a session factory
from sqlalchemy.pool import QueuePool # to manage database connections
import os 
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Engine (connection pool) configuration
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,  # maximum number of connections in the pool
    max_overflow=10,  # maximum number of connections that can be created beyond the pool_size
    pool_timeout=30,  # maximum time to wait for a connection from the pool
    pool_recycle=1800,  # recycle connections after this many seconds
    echo=True # enable SQL query logging for debugging purposes
)

# Session Factory
SessionLocal = sessionmaker(
    autocommit=False, # we want to control when transactions are committed
    autoflush=False, # we want to control when changes are flushed to the database
    bind=engine # bind the session to our engine (connection pool )
)

# Base class for our models
Base = declarative_base()

# Dependency to get a database session
def get_db():
    db = SessionLocal() # create a new session
    try:
        yield db # yield the session to be used in the endpoint
    finally:
        db.close() # close the session after use to return it to the pool