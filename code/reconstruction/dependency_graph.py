"""Ghost Data Still Exists - Resource Dependency & Recreation Graph.

Builds directed graphs of data-flow dependencies, replication topologies,
and recreation relationships between systems and resources with cycle
detection and topological remediation ordering support.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DependencyNode:
    """A node in the dependency graph representing a system resource, connector, or backup."""
    node_id: str  # Format: "system_id:resource_id"
    system_id: str
    resource_id: str
    resource_category: str = "UNKNOWN"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DependencyEdge:
    """A directed edge indicating data flow or dependency between two resources."""
    source_id: str
    target_id: str
    relationship_type: str  # SYNC_DESTINATION, INDEX_DESTINATION, EXPORT_ORIGIN, RESTORE_TARGET, REPLICA
    is_confirmed: bool = True
    evidence_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ResourceDependencyGraph:
    """Directed graph of resources and processes with cycle detection and downstream traversal."""

    def __init__(self) -> None:
        self.nodes: Dict[str, DependencyNode] = {}
        self.adjacency: Dict[str, List[DependencyEdge]] = {}
        self.reverse_adjacency: Dict[str, List[DependencyEdge]] = {}

    def add_node(
        self,
        system_id: str,
        resource_id: str,
        category: str = "UNKNOWN",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DependencyNode:
        """Add or retrieve a node in the graph."""
        nid = f"{system_id.strip()}:{resource_id.strip()}"
        if nid not in self.nodes:
            self.nodes[nid] = DependencyNode(
                node_id=nid,
                system_id=system_id,
                resource_id=resource_id,
                resource_category=category,
                metadata=metadata or {},
            )
            self.adjacency[nid] = []
            self.reverse_adjacency[nid] = []
        return self.nodes[nid]

    def add_edge(
        self,
        source_system: str,
        source_resource: str,
        target_system: str,
        target_resource: str,
        relationship: str,
        is_confirmed: bool = True,
        evidence_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DependencyEdge:
        """Add a directed relationship between two resources."""
        src_node = self.add_node(source_system, source_resource)
        tgt_node = self.add_node(target_system, target_resource)

        edge = DependencyEdge(
            source_id=src_node.node_id,
            target_id=tgt_node.node_id,
            relationship_type=relationship,
            is_confirmed=is_confirmed,
            evidence_id=evidence_id,
            metadata=metadata or {},
        )
        self.adjacency[src_node.node_id].append(edge)
        self.reverse_adjacency[tgt_node.node_id].append(edge)
        return edge

    def get_downstream_resources(self, node_id: str) -> Set[str]:
        """Traverse downstream edges with cycle detection to identify all affected resources."""
        visited: Set[str] = set()
        queue: List[str] = [node_id]

        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            for edge in self.adjacency.get(curr, []):
                if edge.target_id not in visited:
                    queue.append(edge.target_id)

        # Exclude the starting node itself
        visited.discard(node_id)
        return visited

    def get_upstream_sources(self, node_id: str) -> Set[str]:
        """Traverse upstream edges to find source origins for this resource."""
        visited: Set[str] = set()
        queue: List[str] = [node_id]

        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            for edge in self.reverse_adjacency.get(curr, []):
                if edge.source_id not in visited:
                    queue.append(edge.source_id)

        visited.discard(node_id)
        return visited

    def detect_cycles(self) -> List[List[str]]:
        """Detect cyclic dependency paths using depth-first search."""
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(curr: str, path: List[str]) -> None:
            visited.add(curr)
            rec_stack.add(curr)
            path.append(curr)

            for edge in self.adjacency.get(curr, []):
                tgt = edge.target_id
                if tgt not in visited:
                    dfs(tgt, path)
                elif tgt in rec_stack:
                    # Cycle detected
                    idx = path.index(tgt)
                    cycles.append(list(path[idx:]))

            rec_stack.remove(curr)
            path.pop()

        for nid in list(self.nodes.keys()):
            if nid not in visited:
                dfs(nid, [])

        return cycles

    def to_edge_list(self) -> List[Dict[str, Any]]:
        """Serialize edges for inclusion in ReconstructionReport."""
        edges: List[Dict[str, Any]] = []
        for edge_list in self.adjacency.values():
            for e in edge_list:
                edges.append({
                    "source": e.source_id,
                    "target": e.target_id,
                    "relationship": e.relationship_type,
                    "confirmed": e.is_confirmed,
                    "evidence_id": e.evidence_id,
                })
        return edges
