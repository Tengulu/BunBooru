import os
from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    DateTime, ForeignKey, BigInteger, UniqueConstraint, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://booru:booru@localhost:5432/booru")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    category = Column(String(100), default="general")
    post_count = Column(Integer, default=0)
    posts = relationship("PostTag", back_populates="tag")


class TagAlias(Base):
    __tablename__ = "tag_aliases"
    id = Column(Integer, primary_key=True)
    alias = Column(String(255), unique=True, nullable=False)
    canonical = Column(String(255), nullable=False)
    __table_args__ = (UniqueConstraint("alias"),)


class BogusTag(Base):
    __tablename__ = "bogus_tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)


class CreatorAutotag(Base):
    __tablename__ = "creator_autotags"
    id = Column(Integer, primary_key=True)
    creator = Column(String(255), unique=True, nullable=False)  # without cr: prefix
    tags = Column(Text, nullable=False)  # space-separated tag strings with prefixes


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    hash = Column(String(64), unique=True, nullable=False)
    filename = Column(String(512), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    duration = Column(Integer)
    rating = Column(String(10), default="safe")
    source_url = Column(Text)
    source_site = Column(String(100))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    tags = relationship("PostTag", back_populates="post")


class PostTag(Base):
    __tablename__ = "post_tags"
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    post = relationship("Post", back_populates="tags")
    tag = relationship("Tag", back_populates="posts")

