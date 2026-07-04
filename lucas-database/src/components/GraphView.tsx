import { RefreshCcw } from 'lucide-react';
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import { graphClient, type GraphData, type GraphNode, type GraphScope } from '../api/graphClient';
import { lucasDbClient } from '../api/lucasDbClient';
import { GraphPanel } from './GraphPanel';

interface GraphViewProps {
  selectedNodeId: string | null;
}

const Graph3DPanel = lazy(() =>
  import('./Graph3DPanel').then((module) => ({ default: module.Graph3DPanel }))
);

const relationWeight: Record<string, number> = {
  CONTAINS: 1.35,
  MOUNTED_ON: 1.45,
  HAS_CONCEPT: 1.15,
  TAGGED_AS: 0.75,
  HAS_RISK: 0.85,
  SUGGESTS_ACTION: 0.85,
  HAS_METHOD: 0.95,
  DERIVED_FROM_SOURCE: 0.65,
  GENERATED_BY_MODEL: 0.55
};

export function GraphView({ selectedNodeId }: GraphViewProps) {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<GraphScope>('local');
  const [depth, setDepth] = useState(1);
  const [renderMode, setRenderMode] = useState<'2d' | '3d'>('2d');
  const [selectedGraphNode, setSelectedGraphNode] = useState<GraphNode | null>(null);
  const [childTitle, setChildTitle] = useState('');
  const [relationTargetId, setRelationTargetId] = useState('');
  const [relationType, setRelationType] = useState('RELATED_TO');
  const [relationLabel, setRelationLabel] = useState('相关');
  const [savingAction, setSavingAction] = useState(false);

  const loadGraph = useCallback(async () => {
    if (!selectedNodeId && scope === 'local') {
      setGraph(null);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      setGraph(await graphClient.getGraph({ nodeId: selectedNodeId, depth, scope }));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '图谱加载失败');
    } finally {
      setLoading(false);
    }
  }, [depth, scope, selectedNodeId]);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);

  useEffect(() => {
    if (!graph || !selectedGraphNode) {
      return;
    }
    if (!graph.nodes.some((node) => node.id === selectedGraphNode.id)) {
      setSelectedGraphNode(null);
    }
  }, [graph, selectedGraphNode]);

  useEffect(() => {
    setChildTitle('');
    setRelationTargetId('');
  }, [selectedGraphNode?.id]);

  const writableGraphNodes = graph?.nodes.filter((node) => node.entity_type === 'node' || node.entity_type === 'card') || [];
  const graphNodeMetrics = useMemo(() => {
    const metrics = new Map<string, { degree: number; weight: number }>();
    graph?.edges.forEach((edge) => {
      const weight = relationWeight[edge.relation_type] || 1;
      const from = metrics.get(edge.from) || { degree: 0, weight: 1 };
      const to = metrics.get(edge.to) || { degree: 0, weight: 1 };
      from.degree += weight;
      to.degree += weight;
      from.weight = Math.max(1, Math.log2(from.degree + 2));
      to.weight = Math.max(1, Math.log2(to.degree + 2));
      metrics.set(edge.from, from);
      metrics.set(edge.to, to);
    });
    graph?.nodes.forEach((node) => {
      if (!metrics.has(node.id)) {
        metrics.set(node.id, { degree: 0, weight: 1 });
      }
    });
    return metrics;
  }, [graph]);
  const selectedGraphMetrics = selectedGraphNode ? graphNodeMetrics.get(selectedGraphNode.id) : null;
  const selectedIsNode = selectedGraphNode?.entity_type === 'node' && selectedGraphNode.source_id;
  const selectedCanRelate =
    (selectedGraphNode?.entity_type === 'node' || selectedGraphNode?.entity_type === 'card') &&
    selectedGraphNode.source_id;

  const createChildFromGraph = async () => {
    if (!selectedGraphNode?.source_id || selectedGraphNode.entity_type !== 'node') {
      return;
    }
    const title = childTitle.trim() || '未命名子节点';
    try {
      setSavingAction(true);
      await lucasDbClient.createChildNode(selectedGraphNode.source_id, {
        title,
        content: `# ${title}\n`
      });
      setChildTitle('');
      await loadGraph();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : '新增节点失败');
    } finally {
      setSavingAction(false);
    }
  };

  const createManualRelation = async () => {
    if (!selectedGraphNode?.source_id || !relationTargetId) {
      return;
    }
    const target = graph?.nodes.find((node) => node.id === relationTargetId);
    if (!target?.source_id) {
      return;
    }

    try {
      setSavingAction(true);
      await graphClient.createRelation({
        from_node_id: selectedGraphNode.entity_type === 'node' ? selectedGraphNode.source_id : undefined,
        from_card_id: selectedGraphNode.entity_type === 'card' ? selectedGraphNode.source_id : undefined,
        to_node_id: target.entity_type === 'node' ? target.source_id : undefined,
        to_card_id: target.entity_type === 'card' ? target.source_id : undefined,
        relation_type: relationType.trim() || 'RELATED_TO',
        label: relationLabel.trim() || '相关',
        source: 'manual',
        confidence: 1
      });
      await loadGraph();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : '创建关系失败');
    } finally {
      setSavingAction(false);
    }
  };

  const graphActionPanel = selectedGraphNode ? (
    <aside className="graph-action-panel" aria-label="图谱操作面板">
      <div className="graph-action-header">
        <span>{selectedGraphNode.entity_type}</span>
        <strong>{selectedGraphNode.label}</strong>
        <button type="button" onClick={() => setSelectedGraphNode(null)}>
          关闭
        </button>
      </div>
      {selectedGraphMetrics ? (
        <div className="graph-action-metrics">
          <span>
            权重 <strong>{selectedGraphMetrics.weight.toFixed(1)}</strong>
          </span>
          <span>
            连接 <strong>{selectedGraphMetrics.degree.toFixed(1)}</strong>
          </span>
        </div>
      ) : null}

      {selectedIsNode ? (
        <div className="graph-action-section">
          <label>
            新增子节点
            <input
              value={childTitle}
              onChange={(event) => setChildTitle(event.target.value)}
              placeholder="子节点标题"
            />
          </label>
          <button type="button" onClick={() => void createChildFromGraph()} disabled={savingAction}>
            创建节点
          </button>
        </div>
      ) : null}

      {selectedCanRelate ? (
        <div className="graph-action-section">
          <label>
            目标节点
            <select value={relationTargetId} onChange={(event) => setRelationTargetId(event.target.value)}>
              <option value="">选择 Node / Card</option>
              {writableGraphNodes
                .filter((node) => node.id !== selectedGraphNode.id)
                .map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.label} ({node.entity_type})
                  </option>
                ))}
            </select>
          </label>
          <label>
            关系类型
            <input value={relationType} onChange={(event) => setRelationType(event.target.value)} />
          </label>
          <label>
            显示名称
            <input value={relationLabel} onChange={(event) => setRelationLabel(event.target.value)} />
          </label>
          <button
            type="button"
            onClick={() => void createManualRelation()}
            disabled={savingAction || !relationTargetId}
          >
            创建关系
          </button>
        </div>
      ) : (
        <p className="graph-action-note">虚拟实体暂时只用于浏览；请选择 Node 或 Card 创建写入关系。</p>
      )}
    </aside>
  ) : null;

  if (!selectedNodeId && scope === 'local') {
    return (
      <section className="graph-view" aria-label="图谱视图">
        <div className="graph-view-toolbar">
          <div>
            <h1>图谱视图</h1>
            <span>当前没有选中节点</span>
          </div>
          <button className="text-button" type="button" onClick={() => setScope('global')}>
            全库
          </button>
        </div>
        <div className="editor-empty">
          <h2>选择或创建一个节点</h2>
          <p>当前没有选中节点。</p>
        </div>
      </section>
    );
  }

  return (
    <section className="graph-view" aria-label="图谱视图">
      <div className="graph-view-toolbar">
        <div>
          <h1>图谱视图</h1>
          <span>{loading ? '加载中...' : scope === 'global' ? '全库图谱' : `${depth} 层关系`}</span>
        </div>
        <div className="graph-controls">
          <div className="graph-control-group" aria-label="图谱范围">
            <button
              className={scope === 'local' ? 'active' : ''}
              type="button"
              onClick={() => setScope('local')}
              disabled={!selectedNodeId}
            >
              局部
            </button>
            <button
              className={scope === 'global' ? 'active' : ''}
              type="button"
              onClick={() => setScope('global')}
            >
              全库
            </button>
          </div>
          <div className="graph-control-group" aria-label="图谱深度">
            {[1, 2, 3].map((value) => (
              <button
                className={depth === value ? 'active' : ''}
                type="button"
                onClick={() => setDepth(value)}
                key={value}
              >
                {value}
              </button>
            ))}
          </div>
          <div className="graph-control-group" aria-label="图谱显示模式">
            <button
              className={renderMode === '2d' ? 'active' : ''}
              type="button"
              onClick={() => setRenderMode('2d')}
            >
              2D
            </button>
            <button
              className={renderMode === '3d' ? 'active' : ''}
              type="button"
              onClick={() => setRenderMode('3d')}
            >
              3D
            </button>
          </div>
          <button className="icon-button" type="button" title="刷新图谱" onClick={() => void loadGraph()}>
            <RefreshCcw size={17} />
          </button>
        </div>
      </div>
      {error ? (
        <button className="error-banner graph-error" type="button" onClick={() => setError(null)}>
          {error}
        </button>
      ) : null}
      {graph ? (
        <>
          {renderMode === '3d' ? (
            <Suspense fallback={<p className="graph-loading">正在加载 3D 图谱...</p>}>
              <Graph3DPanel
                graph={graph}
                selectedNodeId={selectedNodeId}
                activeGraphNodeId={selectedGraphNode?.id}
                onNodeSelect={setSelectedGraphNode}
              />
            </Suspense>
          ) : (
            <GraphPanel
              graph={graph}
              selectedNodeId={selectedNodeId}
              activeGraphNodeId={selectedGraphNode?.id}
              onNodeSelect={setSelectedGraphNode}
            />
          )}
          {graphActionPanel}
        </>
      ) : (
        <p className="graph-loading">正在读取图谱...</p>
      )}
    </section>
  );
}
