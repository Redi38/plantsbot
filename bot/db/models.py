from datetime import datetime, timezone

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    ungrouped_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    groups: Mapped[list["Group"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    plants: Mapped[list["Plant"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def display_name(self) -> str:
        """username в приоритете (как в самом Telegram), иначе full_name,
        иначе id — если пользователь ни разу не присылал апдейт с этими полями."""
        if self.username:
            return f"Пользователь @{self.username}"
        if self.full_name:
            return f"Пользователь {self.full_name}"
        return f"Пользователь #{self.telegram_id}"


class AiLog(Base):
    """Запись одного обращения пользователя к ИИ-агенту (свободный текст,
    не команда) — что спросили и что агент понял/ответил. Нужно для
    админки: смотреть реальные формулировки пользователей и промахи
    распознавания, чтобы дорабатывать системный промпт ai_service.
    user_id хранится без ForeignKey/CASCADE намеренно: лог должен
    переживать очистку базы пользователя (clear_user_plants чистит
    только groups/plants, самого пользователя не трогает), чтобы
    история для анализа не терялась."""

    __tablename__ = "ai_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    user_text: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plant_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_group_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship(back_populates="groups")
    plants: Mapped[list["Plant"]] = relationship(back_populates="group")


class Plant(Base):
    __tablename__ = "plants"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(150))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship(back_populates="plants")
    group: Mapped["Group | None"] = relationship(back_populates="plants")
