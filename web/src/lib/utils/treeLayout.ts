import { hierarchy, tree as d3Tree } from 'd3-hierarchy';
import type { SessionSummary } from '../types';

// --- Shared tree-building utils (also used by SidebarSessionTree) ---

export interface TreeNode {
  session: SessionSummary;
  children: TreeNode[];
}

export function buildTree(sessions: SessionSummary[]): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];
  for (const s of sessions) {
    byId.set(s.id, { session: s, children: [] });
  }
  for (const s of sessions) {
    const node = byId.get(s.id)!;
    if (s.parent_session_id && byId.has(s.parent_session_id)) {
      byId.get(s.parent_session_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

export function partitionChildren(children: TreeNode[]): { active: TreeNode[]; terminal: TreeNode[] } {
  const active: TreeNode[] = [];
  const terminal: TreeNode[] = [];
  for (const child of children) {
    if (child.session.outcome != null) {
      terminal.push(child);
    } else {
      active.push(child);
    }
  }
  return { active, terminal };
}

export function terminalSummaryText(nodes: TreeNode[]): string {
  const counts: Record<string, number> = {};
  for (const n of nodes) {
    counts[n.session.status] = (counts[n.session.status] ?? 0) + 1;
  }
  const order = ['completed', 'failed', 'canceled'];
  return order
    .filter(s => counts[s])
    .map(s => `${counts[s]} ${s}`)
    .join(', ');
}

export function containsSession(nodes: TreeNode[], id: string | null | undefined): boolean {
  if (!id) return false;
  return nodes.some(n => n.session.id === id || containsSession(n.children, id));
}

// --- Org-chart-only types and logic ---

export interface CompactedNode {
  type: 'session' | 'group';
  session?: SessionSummary;
  groupedSessions?: SessionSummary[];
  children: CompactedNode[];
  x: number;
  y: number;
  width: number;
  height: number;
}

const NODE_WIDTH = 160;
const NODE_HEIGHT = 80;
const GROUP_HEIGHT = 50;
const H_SPACING = 180;
const V_SPACING = 120;

const failedStatuses = new Set(['failed', 'crashed']);

function treeNodeToCompacted(node: TreeNode): CompactedNode {
  const children: CompactedNode[] = [];
  const { active, terminal } = partitionChildren(node.children);

  // Recurse into active children
  for (const child of active) {
    children.push(treeNodeToCompacted(child));
  }

  // Group terminal children: failed/crashed are never grouped
  const groupable: SessionSummary[] = [];
  for (const child of terminal) {
    if (failedStatuses.has(child.session.status)) {
      children.push({
        type: 'session',
        session: child.session,
        children: [],
        x: 0, y: 0,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      });
    } else {
      groupable.push(child.session);
    }
  }

  if (groupable.length >= 3) {
    children.push({
      type: 'group',
      groupedSessions: groupable,
      children: [],
      x: 0, y: 0,
      width: NODE_WIDTH,
      height: GROUP_HEIGHT,
    });
  } else {
    for (const s of groupable) {
      children.push({
        type: 'session',
        session: s,
        children: [],
        x: 0, y: 0,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      });
    }
  }

  return {
    type: 'session',
    session: node.session,
    children,
    x: 0, y: 0,
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  };
}

export function applyCompaction(roots: TreeNode[]): CompactedNode[] {
  return roots.map(treeNodeToCompacted);
}

export interface LayoutEdge {
  from: CompactedNode;
  to: CompactedNode;
}

export interface LayoutResult {
  nodes: CompactedNode[];
  edges: LayoutEdge[];
  bounds: { width: number; height: number };
}

export function computeLayout(roots: CompactedNode[]): LayoutResult {
  if (roots.length === 0) {
    return { nodes: [], edges: [], bounds: { width: 0, height: 0 } };
  }

  // Wrap in a virtual root if multiple roots
  const virtualRoot: CompactedNode = roots.length === 1
    ? roots[0]
    : {
        type: 'session',
        children: roots,
        x: 0, y: 0,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      };

  const hasVirtualRoot = roots.length > 1;

  // Create fresh d3 hierarchy + tree each call (d3 mutates in place)
  const root = hierarchy(virtualRoot, d => d.children);
  const treeLayout = d3Tree<CompactedNode>().nodeSize([H_SPACING, V_SPACING]);
  treeLayout(root);

  // Collect all real nodes and edges
  const allNodes: CompactedNode[] = [];
  const edges: LayoutEdge[] = [];

  // d3 assigns x,y on hierarchy nodes; transfer to CompactedNode
  // d3.tree uses x for horizontal, y for depth (top-down)
  root.each(hNode => {
    if (hasVirtualRoot && hNode === root) return; // skip virtual root
    const cn = hNode.data;
    cn.x = (hNode.x ?? 0) - cn.width / 2;
    cn.y = hNode.y ?? 0;
    allNodes.push(cn);
  });

  // Build edges
  root.each(hNode => {
    if (hasVirtualRoot && hNode === root) return;
    if (!hNode.children) return;
    for (const child of hNode.children) {
      if (hasVirtualRoot && hNode === root) continue;
      edges.push({ from: hNode.data, to: child.data });
    }
  });

  // For virtual root, add edges from each actual root's parent perspective
  if (hasVirtualRoot && root.children) {
    // No edges drawn to/from virtual root — it's hidden
  }

  // Compute bounds: find min/max x,y across all nodes
  let minX = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const node of allNodes) {
    if (node.x < minX) minX = node.x;
    if (node.x + node.width > maxX) maxX = node.x + node.width;
    if (node.y + node.height > maxY) maxY = node.y + node.height;
  }

  // Shift all nodes so minX = 0
  const offsetX = -minX;
  for (const node of allNodes) {
    node.x += offsetX;
  }

  const bounds = {
    width: maxX - minX,
    height: maxY,
  };

  return { nodes: allNodes, edges, bounds };
}

export function edgePath(from: CompactedNode, to: CompactedNode): string {
  const startX = from.x + from.width / 2;
  const startY = from.y + from.height;
  const endX = to.x + to.width / 2;
  const endY = to.y;
  const midY = startY + (endY - startY) / 2;
  return `M ${startX} ${startY} V ${midY} H ${endX} V ${endY}`;
}
