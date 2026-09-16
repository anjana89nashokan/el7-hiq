"""
IndeMap Repository — ORM-based data access and DTO mapping.

Encapsulates all IndeMap DB queries using SQLAlchemy ORM and maps
ORM entities to application-level Pydantic DTOs (TargetTable, TargetColumn).
"""

from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
import logging

from models.indemap_orm import ImEntityCur, ImDb
from agents.mapping_ingestion.models import TargetTable, TargetColumn

logger = logging.getLogger(__name__)


class IndemapRepository:

    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_entities_with_columns(
        self, db_name: str, table_names: List[str]
    ) -> List[ImEntityCur]:
        """
        Fetch entities with eager-loaded attributes, database, and schema.
        """
        if not table_names:
            return []

        entities = (
            self.session.query(ImEntityCur)
            .join(ImEntityCur.database)
            .options(
                joinedload(ImEntityCur.attributes),
                joinedload(ImEntityCur.database),
                joinedload(ImEntityCur.schema_ref),
            )
            .filter(ImDb.im_db_nm == db_name)
            .filter(ImEntityCur.im_entity_phys_nm.in_(table_names))
            .all()
        )

        logger.info(
            f"Fetched {len(entities)} entity(ies) for "
            f"{len(table_names)} table(s) in '{db_name}'"
        )
        return entities

    # ------------------------------------------------------------------
    # Mapping: ORM -> DTO
    # ------------------------------------------------------------------

    def to_target_tables(self, entities: List[ImEntityCur]) -> List[TargetTable]:
        """Map a list of ORM entities to TargetTable DTOs."""
        tables = []
        for entity in entities:
            columns = [self._map_column(attr) for attr in entity.attributes]

            db_name = _clean(entity.database.im_db_nm) if entity.database else None
            schema_nm = _clean(entity.schema_ref.im_schema_nm) if entity.schema_ref else None

            tables.append(TargetTable(
                table_id=str(entity.im_entity_sk),
                table_name=_clean(entity.im_entity_phys_nm) or "",
                logical_name=_clean(entity.im_entity_log_nm),
                description=_clean(entity.im_entity_dsc),
                table_type=_clean(entity.im_entity_tp_cd),
                database=db_name,
                database_name=db_name,
                schema_name=schema_nm,
                columns=columns,
            ))

        total_cols = sum(len(t.columns) for t in tables)
        logger.info(f"Mapped {len(tables)} TargetTable(s) with {total_cols} total columns")
        return tables

    @staticmethod
    def _map_column(attr) -> TargetColumn:
        """Map a single ORM attribute to a TargetColumn DTO."""
        col_name = _clean(attr.im_entity_colm_nm) or ""
        null_ind = _clean(attr.im_entity_colm_null_ind) or "Y"

        return TargetColumn(
            attribute_name=col_name,
            logical_attribute_name=_clean(attr.im_entity_colm_lgc_nm),
            attribute_description=_clean(attr.im_entity_colm_dsc),
            data_type=_clean(attr.im_entity_attr_data_tp_cd) or "UNKNOWN",
            length=attr.im_entity_colm_lng_no,
            precision=attr.im_entity_colm_data_tp_precision_no,
            default_value=_clean(attr.im_entity_colm_dflt_val),
            nullability=(null_ind != "N"),
            order_no=attr.im_entity_colm_ord_no,
            format=_clean(attr.im_entity_colm_fmt_val),
            is_surrogate_key=col_name.endswith("_SK"),
            is_code_column=col_name.endswith("_CD"),
        )


def _clean(value: Optional[str]) -> Optional[str]:
    """Strip whitespace from char/varchar values, return None if empty."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None
