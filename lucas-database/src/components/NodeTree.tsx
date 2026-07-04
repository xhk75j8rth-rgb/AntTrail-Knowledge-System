import { ChevronDown, ChevronRight, FilePlus2, FileText } from 'lucide-react';
import type { TreeNode } from '../api/lucasDbClient';

interface NodeTreeProps {
  nodes: TreeNode[];
  selectedNodeId: string | null;
  expandedIds: Set<string>;
  hoveredNodeId: string | null;
  onToggle: (nodeId: string) => void;
  onSelect: (nodeId: string) => void;
  onCreateChild: (nodeId: string) => void;
  onHover: (nodeId: string | null) => void;
}

export function NodeTree({
  nodes,
  selectedNodeId,
  expandedIds,
  hoveredNodeId,
  onToggle,
  onSelect,
  onCreateChild,
  onHover
}: NodeTreeProps) {
  if (nodes.length === 0) {
    return <div className="empty-tree">还没有节点</div>;
  }

  return (
    <div className="node-tree" role="tree">
      {nodes.map((node) => (
        <NodeTreeItem
          key={node.id}
          node={node}
          depth={0}
          selectedNodeId={selectedNodeId}
          expandedIds={expandedIds}
          hoveredNodeId={hoveredNodeId}
          onToggle={onToggle}
          onSelect={onSelect}
          onCreateChild={onCreateChild}
          onHover={onHover}
        />
      ))}
    </div>
  );
}

interface NodeTreeItemProps extends Omit<NodeTreeProps, 'nodes'> {
  node: TreeNode;
  depth: number;
}

function NodeTreeItem({
  node,
  depth,
  selectedNodeId,
  expandedIds,
  hoveredNodeId,
  onToggle,
  onSelect,
  onCreateChild,
  onHover
}: NodeTreeItemProps) {
  const expanded = expandedIds.has(node.id);
  const hasChildren = node.children.length > 0;
  const selected = selectedNodeId === node.id;
  const hovered = hoveredNodeId === node.id;
  const showAddButton = hovered || selected;

  return (
    <div role="treeitem" aria-expanded={hasChildren ? expanded : undefined}>
      <div
        className={`tree-row ${selected ? 'selected' : ''}`}
        style={{ paddingLeft: `${depth * 18 + 8}px` }}
        onClick={() => onSelect(node.id)}
        onMouseEnter={() => onHover(node.id)}
        onMouseLeave={() => onHover(null)}
      >
        <button
          className="icon-button tree-toggle"
          type="button"
          title={hasChildren ? (expanded ? '折叠' : '展开') : '无子节点'}
          onClick={(event) => {
            event.stopPropagation();
            if (hasChildren) {
              onToggle(node.id);
            }
          }}
        >
          {hasChildren ? (
            expanded ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )
          ) : (
            <span className="toggle-placeholder" />
          )}
        </button>
        <FileText size={15} className="tree-file-icon" />
        <span className="tree-title">{node.title}</span>
        <button
          className={`icon-button tree-add ${showAddButton ? 'visible' : ''}`}
          type="button"
          title="添加子文件"
          aria-label="添加子文件"
          onClick={(event) => {
            event.stopPropagation();
            onCreateChild(node.id);
          }}
        >
          <FilePlus2 size={14} />
        </button>
      </div>
      {hasChildren && expanded ? (
        <div role="group">
          {node.children.map((child) => (
            <NodeTreeItem
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedNodeId={selectedNodeId}
              expandedIds={expandedIds}
              hoveredNodeId={hoveredNodeId}
              onToggle={onToggle}
              onSelect={onSelect}
              onCreateChild={onCreateChild}
              onHover={onHover}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
