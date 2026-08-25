"""DAG definition for editor chapter generation.

This is the explicit dependency map for the chapter-generation workflow. The
current runner can consume it incrementally while legacy step names remain
compatible with the existing frontend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BlockingPolicy = Literal["hard", "soft", "advisory"]


@dataclass(frozen=True)
class EditorDagNode:
    node_id: str
    display_name: str
    node_type: str
    dependencies: list[str] = field(default_factory=list)
    scene_index: int | None = None
    workflow_step: str | None = None
    blocking_policy: BlockingPolicy = "hard"
    parallel_group: str = ""
    read_scope: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
    can_mutate_text: bool = False

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "node_type": self.node_type,
            "dependencies": list(self.dependencies),
            "scene_index": self.scene_index,
            "workflow_step": self.workflow_step or self.node_id,
            "blocking_policy": self.blocking_policy,
            "parallel_group": self.parallel_group,
            "read_scope": list(self.read_scope),
            "write_scope": list(self.write_scope),
            "can_mutate_text": self.can_mutate_text,
        }


@dataclass(frozen=True)
class EditorGenerationDAG:
    dag_version: str
    mode: str
    scene_count: int
    nodes: list[EditorDagNode]

    def to_dict(self) -> dict:
        return {
            "dag_version": self.dag_version,
            "mode": self.mode,
            "scene_count": self.scene_count,
            "nodes": [node.to_dict() for node in self.nodes],
            "layers": self.layers(),
        }

    def step_names(self) -> list[str]:
        names: list[str] = []
        for node in self.nodes:
            step = node.workflow_step or node.node_id
            if step not in names:
                names.append(step)
        return names

    def layers(self) -> list[list[str]]:
        unresolved = {node.node_id: set(node.dependencies) for node in self.nodes}
        resolved: set[str] = set()
        layers: list[list[str]] = []
        while unresolved:
            ready = sorted(node_id for node_id, deps in unresolved.items() if deps <= resolved)
            if not ready:
                cycle = {node_id: sorted(deps) for node_id, deps in unresolved.items()}
                raise ValueError(f"EditorGenerationDAG has cyclic or missing dependencies: {cycle}")
            layers.append(ready)
            resolved.update(ready)
            for node_id in ready:
                unresolved.pop(node_id, None)
        return layers


class EditorGenerationDAGBuilder:
    DAG_VERSION = "editor_generation_v1"

    def build(self, *, scene_count: int, mode: str = "safe") -> EditorGenerationDAG:
        if scene_count <= 0:
            raise ValueError("scene_count must be positive")
        if mode == "safe":
            return self._build_safe(scene_count=scene_count)
        if mode == "parallel_review":
            return self._build_parallel_review(scene_count=scene_count)
        raise ValueError(f"Unknown DAG mode: {mode!r}")

    # ------------------------------------------------------------------
    # safe mode – per-scene serial chain
    # ------------------------------------------------------------------
    def _build_safe(self, *, scene_count: int) -> EditorGenerationDAG:
        nodes: list[EditorDagNode] = [
            EditorDagNode("editor_planning", "主编规划", "chapter_planning", workflow_step="editor_planning"),
            EditorDagNode("context_compile", "上下文编译", "context_compile", ["editor_planning"], workflow_step="context_compile"),
            EditorDagNode("chapter_writer", "整章 Writer", "chapter_writer", ["context_compile"], workflow_step="chapter_writer", blocking_policy="soft"),
            EditorDagNode("scene_alignment", "场景对齐", "scene_alignment", ["chapter_writer"], workflow_step="scene_alignment", blocking_policy="soft"),
        ]

        previous_commit = "scene_alignment"
        for index in range(scene_count):
            suffix = index + 1
            prepare = f"scene_prepare_{suffix}"
            generate = f"scene_generate_{suffix}"
            quality = f"scene_quality_fanout_{suffix}"
            extract = f"scene_post_extract_fanout_{suffix}"
            accept = f"scene_accept_{suffix}"
            commit = f"scene_state_commit_{suffix}"

            nodes.extend([
                EditorDagNode(
                    prepare,
                    f"场景 {suffix} 准备",
                    "scene_prepare",
                    [previous_commit],
                    scene_index=index,
                    workflow_step=f"scene_preparation_{suffix}",
                ),
                EditorDagNode(
                    generate,
                    f"场景 {suffix} 正文生成",
                    "scene_generate",
                    [prepare],
                    scene_index=index,
                    workflow_step=f"core_generation_{suffix}",
                ),
                EditorDagNode(
                    quality,
                    f"场景 {suffix} 并行审校",
                    "scene_quality_fanout",
                    [generate],
                    scene_index=index,
                    workflow_step=f"consistency_check_{suffix}",
                    parallel_group=f"scene_{suffix}_fanout",
                ),
                EditorDagNode(
                    extract,
                    f"场景 {suffix} 后处理抽取",
                    "scene_post_extract_fanout",
                    [generate],
                    scene_index=index,
                    workflow_step=f"detail_harvest_{suffix}",
                    parallel_group=f"scene_{suffix}_fanout",
                ),
                EditorDagNode(
                    accept,
                    f"场景 {suffix} 接受",
                    "scene_accept",
                    [quality, extract],
                    scene_index=index,
                    workflow_step=f"core_generation_{suffix}",
                ),
                EditorDagNode(
                    commit,
                    f"场景 {suffix} 状态提交",
                    "scene_state_commit",
                    [accept, previous_commit],
                    scene_index=index,
                    workflow_step=f"state_update_{suffix}",
                ),
            ])
            previous_commit = commit

        nodes.extend([
            EditorDagNode("chapter_fact_finalize", "章节事实汇总", "chapter_fact_finalize", [previous_commit], workflow_step="fact_extraction"),
            EditorDagNode("style_polish", "文风润色", "style_polish", ["chapter_fact_finalize"], workflow_step="style_polish", blocking_policy="soft"),
            EditorDagNode("write_chapter", "章节写入", "write_chapter", ["style_polish"], workflow_step="write_chapter"),
        ])
        dag = EditorGenerationDAG(self.DAG_VERSION, "safe", scene_count, nodes)
        dag.layers()  # validate acyclic
        return dag

    # ------------------------------------------------------------------
    # parallel_review mode – parallel scene review + FBI repair loop
    # ------------------------------------------------------------------
    def _build_parallel_review(self, *, scene_count: int) -> EditorGenerationDAG:
        nodes: list[EditorDagNode] = []

        # --- parallel scene review (all scenes reviewed simultaneously) ---
        review_ids: list[str] = []
        for index in range(scene_count):
            suffix = index + 1
            review_id = f"scene_review_{suffix}"
            review_ids.append(review_id)
            nodes.append(
                EditorDagNode(
                    review_id,
                    f"场景 {suffix} 并行审阅",
                    "parallel_scene_review",
                    [],
                    scene_index=index,
                    workflow_step=f"parallel_review_{suffix}",
                    parallel_group="parallel_review",
                    read_scope=[f"scene:{index}", "scene_contracts"],
                    write_scope=[f"issue_output:{index}"],
                    can_mutate_text=False,
                )
            )

        nodes.append(
            EditorDagNode(
                "chapter_review",
                "Chapter Review",
                "chapter_review",
                [],
                workflow_step="chapter_review",
                parallel_group="review_collection_parallel",
                read_scope=["chapter_text", "scene_contracts"],
                write_scope=["issue_output:chapter"],
                can_mutate_text=False,
            )
        )

        # --- FBI chapter-level case intake ---
        nodes.append(
            EditorDagNode(
                "fbi_chapter_case_intake",
                "FBI 章节案件受理",
                "fbi_chapter_case_intake",
                [*review_ids, "chapter_review"],
                workflow_step="fbi_case_intake",
                read_scope=["issue_output"],
                write_scope=["review_case_file"],
                can_mutate_text=False,
            )
        )

        # --- parallel scene repair (all scenes; runtime skips those without orders) ---
        repair_ids: list[str] = []
        for index in range(scene_count):
            suffix = index + 1
            repair_id = f"scene_repair_{suffix}"
            repair_ids.append(repair_id)
            nodes.append(
                EditorDagNode(
                    repair_id,
                    f"场景 {suffix} 修复",
                    "parallel_scene_repair",
                    ["fbi_chapter_case_intake"],
                    scene_index=index,
                    workflow_step=f"parallel_repair_{suffix}",
                    parallel_group="parallel_repair",
                    read_scope=["repair_plan", "review_case_file", f"scene:{index}"],
                    write_scope=[f"scene:{index}"],
                    can_mutate_text=True,
                )
            )

        # --- parallel scene recheck (only repaired scenes; depends on repair) ---
        recheck_ids: list[str] = []
        for index in range(scene_count):
            suffix = index + 1
            recheck_id = f"scene_recheck_{suffix}"
            recheck_ids.append(recheck_id)
            nodes.append(
                EditorDagNode(
                    recheck_id,
                    f"场景 {suffix} 复检",
                    "parallel_scene_recheck",
                    [repair_ids[index]],
                    scene_index=index,
                    workflow_step=f"parallel_recheck_{suffix}",
                    parallel_group="parallel_recheck",
                    read_scope=[f"scene:{index}", "repair_plan"],
                    write_scope=[f"issue_delta_output:{index}"],
                    can_mutate_text=False,
                )
            )

        # --- recheck delta merge / optional FBI repair cycle ---
        nodes.append(
            EditorDagNode(
                "review_case_delta_merge",
                "复检问题增量归档",
                "review_case_delta_merge",
                recheck_ids,
                workflow_step="review_case_delta_merge",
                read_scope=["issue_delta_output", "review_case_file", "repair_plan"],
                write_scope=["review_case_delta", "repair_plan"],
                can_mutate_text=True,
            )
        )

        # --- ordered state commit (strictly sequential) ---
        commit_ids: list[str] = []
        for index in range(scene_count):
            suffix = index + 1
            commit_id = f"state_commit_{suffix}"
            commit_ids.append(commit_id)
            # commit_i depends on the serialized recheck delta merge and commit_{i-1}
            deps = ["review_case_delta_merge"] if index == 0 else []
            if index > 0:
                deps.append(commit_ids[index - 1])
            nodes.append(
                EditorDagNode(
                    commit_id,
                    f"场景 {suffix} 有序状态提交",
                    "ordered_state_commit",
                    deps,
                    scene_index=index,
                    workflow_step=f"ordered_commit_{suffix}",
                )
            )

        # --- final acceptance delta chain (scheduled by second DAG run) ---
        # R4-9 修复：final_acceptance_delta_intake 应依赖 ordered_state_commit，
        # 而非 dependencies=[]（否则语义上与 parallel_scene_review 同层）
        _final_delta_deps = commit_ids[-1:] if commit_ids else []
        nodes.extend([
            EditorDagNode(
                "final_acceptance_delta_intake",
                "Final Delta Intake",
                "final_acceptance_delta_intake",
                _final_delta_deps,
                workflow_step="final_acceptance_delta_intake",
                read_scope=["final_candidate", "case_file_delta", "review_case_file"],
                write_scope=["final_delta_case"],
                can_mutate_text=False,
            ),
            EditorDagNode(
                "final_acceptance_delta_blueprint",
                "Final Delta Blueprint",
                "final_acceptance_delta_blueprint",
                ["final_acceptance_delta_intake"],
                workflow_step="final_acceptance_delta_blueprint",
                read_scope=["final_delta_case"],
                write_scope=["final_delta_repair_plan"],
                can_mutate_text=False,
            ),
            EditorDagNode(
                "final_acceptance_delta_execute",
                "Final Delta Execute",
                "final_acceptance_delta_execute",
                ["final_acceptance_delta_blueprint"],
                workflow_step="final_acceptance_delta_execute",
                read_scope=["final_delta_repair_plan", "scene_texts"],
                write_scope=["scene_texts"],
                can_mutate_text=True,
            ),
            EditorDagNode(
                "final_acceptance_delta_recheck",
                "Final Delta Recheck",
                "final_acceptance_delta_recheck",
                ["final_acceptance_delta_execute"],
                workflow_step="final_acceptance_delta_recheck",
                read_scope=["scene_texts", "final_delta_repair_plan"],
                write_scope=["commit_decision"],
                can_mutate_text=False,
            ),
        ])

        dag = EditorGenerationDAG(self.DAG_VERSION, "parallel_review", scene_count, nodes)
        dag.layers()  # validate acyclic
        return dag
