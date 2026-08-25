from app.repositories.base import BaseRepository


def create_core(project_id: str, entity_type: str) -> BaseRepository:
    from app.repositories.networkx_core_repository import NetworkxCoreRepository
    return NetworkxCoreRepository(project_id, entity_type)


def create_shell(project_id: str) -> BaseRepository:
    from app.repositories.sqlite_vss_shell_repository import SqliteVssShellRepository
    return SqliteVssShellRepository(project_id)


def create_dag(project_id: str = "", trigger_type: str = ""):
    from app.dag.persistent_engine import PersistentDAG
    return PersistentDAG(project_id, trigger_type)


def create_search_service():
    from app.services.sqlite_fts_service import SqliteFtsService
    return SqliteFtsService()


def create_state_manager():
    from app.services.sqlite_state_manager import SqliteStateManager
    return SqliteStateManager()
