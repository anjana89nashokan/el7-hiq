"""
SQLAlchemy ORM models for IndeMap database tables.

Maps to IM_ENTITY_CUR, IM_ENTITY_ATTR_CUR, IM_DB, and IM_SCHEMA tables
in the IndeMap SQL Server database. These are read-only mappings — no DDL
is performed against the external database.
"""

from sqlalchemy import BigInteger, Integer, SmallInteger, String, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from typing import Optional, List
from datetime import datetime


class Base(DeclarativeBase):
    pass


class ImDb(Base):
    __tablename__ = "IM_DB"

    im_db_sk: Mapped[int] = mapped_column("IM_DB_SK", BigInteger, primary_key=True)
    im_db_nm: Mapped[Optional[str]] = mapped_column("IM_DB_NM", String)

    entities: Mapped[List["ImEntityCur"]] = relationship(back_populates="database")

    def __repr__(self) -> str:
        return f"ImDb(sk={self.im_db_sk}, name={self.im_db_nm})"


class ImSchema(Base):
    __tablename__ = "IM_SCHEMA"

    im_schema_sk: Mapped[int] = mapped_column("IM_SCHEMA_SK", SmallInteger, primary_key=True)
    im_schema_nm: Mapped[Optional[str]] = mapped_column("IM_SCHEMA_NM", String)

    entities: Mapped[List["ImEntityCur"]] = relationship(back_populates="schema_ref")

    def __repr__(self) -> str:
        return f"ImSchema(sk={self.im_schema_sk}, name={self.im_schema_nm})"


class ImEntityCur(Base):
    """Maps to IM_ENTITY_CUR — current entity (table) metadata. 18 columns."""
    __tablename__ = "IM_ENTITY_CUR"

    im_entity_sk: Mapped[int] = mapped_column("IM_ENTITY_SK", BigInteger, primary_key=True)
    rw_eff_ts: Mapped[Optional[datetime]] = mapped_column("RW_EFF_TS", DateTime)
    im_entity_log_nm: Mapped[Optional[str]] = mapped_column("IM_ENTITY_LOG_NM", String)
    im_entity_tp_cd: Mapped[Optional[str]] = mapped_column("IM_ENTITY_TP_CD", String)
    im_entity_phys_nm: Mapped[Optional[str]] = mapped_column("IM_ENTITY_PHYS_NM", String)
    im_entity_dsc: Mapped[Optional[str]] = mapped_column("IM_ENTITY_DSC", String)
    rw_exp_ts: Mapped[Optional[datetime]] = mapped_column("RW_EXP_TS", DateTime)
    im_trans_sk: Mapped[Optional[int]] = mapped_column("IM_TRANS_SK", BigInteger)
    cre_use_id: Mapped[Optional[str]] = mapped_column("CRE_USE_ID", String)
    cre_pgm_nm: Mapped[Optional[str]] = mapped_column("CRE_PGM_NM", String)
    cre_ts: Mapped[Optional[datetime]] = mapped_column("CRE_TS", DateTime)
    last_upd_use_id: Mapped[Optional[str]] = mapped_column("LAST_UPD_USE_ID", String)
    last_upd_pgm_nm: Mapped[Optional[str]] = mapped_column("LAST_UPD_PGM_NM", String)
    last_upd_ts: Mapped[Optional[datetime]] = mapped_column("LAST_UPD_TS", DateTime)
    del_ind: Mapped[Optional[str]] = mapped_column("DEL_IND", String)
    im_entity_bus_nm: Mapped[Optional[str]] = mapped_column("IM_ENTITY_BUS_NM", String)

    # Foreign keys
    im_db_sk: Mapped[Optional[int]] = mapped_column("IM_DB_SK", BigInteger, ForeignKey("IM_DB.IM_DB_SK"))
    im_schema_sk: Mapped[Optional[int]] = mapped_column("IM_SCHEMA_SK", SmallInteger, ForeignKey("IM_SCHEMA.IM_SCHEMA_SK"))

    # Relationships
    database: Mapped[Optional["ImDb"]] = relationship(back_populates="entities")
    schema_ref: Mapped[Optional["ImSchema"]] = relationship(back_populates="entities")
    attributes: Mapped[List["ImEntityAttrCur"]] = relationship(
        back_populates="entity",
        order_by="ImEntityAttrCur.im_entity_colm_ord_no",
    )

    def __repr__(self) -> str:
        return f"ImEntityCur(sk={self.im_entity_sk}, phys_nm={self.im_entity_phys_nm})"


class ImEntityAttrCur(Base):
    """Maps to IM_ENTITY_ATTR_CUR — current entity attribute (column) metadata. 23 columns."""
    __tablename__ = "IM_ENTITY_ATTR_CUR"

    im_entity_colm_sk: Mapped[int] = mapped_column("IM_ENTITY_COLM_SK", BigInteger, primary_key=True)
    rw_eff_ts: Mapped[Optional[datetime]] = mapped_column("RW_EFF_TS", DateTime)
    im_entity_attr_data_tp_cd: Mapped[Optional[str]] = mapped_column("IM_ENTITY_ATTR_DATA_TP_CD", String)
    im_entity_sk: Mapped[int] = mapped_column("IM_ENTITY_SK", BigInteger, ForeignKey("IM_ENTITY_CUR.IM_ENTITY_SK"))
    im_entity_rw_eff_ts: Mapped[Optional[datetime]] = mapped_column("IM_ENTITY_RW_EFF_TS", DateTime)
    im_entity_colm_nm: Mapped[Optional[str]] = mapped_column("IM_ENTITY_COLM_NM", String)
    im_entity_colm_lgc_nm: Mapped[Optional[str]] = mapped_column("IM_ENTITY_COLM_LGC_NM", String)
    im_entity_colm_dsc: Mapped[Optional[str]] = mapped_column("IM_ENTITY_COLM_DSC", String)
    im_entity_colm_ord_no: Mapped[Optional[int]] = mapped_column("IM_ENTITY_COLM_ORD_NO", Integer)
    im_entity_colm_lng_no: Mapped[Optional[int]] = mapped_column("IM_ENTITY_COLM_LNG_NO", Integer)
    im_entity_colm_data_tp_precision_no: Mapped[Optional[int]] = mapped_column("IM_ENTITY_COLM_DATA_TP_PRECISION_NO", Integer)
    im_entity_colm_dflt_val: Mapped[Optional[str]] = mapped_column("IM_ENTITY_COLM_DFLT_VAL", String)
    im_entity_colm_null_ind: Mapped[Optional[str]] = mapped_column("IM_ENTITY_COLM_NULL_IND", String)
    rw_exp_ts: Mapped[Optional[datetime]] = mapped_column("RW_EXP_TS", DateTime)
    im_trans_sk: Mapped[Optional[int]] = mapped_column("IM_TRANS_SK", BigInteger)
    cre_use_id: Mapped[Optional[str]] = mapped_column("CRE_USE_ID", String)
    cre_pgm_nm: Mapped[Optional[str]] = mapped_column("CRE_PGM_NM", String)
    cre_ts: Mapped[Optional[datetime]] = mapped_column("CRE_TS", DateTime)
    last_upd_use_id: Mapped[Optional[str]] = mapped_column("LAST_UPD_USE_ID", String)
    last_upd_pgm_nm: Mapped[Optional[str]] = mapped_column("LAST_UPD_PGM_NM", String)
    last_upd_ts: Mapped[Optional[datetime]] = mapped_column("LAST_UPD_TS", DateTime)
    del_ind: Mapped[Optional[str]] = mapped_column("DEL_IND", String)
    im_entity_colm_fmt_val: Mapped[Optional[str]] = mapped_column("IM_ENTITY_COLM_FMT_VAL", String)

    # Relationships
    entity: Mapped["ImEntityCur"] = relationship(back_populates="attributes")

    def __repr__(self) -> str:
        return f"ImEntityAttrCur(sk={self.im_entity_colm_sk}, name={self.im_entity_colm_nm})"
