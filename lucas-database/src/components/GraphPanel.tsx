import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum
} from 'd3-force';
import { Eye, EyeOff, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { GraphData, GraphEdge, GraphNode } from '../api/graphClient';

interface GraphPanelProps {
  graph: GraphData;
  selectedNodeId?: string | null;
  activeGraphNodeId?: string | null;
  onNodeSelect?: (node: GraphNode) => void;
}

interface ForceNode extends SimulationNodeDatum {
  id: string;
  entity_type: string;
  label: string;
  source_id?: string;
  metadata?: Record<string, unknown>;
  radius: number;
  color: string;
  weight: number;
  degree: number;
}

interface ForceLink extends SimulationLinkDatum<ForceNode> {
  id: string;
  source: string | ForceNode;
  target: string | ForceNode;
  relation_type: string;
  label: string | null;
  sourceKind: string;
  confidence: number;
}

interface ViewTransform {
  scale: number;
  x: number;
  y: number;
}

type PointerMode = 'pan' | 'drag-node' | null;

const entityLabel: Record<string, string> = {
  node: 'Node',
  card: 'Card',
  concept: 'Concept',
  tag: 'Tag',
  risk: 'Risk',
  action: 'Action',
  method: 'Method',
  source: 'Source',
  model: 'Model'
};

const entityStyle: Record<string, { color: string; radius: number }> = {
  node: { color: '#62a8ff', radius: 8 },
  card: { color: '#f7b955', radius: 9 },
  concept: { color: '#a78bfa', radius: 6 },
  tag: { color: '#6fdc8c', radius: 5 },
  risk: { color: '#ff6b6b', radius: 6 },
  action: { color: '#44d7c9', radius: 6 },
  method: { color: '#ff9f43', radius: 6 },
  source: { color: '#c7ced8', radius: 5 },
  model: { color: '#f472b6', radius: 5 }
};

const fallbackStyle = { color: '#8ea0b8', radius: 5 };
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

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

const getLinkNode = (value: string | ForceNode | number | undefined): ForceNode | null =>
  typeof value === 'object' && value !== null ? value : null;

const createGraphModel = (graph: GraphData) => {
  const degree = new Map<string, number>();
  graph.edges.forEach((edge) => {
    const weight = relationWeight[edge.relation_type] || 1;
    degree.set(edge.from, (degree.get(edge.from) || 0) + weight);
    degree.set(edge.to, (degree.get(edge.to) || 0) + weight);
  });

  const nodes: ForceNode[] = graph.nodes.map((node, index) => {
    const style = entityStyle[node.entity_type] || fallbackStyle;
    const angle = index * 2.399963229728653;
    const nodeDegree = degree.get(node.id) || 0;
    const weight = Math.max(1, Math.log2(nodeDegree + 2));
    const spread = nodeDegree === 0 ? 220 + (index % 9) * 10 : 36 + Math.sqrt(index + 1) * 13;
    return {
      ...node,
      x: Math.cos(angle) * spread,
      y: Math.sin(angle) * spread,
      radius: style.radius + Math.min(8, weight * 1.7),
      color: style.color,
      weight,
      degree: nodeDegree
    };
  });
  const nodeIds = new Set(nodes.map((node) => node.id));
  const links: ForceLink[] = graph.edges
    .filter((edge) => nodeIds.has(edge.from) && nodeIds.has(edge.to))
    .map((edge) => ({
      id: edge.id,
      source: edge.from,
      target: edge.to,
      relation_type: edge.relation_type,
      label: edge.label,
      sourceKind: edge.source,
      confidence: edge.confidence
    }));

  return { nodes, links };
};

const drawRoundedLabel = (
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number
) => {
  const label = text.length > 34 ? `${text.slice(0, 33)}...` : text;
  ctx.font = '12px "Segoe UI", sans-serif';
  const width = Math.min(ctx.measureText(label).width, maxWidth);
  const height = 20;
  const padding = 7;
  const left = x - width / 2 - padding;
  const top = y - height / 2;
  const radius = 6;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(left + radius, top);
  ctx.lineTo(left + width + padding * 2 - radius, top);
  ctx.quadraticCurveTo(left + width + padding * 2, top, left + width + padding * 2, top + radius);
  ctx.lineTo(left + width + padding * 2, top + height - radius);
  ctx.quadraticCurveTo(
    left + width + padding * 2,
    top + height,
    left + width + padding * 2 - radius,
    top + height
  );
  ctx.lineTo(left + radius, top + height);
  ctx.quadraticCurveTo(left, top + height, left, top + height - radius);
  ctx.lineTo(left, top + radius);
  ctx.quadraticCurveTo(left, top, left + radius, top);
  ctx.closePath();
  ctx.fillStyle = 'rgba(12, 18, 28, 0.72)';
  ctx.fill();
  ctx.fillStyle = '#e8edf6';
  ctx.fillText(label, x - width / 2, y + 4, maxWidth);
  ctx.restore();
};

export function GraphPanel({ graph, selectedNodeId, activeGraphNodeId, onNodeSelect }: GraphPanelProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const simulationRef = useRef<Simulation<ForceNode, ForceLink> | null>(null);
  const nodesRef = useRef<ForceNode[]>([]);
  const linksRef = useRef<ForceLink[]>([]);
  const transformRef = useRef<ViewTransform>({ scale: 1, x: 0, y: 0 });
  const hoveredNodeRef = useRef<ForceNode | null>(null);
  const pointerRef = useRef<{
    mode: PointerMode;
    pointerId: number | null;
    startX: number;
    startY: number;
    startTransform: ViewTransform;
    node: ForceNode | null;
    moved: boolean;
  }>({
    mode: null,
    pointerId: null,
    startX: 0,
    startY: 0,
    startTransform: { scale: 1, x: 0, y: 0 },
    node: null,
    moved: false
  });
  const hasUserTransformRef = useRef(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hoveredNode, setHoveredNode] = useState<ForceNode | null>(null);
  const [hoveredPoint, setHoveredPoint] = useState({ x: 0, y: 0 });
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [showIsolated, setShowIsolated] = useState(false);
  const highlightedGraphId = selectedNodeId ? `node:${selectedNodeId}` : null;

  const model = useMemo(() => createGraphModel(graph), [graph]);
  const hasConnectedNodes = model.nodes.some((node) => node.degree > 0);
  const visibleTypes = useMemo(
    () => new Set(graph.nodes.map((node) => node.entity_type).filter((type) => !hiddenTypes.has(type))),
    [graph.nodes, hiddenTypes]
  );

  useEffect(() => {
    setHiddenTypes(new Set());
    setShowIsolated(graph.nodes.length <= 36 || graph.edges.length === 0);
  }, [graph]);

  const isNodeVisible = useCallback(
    (node: ForceNode) =>
      visibleTypes.has(node.entity_type) &&
      (showIsolated ||
        node.degree > 0 ||
        !hasConnectedNodes ||
        node.id === highlightedGraphId ||
        node.id === activeGraphNodeId),
    [activeGraphNodeId, hasConnectedNodes, highlightedGraphId, showIsolated, visibleTypes]
  );

  const screenToWorld = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    const rect = canvas?.getBoundingClientRect();
    const transform = transformRef.current;
    if (!rect) {
      return { x: 0, y: 0 };
    }

    return {
      x: (clientX - rect.left - transform.x) / transform.scale,
      y: (clientY - rect.top - transform.y) / transform.scale
    };
  }, []);

  const findNodeAt = useCallback((clientX: number, clientY: number) => {
    const point = screenToWorld(clientX, clientY);
    const scale = transformRef.current.scale;
    for (let index = nodesRef.current.length - 1; index >= 0; index -= 1) {
      const node = nodesRef.current[index];
      if (!isNodeVisible(node)) {
        continue;
      }
      const dx = point.x - (node.x || 0);
      const dy = point.y - (node.y || 0);
      const radius = node.radius + 7 / scale;
      if (dx * dx + dy * dy <= radius * radius) {
        return node;
      }
    }
    return null;
  }, [isNodeVisible, screenToWorld]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0 || size.height === 0) {
      return;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) {
      return;
    }

    const pixelRatio = window.devicePixelRatio || 1;
    ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    ctx.clearRect(0, 0, size.width, size.height);

    const gradient = ctx.createRadialGradient(
      size.width * 0.5,
      size.height * 0.45,
      0,
      size.width * 0.5,
      size.height * 0.45,
      Math.max(size.width, size.height) * 0.75
    );
    gradient.addColorStop(0, '#172234');
    gradient.addColorStop(1, '#0c111b');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size.width, size.height);

    ctx.save();
    const transform = transformRef.current;
    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.scale, transform.scale);

    const hoveredId = hoveredNodeRef.current?.id || null;
    const relatedIds = new Set<string>();
    if (hoveredId) {
      linksRef.current.forEach((link) => {
        const source = getLinkNode(link.source);
        const target = getLinkNode(link.target);
        if (source?.id === hoveredId || target?.id === hoveredId) {
          if (source) {
            relatedIds.add(source.id);
          }
          if (target) {
            relatedIds.add(target.id);
          }
        }
      });
    }

    linksRef.current.forEach((link) => {
      const source = getLinkNode(link.source);
      const target = getLinkNode(link.target);
      if (!source || !target) {
        return;
      }
      if (!isNodeVisible(source) || !isNodeVisible(target)) {
        return;
      }

      const isHighlighted =
        !hoveredId ||
        source.id === hoveredId ||
        target.id === hoveredId ||
        source.id === highlightedGraphId ||
        target.id === highlightedGraphId ||
        source.id === activeGraphNodeId ||
        target.id === activeGraphNodeId;
      ctx.beginPath();
      ctx.moveTo(source.x || 0, source.y || 0);
      ctx.lineTo(target.x || 0, target.y || 0);
      ctx.strokeStyle = isHighlighted ? 'rgba(151, 177, 214, 0.52)' : 'rgba(119, 139, 170, 0.14)';
      ctx.lineWidth =
        (isHighlighted ? 1.2 + Math.min(1.2, (relationWeight[link.relation_type] || 1) * 0.35) : 0.75) /
        transform.scale;
      ctx.stroke();
    });

    nodesRef.current.forEach((node) => {
      if (!isNodeVisible(node)) {
        return;
      }

      const x = node.x || 0;
      const y = node.y || 0;
      const isHovered = node.id === hoveredId;
      const isSelected = node.id === highlightedGraphId;
      const isActive = node.id === activeGraphNodeId;
      const isRelated = relatedIds.has(node.id);
      const muted = Boolean(hoveredId) && !isHovered && !isRelated;
      const radius = node.radius * (isHovered || isSelected || isActive ? 1.45 : 1);

      ctx.beginPath();
      ctx.arc(x, y, radius + 4, 0, Math.PI * 2);
      ctx.fillStyle = isSelected
        ? 'rgba(255, 255, 255, 0.2)'
        : isActive
          ? 'rgba(255, 255, 255, 0.18)'
        : isHovered
          ? 'rgba(255, 255, 255, 0.15)'
          : 'rgba(255, 255, 255, 0.04)';
      ctx.fill();

      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = muted ? `${node.color}55` : node.color;
      ctx.fill();
      ctx.lineWidth = (isHovered || isSelected || isActive ? 2.2 : 1) / transform.scale;
      ctx.strokeStyle = isHovered || isSelected || isActive ? '#ffffff' : 'rgba(255, 255, 255, 0.45)';
      ctx.stroke();
    });

    const shouldShowLabels = nodesRef.current.length <= 36 || transform.scale > 1.15;
    nodesRef.current.forEach((node) => {
      if (!isNodeVisible(node)) {
        return;
      }

      const important =
        shouldShowLabels ||
        node.id === hoveredId ||
        node.id === highlightedGraphId ||
        node.id === activeGraphNodeId ||
        (nodesRef.current.length <= 80 && node.entity_type === 'node') ||
        node.entity_type === 'card';
      if (!important) {
        return;
      }
      drawRoundedLabel(
        ctx,
        node.label,
        node.x || 0,
        (node.y || 0) - node.radius - 14 / transform.scale,
        220 / transform.scale
      );
    });

    ctx.restore();
  }, [activeGraphNodeId, highlightedGraphId, isNodeVisible, size.height, size.width]);

  const updateHoveredNode = useCallback(
    (node: ForceNode | null) => {
      const currentId = hoveredNodeRef.current?.id || null;
      const nextId = node?.id || null;
      if (currentId === nextId) {
        return;
      }
      hoveredNodeRef.current = node;
      setHoveredNode(node);
      draw();
    },
    [draw]
  );

  const fitView = useCallback(() => {
    const visibleNodes = nodesRef.current.filter(isNodeVisible);
    if (size.width === 0 || size.height === 0 || visibleNodes.length === 0) {
      return;
    }

    const xs = visibleNodes.map((node) => node.x || 0);
    const ys = visibleNodes.map((node) => node.y || 0);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const graphWidth = Math.max(maxX - minX, 80);
    const graphHeight = Math.max(maxY - minY, 80);
    const scale = clamp(Math.min((size.width - 140) / graphWidth, (size.height - 150) / graphHeight), 0.28, 1.8);
    transformRef.current = {
      scale,
      x: size.width / 2 - ((minX + maxX) / 2) * scale,
      y: size.height / 2 + 22 - ((minY + maxY) / 2) * scale
    };
    draw();
  }, [draw, isNodeVisible, size.height, size.width]);

  const zoomView = useCallback(
    (factor: number) => {
      if (size.width === 0 || size.height === 0) {
        return;
      }
      const transform = transformRef.current;
      const centerX = size.width / 2;
      const centerY = size.height / 2;
      const worldX = (centerX - transform.x) / transform.scale;
      const worldY = (centerY - transform.y) / transform.scale;
      const nextScale = clamp(transform.scale * factor, 0.25, 3.2);
      transformRef.current = {
        scale: nextScale,
        x: centerX - worldX * nextScale,
        y: centerY - worldY * nextScale
      };
      hasUserTransformRef.current = true;
      draw();
    },
    [draw, size.height, size.width]
  );

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) {
      return undefined;
    }

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({
        width: Math.floor(width),
        height: Math.floor(height)
      });
    });
    observer.observe(shell);

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0 || size.height === 0) {
      return;
    }

    const pixelRatio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(size.width * pixelRatio));
    canvas.height = Math.max(1, Math.floor(size.height * pixelRatio));
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    draw();
  }, [draw, size.height, size.width]);

  useEffect(() => {
    simulationRef.current?.stop();

    nodesRef.current = model.nodes;
    linksRef.current = model.links;
    transformRef.current = { scale: 1, x: 0, y: 0 };
    hoveredNodeRef.current = null;
    setHoveredNode(null);
    hasUserTransformRef.current = false;

    const simulation = forceSimulation<ForceNode>(nodesRef.current)
      .force(
        'link',
        forceLink<ForceNode, ForceLink>(linksRef.current)
          .id((node) => node.id)
          .distance((link) => {
            const source = getLinkNode(link.source);
            const target = getLinkNode(link.target);
            const base = link.relation_type === 'MOUNTED_ON' ? 82 : link.relation_type === 'CONTAINS' ? 105 : 132;
            const linkWeight = relationWeight[link.relation_type] || 1;
            const degreeBoost = Math.min(28, ((source?.degree || 0) + (target?.degree || 0)) * 1.5);
            return Math.max(58, base - linkWeight * 12 + degreeBoost);
          })
          .strength((link) => clamp((relationWeight[link.relation_type] || 1) * 0.22, 0.08, 0.45))
      )
      .force('charge', forceManyBody<ForceNode>().strength((node) => -90 - node.weight * 72))
      .force('collision', forceCollide<ForceNode>().radius((node) => node.radius + 16).strength(0.92))
      .force('center', forceCenter(size.width / 2, size.height / 2))
      .force('x', forceX<ForceNode>(size.width / 2).strength(0.025))
      .force('y', forceY<ForceNode>(size.height / 2).strength(0.025))
      .alpha(0.9)
      .alphaDecay(0.045)
      .on('tick', draw);

    simulationRef.current = simulation;
    draw();
    const fitTimer = window.setTimeout(() => {
      if (!hasUserTransformRef.current) {
        fitView();
      }
    }, 500);

    return () => {
      window.clearTimeout(fitTimer);
      simulation.stop();
    };
  }, [draw, fitView, model.links, model.nodes, size.height, size.width]);

  const handleWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    const canvas = canvasRef.current;
    const rect = canvas?.getBoundingClientRect();
    if (!rect) {
      return;
    }

    const transform = transformRef.current;
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    const worldX = (pointerX - transform.x) / transform.scale;
    const worldY = (pointerY - transform.y) / transform.scale;
    const nextScale = clamp(transform.scale * Math.exp(-event.deltaY * 0.0012), 0.25, 3.2);

    transformRef.current = {
      scale: nextScale,
      x: pointerX - worldX * nextScale,
      y: pointerY - worldY * nextScale
    };
    hasUserTransformRef.current = true;
    draw();
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const node = findNodeAt(event.clientX, event.clientY);
    const canvas = canvasRef.current;
    canvas?.setPointerCapture(event.pointerId);
    pointerRef.current = {
      mode: node ? 'drag-node' : 'pan',
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startTransform: { ...transformRef.current },
      node,
      moved: false
    };

    if (node) {
      const point = screenToWorld(event.clientX, event.clientY);
      node.fx = point.x;
      node.fy = point.y;
      simulationRef.current?.alphaTarget(0.22).restart();
    }
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const pointer = pointerRef.current;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (rect) {
      setHoveredPoint({ x: event.clientX - rect.left, y: event.clientY - rect.top });
    }

    if (pointer.mode === 'drag-node' && pointer.node) {
      const point = screenToWorld(event.clientX, event.clientY);
      pointer.node.fx = point.x;
      pointer.node.fy = point.y;
      pointer.moved = true;
      hasUserTransformRef.current = true;
      updateHoveredNode(pointer.node);
      draw();
      return;
    }

    if (pointer.mode === 'pan') {
      pointer.moved = true;
      hasUserTransformRef.current = true;
      transformRef.current = {
        ...pointer.startTransform,
        x: pointer.startTransform.x + event.clientX - pointer.startX,
        y: pointer.startTransform.y + event.clientY - pointer.startY
      };
      draw();
      return;
    }

    updateHoveredNode(findNodeAt(event.clientX, event.clientY));
  };

  const finishPointer = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const pointer = pointerRef.current;
    if (pointer.node) {
      pointer.node.fx = null;
      pointer.node.fy = null;
      simulationRef.current?.alphaTarget(0);
      if (!pointer.moved && onNodeSelect) {
        onNodeSelect(pointer.node);
      }
    }
    canvasRef.current?.releasePointerCapture(event.pointerId);
    pointerRef.current = {
      mode: null,
      pointerId: null,
      startX: 0,
      startY: 0,
      startTransform: { ...transformRef.current },
      node: null,
      moved: false
    };
  };

  const legendItems = useMemo(
    () =>
      [...new Set(graph.nodes.map((node) => node.entity_type))].map((type) => ({
        type,
        label: entityLabel[type] || type,
        color: (entityStyle[type] || fallbackStyle).color
      })),
    [graph.nodes]
  );

  const visibleNodeCount = nodesRef.current.filter(isNodeVisible).length || model.nodes.filter(isNodeVisible).length;
  const visibleWeightMax = Math.max(
    1,
    ...(nodesRef.current.length ? nodesRef.current : model.nodes)
      .filter(isNodeVisible)
      .map((node) => node.weight)
  );
  const visibleEdgeCount = graph.edges.filter((edge) => {
    const from = nodesRef.current.find((node) => node.id === edge.from) || model.nodes.find((node) => node.id === edge.from);
    const to = nodesRef.current.find((node) => node.id === edge.to) || model.nodes.find((node) => node.id === edge.to);
    return Boolean(from && to && isNodeVisible(from) && isNodeVisible(to));
  }).length;

  const toggleType = (type: string) => {
    setHiddenTypes((current) => {
      const next = new Set(current);
      if (next.has(type)) {
        next.delete(type);
        return next;
      }
      if (visibleTypes.size <= 1) {
        return current;
      }
      next.add(type);
      return next;
    });
  };

  if (graph.nodes.length === 0) {
    return (
      <section className="graph-canvas-empty">
        <strong>暂无图谱节点</strong>
      </section>
    );
  }

  return (
    <section className="graph-canvas-panel" aria-label="图谱画布">
      <div className="graph-canvas-topbar">
        <div className="graph-stats">
          <strong>{visibleNodeCount}</strong>
          <span>Nodes</span>
          <strong>{visibleEdgeCount}</strong>
          <span>Edges</span>
          <strong>{visibleWeightMax.toFixed(1)}</strong>
          <span>Weight</span>
        </div>
        <button className="graph-fit-button" type="button" onClick={fitView} title="适配视图">
          <Maximize2 size={15} />
          适配
        </button>
        <button className="graph-fit-button graph-icon-only" type="button" onClick={() => zoomView(1.18)} title="放大">
          <ZoomIn size={15} />
        </button>
        <button className="graph-fit-button graph-icon-only" type="button" onClick={() => zoomView(0.86)} title="缩小">
          <ZoomOut size={15} />
        </button>
        <button
          className={`graph-fit-button ${showIsolated ? 'active' : ''}`}
          type="button"
          onClick={() => setShowIsolated((value) => !value)}
          title={showIsolated ? '隐藏孤立节点' : '显示孤立节点'}
        >
          {showIsolated ? <Eye size={15} /> : <EyeOff size={15} />}
          孤点
        </button>
      </div>

      <div className="graph-canvas-shell" ref={shellRef}>
        <canvas
          ref={canvasRef}
          className="graph-canvas"
          title="滚轮缩放"
          onWheel={handleWheel}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={finishPointer}
          onPointerCancel={finishPointer}
          onPointerLeave={() => updateHoveredNode(null)}
        />
        {hoveredNode ? (
          <div
            className="graph-tooltip"
            style={{
              left: hoveredPoint.x + 14,
              top: hoveredPoint.y + 14
            }}
          >
            <strong>{hoveredNode.label}</strong>
            <span>{entityLabel[hoveredNode.entity_type] || hoveredNode.entity_type}</span>
            <span>
              权重 {hoveredNode.weight.toFixed(1)} · 连接 {hoveredNode.degree.toFixed(1)}
            </span>
          </div>
        ) : null}
      </div>

      <div className="graph-legend" aria-label="图谱图例">
        {legendItems.map((item) => (
          <button
            className={hiddenTypes.has(item.type) ? 'muted' : ''}
            key={item.type}
            type="button"
            onClick={() => toggleType(item.type)}
          >
            <i style={{ background: item.color }} />
            {item.label}
          </button>
        ))}
      </div>
    </section>
  );
}
