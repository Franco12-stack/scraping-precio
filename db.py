from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    create_engine, String, DateTime,
    Date, ForeignKey, Text, Boolean, func
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column,
    relationship, Session
)

engine = create_engine("sqlite:///./epagos.db", connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id:            Mapped[int]      = mapped_column(primary_key=True)
    username:      Mapped[str]      = mapped_column(String(64), unique=True)
    password_hash: Mapped[str]      = mapped_column(String(200))
    rol:           Mapped[str]      = mapped_column(String(20), default="operador")
    activo:        Mapped[bool]     = mapped_column(Boolean, default=True)
    creado_en:     Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Cliente(Base):
    __tablename__ = "clientes"

    id:                    Mapped[int]      = mapped_column(primary_key=True)
    identificador_cliente: Mapped[str]      = mapped_column(String(64), unique=True)
    nombre:                Mapped[str]           = mapped_column(String(100))
    apellido:              Mapped[str]           = mapped_column(String(100))
    email:                 Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    dni:                   Mapped[Optional[int]] = mapped_column(nullable=True)
    cuit:                  Mapped[int]
    creado_en:             Mapped[datetime] = mapped_column(DateTime, default=func.now())

    cuentas: Mapped[List["Cuenta"]] = relationship(back_populates="cliente", cascade="all, delete")
    cobros:  Mapped[List["Cobro"]]  = relationship(back_populates="cliente", cascade="all, delete")


class Cuenta(Base):
    __tablename__ = "cuentas"

    id:                   Mapped[int]           = mapped_column(primary_key=True)
    cliente_id:           Mapped[int]           = mapped_column(ForeignKey("clientes.id"))
    identificador_cuenta: Mapped[str]           = mapped_column(String(64), unique=True)
    alias:                Mapped[Optional[str]] = mapped_column(String(200))
    cbu:                  Mapped[Optional[str]] = mapped_column(String(22))
    sincronizado_en:      Mapped[datetime]      = mapped_column(DateTime, default=func.now())

    cliente: Mapped["Cliente"]      = relationship(back_populates="cuentas")
    cobros:  Mapped[List["Cobro"]]  = relationship(back_populates="cuenta")


class Cobro(Base):
    __tablename__ = "cobros"

    id:               Mapped[int]           = mapped_column(primary_key=True)
    cliente_id:       Mapped[int]           = mapped_column(ForeignKey("clientes.id"))
    cuenta_id:        Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"))
    numero_operacion: Mapped[str]           = mapped_column(String(64), unique=True)
    importe:          Mapped[float]
    descripcion:      Mapped[str]           = mapped_column(String(200), default="Cobro recurrente")
    tipo:             Mapped[str]           = mapped_column(String(20), default="inmediato")
    fecha_cobro:      Mapped[Optional[date]] = mapped_column(Date)
    estado:           Mapped[str]           = mapped_column(String(30), default="enviado")
    id_transaccion:   Mapped[Optional[str]] = mapped_column(String(64))
    error:            Mapped[Optional[str]] = mapped_column(Text)
    creado_en:        Mapped[datetime]      = mapped_column(DateTime, default=func.now())

    cliente: Mapped["Cliente"]          = relationship(back_populates="cobros")
    cuenta:  Mapped[Optional["Cuenta"]] = relationship(back_populates="cobros")


def init_db():
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
