import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import type { GraphData, GraphNode } from '../api/graphClient';

interface Graph3DPanelProps {
  graph: GraphData;
  selectedNodeId?: string | null;
  activeGraphNodeId?: string | null;
  onNodeSelect?: (node: GraphNode) => void;
}

interface SceneNode {
  id: string;
  entity_type: string;
  label: string;
  graphNode: GraphNode;
  color: string;
  radius: number;
  position: THREE.Vector3;
  labelPriority: number;
  degree: number;
  weight: number;
  isHub: boolean;
  systemId: string;
  orbitRadius: number;
  hubRank: number;
}

interface ViewAngles {
  theta: number;
  phi: number;
  distance: number;
}

type SceneLineKind = 'galaxy' | 'orbit' | 'edge';

interface SceneLine {
  geometry: THREE.BufferGeometry;
  material: THREE.LineBasicMaterial;
  kind: SceneLineKind;
  group?: string;
  baseOpacity: number;
}

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
  node: { color: '#62a8ff', radius: 4.8 },
  card: { color: '#f7b955', radius: 5.6 },
  concept: { color: '#a78bfa', radius: 4.1 },
  tag: { color: '#6fdc8c', radius: 3.6 },
  risk: { color: '#ff6b6b', radius: 4.2 },
  action: { color: '#44d7c9', radius: 4.0 },
  method: { color: '#ff9f43', radius: 4.0 },
  source: { color: '#c7ced8', radius: 3.5 },
  model: { color: '#f472b6', radius: 3.5 }
};

const fallbackStyle = { color: '#8ea0b8', radius: 3.6 };
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
const goldenAngle = Math.PI * (3 - Math.sqrt(5));
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

const labelPriority: Record<string, number> = {
  node: 3,
  card: 3,
  concept: 2,
  tag: 1,
  risk: 2,
  action: 2,
  method: 2,
  source: 1,
  model: 1
};

const createSceneNodes = (graph: GraphData) => {
  const degree = new Map<string, number>();
  const neighbors = new Map<string, Set<string>>();
  graph.edges.forEach((edge) => {
    const weight = relationWeight[edge.relation_type] || 1;
    degree.set(edge.from, (degree.get(edge.from) || 0) + weight);
    degree.set(edge.to, (degree.get(edge.to) || 0) + weight);
    if (!neighbors.has(edge.from)) {
      neighbors.set(edge.from, new Set());
    }
    if (!neighbors.has(edge.to)) {
      neighbors.set(edge.to, new Set());
    }
    neighbors.get(edge.from)?.add(edge.to);
    neighbors.get(edge.to)?.add(edge.from);
  });

  if (graph.nodes.length === 0) {
    return [];
  }

  const weightedNodes = graph.nodes
    .map((node, index) => ({
      node,
      index,
      degree: degree.get(node.id) || 0,
      weight: Math.max(1, Math.log2((degree.get(node.id) || 0) + 2))
    }))
    .sort((left, right) => right.weight - left.weight || left.index - right.index);

  let hubs = weightedNodes.filter(
    (item) => item.node.entity_type === 'node' || item.node.entity_type === 'card'
  );
  if (hubs.length === 0) {
    hubs = weightedNodes.slice(0, Math.min(12, weightedNodes.length));
  }

  const hubIds = new Set(hubs.map((item) => item.node.id));
  const hubOrder = new Map<string, number>();
  const hubPositions = new Map<string, THREE.Vector3>();
  const armCount = clamp(Math.ceil(Math.sqrt(Math.max(4, hubs.length))), 4, 7);
  const maxRank = Math.max(1, Math.ceil(hubs.length / armCount));
  const ringStep = clamp(18 + Math.sqrt(graph.nodes.length) * 0.45, 20, 28);

  hubs.forEach((item, index) => {
    const arm = index % armCount;
    const rank = Math.floor(index / armCount);
    const armAngle = (Math.PI * 2 * arm) / armCount;
    const normalizedRank = rank / Math.max(1, maxRank - 1);
    const radius = 34 + rank * ringStep + Math.min(12, item.weight * 2.4);
    const angle = armAngle + normalizedRank * 1.25 + rank * 0.34;
    const vertical = ((arm % 2) - 0.5) * 6 + ((rank % 3) - 1) * 2.2;
    hubOrder.set(item.node.id, index);
    hubPositions.set(
      item.node.id,
      new THREE.Vector3(Math.cos(angle) * radius, vertical, Math.sin(angle) * radius)
    );
  });

  const assignedHub = new Map<string, string>();
  const findHubForNode = (node: GraphNode, fallbackIndex: number) => {
    if (hubIds.has(node.id)) {
      return node.id;
    }

    const neighborHub = [...(neighbors.get(node.id) || [])]
      .filter((id) => hubIds.has(id))
      .sort((left, right) => (degree.get(right) || 0) - (degree.get(left) || 0))[0];
    if (neighborHub) {
      return neighborHub;
    }

    const indirectHub = [...(neighbors.get(node.id) || [])]
      .flatMap((id) => [...(neighbors.get(id) || [])])
      .filter((id) => hubIds.has(id))
      .sort((left, right) => (degree.get(right) || 0) - (degree.get(left) || 0))[0];
    if (indirectHub) {
      return indirectHub;
    }

    return hubs[fallbackIndex % hubs.length]?.node.id || node.id;
  };

  graph.nodes.forEach((node, index) => {
    assignedHub.set(node.id, findHubForNode(node, index));
  });

  const childBuckets = new Map<string, GraphNode[]>();
  graph.nodes.forEach((node) => {
    if (hubIds.has(node.id)) {
      return;
    }
    const systemId = assignedHub.get(node.id) || hubs[0]?.node.id || node.id;
    const bucket = childBuckets.get(systemId) || [];
    bucket.push(node);
    childBuckets.set(systemId, bucket);
  });

  const makeSceneNode = (
    node: GraphNode,
    position: THREE.Vector3,
    isHub: boolean,
    systemId: string,
    orbitRadius: number,
    hubRank: number
  ): SceneNode => {
    const style = entityStyle[node.entity_type] || fallbackStyle;
    const nodeDegree = degree.get(node.id) || 0;
    const weight = Math.max(1, Math.log2(nodeDegree + 2));
    return {
      id: node.id,
      entity_type: node.entity_type,
      label: node.label,
      graphNode: node,
      color: style.color,
      radius:
        style.radius * (isHub ? 0.72 : 0.34) +
        Math.min(isHub ? 3.1 : 1.35, weight * (isHub ? 0.48 : 0.22)),
      labelPriority: labelPriority[node.entity_type] || 1,
      degree: nodeDegree,
      weight,
      position,
      isHub,
      systemId,
      orbitRadius,
      hubRank
    };
  };

  const sceneNodes: SceneNode[] = hubs.map((item, index) =>
    makeSceneNode(item.node, hubPositions.get(item.node.id) || new THREE.Vector3(), true, item.node.id, 0, index)
  );

  childBuckets.forEach((children, systemId) => {
    const parentPosition = hubPositions.get(systemId) || new THREE.Vector3();
    const parentOrder = hubOrder.get(systemId) || 0;
    const parentWeight = Math.max(1, Math.log2((degree.get(systemId) || 0) + 2));
    const sortedChildren = [...children].sort(
      (left, right) =>
        (labelPriority[right.entity_type] || 1) - (labelPriority[left.entity_type] || 1) ||
        (degree.get(right.id) || 0) - (degree.get(left.id) || 0) ||
        left.label.localeCompare(right.label)
    );
    sortedChildren.forEach((node, index) => {
      const ring = Math.floor(index / 9);
      const ringStart = ring * 9;
      const ringCount = Math.min(9 + ring * 3, sortedChildren.length - ringStart);
      const localIndex = index - ringStart;
      const orbitRadius = 14 + ring * 9 + Math.min(10, parentWeight * 1.8);
      const angle =
        (Math.PI * 2 * localIndex) / Math.max(1, ringCount) +
        parentOrder * 0.31 +
        ring * 0.48 +
        goldenAngle;
      const vertical = Math.sin(angle * 1.7 + parentOrder) * (2.8 + ring * 0.8);
      const offset = new THREE.Vector3(
        Math.cos(angle) * orbitRadius,
        vertical,
        Math.sin(angle) * orbitRadius
      );
      sceneNodes.push(
        makeSceneNode(node, parentPosition.clone().add(offset), false, systemId, orbitRadius, parentOrder)
      );
    });
  });

  return sceneNodes;
};

const updateCamera = (camera: THREE.PerspectiveCamera, angles: ViewAngles) => {
  const phi = clamp(angles.phi, -1.1, 1.1);
  const cosPhi = Math.cos(phi);
  camera.position.set(
    Math.sin(angles.theta) * cosPhi * angles.distance,
    Math.sin(phi) * angles.distance,
    Math.cos(angles.theta) * cosPhi * angles.distance
  );
  camera.lookAt(0, 0, 0);
};

export function Graph3DPanel({ graph, selectedNodeId, activeGraphNodeId, onNodeSelect }: Graph3DPanelProps) {
  const shellRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const frameRef = useRef<number | null>(null);
  const rootRef = useRef<THREE.Group | null>(null);
  const raycasterRef = useRef(new THREE.Raycaster());
  const nodeMeshesRef = useRef<THREE.Object3D[]>([]);
  const nodeObjectsRef = useRef(
    new Map<string, { mesh: THREE.Mesh; halo: THREE.Mesh; node: SceneNode }>()
  );
  const labelsRef = useRef<Array<{ node: SceneNode; element: HTMLDivElement }>>([]);
  const pointerRef = useRef({ active: false, x: 0, y: 0, startX: 0, startY: 0, moved: false });
  const viewRef = useRef<ViewAngles>({ theta: 0.7, phi: 0.22, distance: 224 });
  const activeIdRef = useRef<string | null>(activeGraphNodeId || null);
  const highlightedIdRef = useRef<string | null>(selectedNodeId ? `node:${selectedNodeId}` : null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const highlightedGraphId = selectedNodeId ? `node:${selectedNodeId}` : null;
  const sceneNodes = useMemo(() => createSceneNodes(graph), [graph]);
  const maxSceneWeight = Math.max(1, ...sceneNodes.map((node) => node.weight));
  const sceneRadius = useMemo(
    () => Math.max(96, ...sceneNodes.map((node) => node.position.length() + node.radius + node.orbitRadius * 0.18)),
    [sceneNodes]
  );
  const cameraLimits = useMemo(
    () => ({
      min: 118,
      fit: clamp(sceneRadius * 2.42, 360, 980),
      max: clamp(sceneRadius * 2.75, 460, 1120)
    }),
    [sceneRadius]
  );

  useEffect(() => {
    activeIdRef.current = activeGraphNodeId || null;
    highlightedIdRef.current = highlightedGraphId;
    nodeObjectsRef.current.forEach(({ mesh, halo, node }) => {
      const active = node.id === activeIdRef.current || node.id === highlightedIdRef.current;
      mesh.scale.setScalar(active ? node.radius * 1.28 : node.radius);
      const meshMaterial = mesh.material instanceof THREE.MeshStandardMaterial ? mesh.material : null;
      if (meshMaterial) {
        meshMaterial.emissiveIntensity = active ? 0.58 : 0.25;
      }
      halo.scale.setScalar((active ? node.radius * 1.28 : node.radius) + 1.5);
      const haloMaterial = halo.material instanceof THREE.MeshBasicMaterial ? halo.material : null;
      if (haloMaterial) {
        haloMaterial.opacity = active ? 0.13 : 0.025;
      }
    });
  }, [activeGraphNodeId, highlightedGraphId]);

  const fitView = useCallback(() => {
    const camera = cameraRef.current;
    if (!camera) {
      return;
    }
    viewRef.current = { theta: 0.75, phi: 0.36, distance: cameraLimits.fit };
    updateCamera(camera, viewRef.current);
  }, [cameraLimits.fit]);

  const zoomCamera = useCallback((delta: number) => {
    const camera = cameraRef.current;
    if (!camera) {
      return;
    }
    viewRef.current = {
      ...viewRef.current,
      distance: clamp(viewRef.current.distance + delta, cameraLimits.min, cameraLimits.max)
    };
    updateCamera(camera, viewRef.current);
  }, [cameraLimits.max, cameraLimits.min]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) {
      return undefined;
    }

    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.floor(entry.contentRect.width),
        height: Math.floor(entry.contentRect.height)
      });
    });
    observer.observe(shell);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell || size.width === 0 || size.height === 0) {
      return undefined;
    }

    shell.querySelectorAll('.graph-3d-label').forEach((label) => label.remove());
    labelsRef.current = [];
    nodeMeshesRef.current = [];
    nodeObjectsRef.current.clear();

    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#0c111b');
    scene.fog = null;

    const camera = new THREE.PerspectiveCamera(42, size.width / size.height, 0.1, Math.max(1400, cameraLimits.max * 1.8));
    cameraRef.current = camera;
    fitView();

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(size.width, size.height);
    rendererRef.current = renderer;
    shell.appendChild(renderer.domElement);

    const root = new THREE.Group();
    rootRef.current = root;
    scene.add(root);

    const ambient = new THREE.AmbientLight('#d9e8ff', 1.8);
    scene.add(ambient);
    const keyLight = new THREE.PointLight('#8dbbff', 140, 420);
    keyLight.position.set(80, 110, 120);
    scene.add(keyLight);

    const nodeMap = new Map(sceneNodes.map((node) => [node.id, node]));
    const sphereGeometry = new THREE.SphereGeometry(1, 24, 16);
    const lineDisposables: SceneLine[] = [];
    const createOrbitRing = (
      center: THREE.Vector3,
      radius: number,
      color: string,
      opacity: number,
      segments = 96,
      kind: SceneLineKind = 'orbit'
    ) => {
      const points: THREE.Vector3[] = [];
      for (let index = 0; index < segments; index += 1) {
        const angle = (Math.PI * 2 * index) / segments;
        points.push(
          new THREE.Vector3(
            center.x + Math.cos(angle) * radius,
            center.y,
            center.z + Math.sin(angle) * radius
          )
        );
      }
      const geometry = new THREE.BufferGeometry().setFromPoints(points);
      const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
      lineDisposables.push({ geometry, material, kind, baseOpacity: opacity });
      root.add(new THREE.LineLoop(geometry, material));
    };

    const galaxyRadius = Math.max(54, ...sceneNodes.filter((node) => node.isHub).map((node) => node.position.length()));
    [0.32, 0.52, 0.72, 0.92, 1.12].forEach((ratio) =>
      createOrbitRing(
        new THREE.Vector3(0, 0, 0),
        galaxyRadius * ratio,
        '#35506f',
        ratio >= 1 ? 0.18 : 0.12,
        96,
        'galaxy'
      )
    );
    const childrenBySystem = new Map<string, SceneNode[]>();
    sceneNodes.forEach((node) => {
      if (node.isHub) {
        return;
      }
      const bucket = childrenBySystem.get(node.systemId) || [];
      bucket.push(node);
      childrenBySystem.set(node.systemId, bucket);
    });
    sceneNodes
      .filter((node) => node.isHub)
      .sort((left, right) => right.weight - left.weight)
      .slice(0, 34)
      .forEach((hub) => {
        const children = childrenBySystem.get(hub.id) || [];
        const maxOrbit = Math.max(0, ...children.map((node) => node.orbitRadius));
        if (children.length >= 3 && maxOrbit > 0) {
          createOrbitRing(hub.position, maxOrbit + 2, hub.color, 0.12, 64, 'orbit');
        }
      });

    sceneNodes.forEach((node) => {
      const active = node.id === highlightedIdRef.current || node.id === activeIdRef.current;
      const material = new THREE.MeshStandardMaterial({
        color: node.color,
        emissive: node.color,
        emissiveIntensity: active ? 0.58 : 0.25,
        transparent: true,
        opacity: active ? 1 : 0.92,
        roughness: 0.42,
        metalness: 0.1
      });
      const mesh = new THREE.Mesh(sphereGeometry, material);
      mesh.position.copy(node.position);
      mesh.scale.setScalar(active ? node.radius * 1.28 : node.radius);
      mesh.userData.graphNode = node.graphNode;
      nodeMeshesRef.current.push(mesh);
      root.add(mesh);

      const halo = new THREE.Mesh(
        sphereGeometry,
        new THREE.MeshBasicMaterial({
          color: node.color,
          transparent: true,
          opacity: active ? 0.13 : 0.025
        })
      );
      halo.position.copy(node.position);
      halo.scale.setScalar((active ? node.radius * 1.28 : node.radius) + 1.5);
      root.add(halo);
      nodeObjectsRef.current.set(node.id, { mesh, halo, node });

      const label = document.createElement('div');
      label.className = 'graph-3d-label';
      label.dataset.priority = String(node.labelPriority);
      label.textContent = node.label.length > 34 ? `${node.label.slice(0, 33)}...` : node.label;
      shell.appendChild(label);
      labelsRef.current.push({ node, element: label });
    });

    const lineGroups = new Map<string, number[]>();
    graph.edges.forEach((edge) => {
      const source = nodeMap.get(edge.from);
      const target = nodeMap.get(edge.to);
      if (!source || !target) {
        return;
      }
      const weight = relationWeight[edge.relation_type] || 1;
      const group = weight >= 1.2 ? 'strong' : weight < 0.8 ? 'weak' : 'normal';
      const linePositions = lineGroups.get(group) || [];
      linePositions.push(
        source.position.x,
        source.position.y,
        source.position.z,
        target.position.x,
        target.position.y,
        target.position.z
      );
      lineGroups.set(group, linePositions);
    });
    const lineStyles: Record<string, { color: string; opacity: number }> = {
      strong: { color: '#adc6f2', opacity: 0.2 },
      normal: { color: '#8ea8d0', opacity: 0.085 },
      weak: { color: '#6f839f', opacity: 0.028 }
    };
    lineGroups.forEach((linePositions, group) => {
      const lineGeometry = new THREE.BufferGeometry();
      lineGeometry.setAttribute('position', new THREE.Float32BufferAttribute(linePositions, 3));
      const style = lineStyles[group] || lineStyles.normal;
      const lineMaterial = new THREE.LineBasicMaterial({
        color: style.color,
        transparent: true,
        opacity: style.opacity
      });
      lineDisposables.push({ geometry: lineGeometry, material: lineMaterial, kind: 'edge', group, baseOpacity: style.opacity });
      root.add(new THREE.LineSegments(lineGeometry, lineMaterial));
    });

    const animate = () => {
      frameRef.current = window.requestAnimationFrame(animate);
      if (!pointerRef.current.active) {
        root.rotation.y += 0.0011;
      }
      const distance = viewRef.current.distance;
      const globalView = distance >= cameraLimits.fit * 0.82;
      const midView = distance >= 240 && !globalView;
      const nearScale = distance < 170 ? clamp(distance / 170, 0.62, 1) : 1;
      nodeObjectsRef.current.forEach(({ mesh, halo, node }) => {
        const active = node.id === highlightedIdRef.current || node.id === activeIdRef.current;
        const topHub = node.isHub && node.hubRank < (globalView ? 18 : 32);
        const detailNode = !node.isHub && (node.weight >= 2.15 || node.labelPriority >= 2);
        const opacity = active
          ? 1
          : globalView
            ? node.isHub
              ? topHub
                ? 0.95
                : 0.52
              : detailNode
                ? 0.36
                : 0.14
            : midView
              ? node.isHub
                ? 0.95
                : detailNode
                  ? 0.48
                  : 0.22
              : 0.92;
        const scale =
          node.radius *
          nearScale *
          (active ? 1.28 : 1) *
          (globalView && !node.isHub ? 0.62 : midView && !node.isHub ? 0.78 : 1);
        mesh.visible = opacity > 0.045;
        halo.visible = active || node.isHub || (!globalView && detailNode);
        mesh.scale.setScalar(scale);
        halo.scale.setScalar(scale + (node.isHub ? 1.4 : 0.85));
        const meshMaterial = mesh.material instanceof THREE.MeshStandardMaterial ? mesh.material : null;
        if (meshMaterial) {
          meshMaterial.opacity = opacity;
          meshMaterial.emissiveIntensity = active ? 0.58 : node.isHub ? 0.24 : 0.13;
        }
        const haloMaterial = halo.material instanceof THREE.MeshBasicMaterial ? halo.material : null;
        if (haloMaterial) {
          haloMaterial.opacity = active ? 0.13 : node.isHub ? (globalView ? 0.028 : 0.045) : 0.014;
        }
      });
      lineDisposables.forEach((line) => {
        if (line.kind === 'galaxy') {
          line.material.opacity = line.baseOpacity * (globalView ? 1.45 : midView ? 0.9 : 0.48);
          return;
        }
        if (line.kind === 'orbit') {
          line.material.opacity = line.baseOpacity * (globalView ? 0.72 : midView ? 0.86 : 0.62);
          return;
        }
        const edgeFactor = globalView
          ? line.group === 'strong'
            ? 0.58
            : line.group === 'normal'
              ? 0.28
              : 0.12
          : midView
            ? line.group === 'weak'
              ? 0.28
              : 0.66
            : line.group === 'weak'
              ? 0.48
              : 0.7;
        line.material.opacity = line.baseOpacity * edgeFactor;
      });
      renderer.render(scene, camera);

      const labelBoxes: Array<{ left: number; right: number; top: number; bottom: number }> = [];
      let visibleLabelCount = 0;
      const maxVisibleLabels = globalView ? 4 : midView ? 12 : distance < 135 ? 26 : 18;
      labelsRef.current.forEach(({ node, element }) => {
        const worldPosition = node.position.clone().applyMatrix4(root.matrixWorld);
        const projected = worldPosition.clone().project(camera);
        const screenX = (projected.x * 0.5 + 0.5) * size.width;
        const screenY = (-projected.y * 0.5 + 0.5) * size.height;
        const onScreen =
          projected.z > -1 &&
          projected.z < 1 &&
          projected.x > -1.08 &&
          projected.x < 1.08 &&
          projected.y > -1.08 &&
          projected.y < 1.08;
        const importantHub = node.isHub && (node.weight >= 2.2 || node.entity_type === 'node' || node.entity_type === 'card');
        const showLabel =
          onScreen &&
          (node.id === highlightedIdRef.current ||
            node.id === activeIdRef.current ||
            (globalView && node.isHub && node.hubRank < 8) ||
            (midView && node.isHub && node.hubRank < 20) ||
            (distance < 235 && importantHub && node.weight >= 2.35) ||
            (distance < 175 && importantHub) ||
            (distance < 145 && !node.isHub && (node.weight >= 2.15 || node.labelPriority >= 2)) ||
            (distance < 128 && !node.isHub && node.weight >= 1.45));
        const depthFade = clamp(1 - (projected.z + 0.2) * 0.36, 0.36, 1);
        const labelWidth = Math.min(260, 42 + Math.min(node.label.length, 34) * 6.8);
        const box = {
          left: screenX - labelWidth / 2,
          right: screenX + labelWidth / 2,
          top: screenY - 34,
          bottom: screenY - 8
        };
        const collides = labelBoxes.some(
          (item) => box.left < item.right && box.right > item.left && box.top < item.bottom && box.bottom > item.top
        );
        const visibleLabel = showLabel && !collides && visibleLabelCount < maxVisibleLabels;
        if (visibleLabel) {
          labelBoxes.push(box);
          visibleLabelCount += 1;
        }
        element.style.opacity = visibleLabel ? String(depthFade) : '0';
        element.style.transform = `translate3d(${screenX}px, ${screenY}px, 0) translate(-50%, -120%)`;
      });
    };
    animate();

    return () => {
      if (frameRef.current) {
        window.cancelAnimationFrame(frameRef.current);
      }
      labelsRef.current.forEach(({ element }) => element.remove());
      labelsRef.current = [];
      nodeMeshesRef.current = [];
      nodeObjectsRef.current.clear();
      renderer.dispose();
      sphereGeometry.dispose();
      lineDisposables.forEach(({ geometry, material }) => {
        geometry.dispose();
        material.dispose();
      });
      shell.removeChild(renderer.domElement);
      root.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const material = object.material;
          if (Array.isArray(material)) {
            material.forEach((item) => item.dispose());
          } else {
            material.dispose();
          }
        }
      });
    };
  }, [fitView, graph.edges, sceneNodes, size.height, size.width]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (!renderer || !camera || size.width === 0 || size.height === 0) {
      return;
    }
    renderer.setSize(size.width, size.height);
    camera.aspect = size.width / size.height;
    camera.updateProjectionMatrix();
  }, [size.height, size.width]);

  const selectNodeAt = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const shell = shellRef.current;
      const camera = cameraRef.current;
      if (!shell || !camera || !onNodeSelect) {
        return;
      }
      const rect = shell.getBoundingClientRect();
      const pointer = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1
      );
      raycasterRef.current.setFromCamera(pointer, camera);
      const hit = raycasterRef.current.intersectObjects(nodeMeshesRef.current, false)[0];
      const graphNode = hit?.object.userData.graphNode as GraphNode | undefined;
      if (graphNode) {
        onNodeSelect(graphNode);
      }
    },
    [onNodeSelect]
  );

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    pointerRef.current = {
      active: true,
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      moved: false
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!pointerRef.current.active || !rootRef.current) {
      return;
    }
    const dx = event.clientX - pointerRef.current.x;
    const dy = event.clientY - pointerRef.current.y;
    pointerRef.current.x = event.clientX;
    pointerRef.current.y = event.clientY;
    if (
      Math.abs(event.clientX - pointerRef.current.startX) > 3 ||
      Math.abs(event.clientY - pointerRef.current.startY) > 3
    ) {
      pointerRef.current.moved = true;
    }
    rootRef.current.rotation.y += dx * 0.006;
    rootRef.current.rotation.x = clamp(rootRef.current.rotation.x + dy * 0.004, -0.85, 0.85);
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!pointerRef.current.moved) {
      selectNodeAt(event);
    }
    pointerRef.current.active = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const camera = cameraRef.current;
    if (!camera) {
      return;
    }
    viewRef.current = {
      ...viewRef.current,
      distance: clamp(viewRef.current.distance + event.deltaY * 0.32, cameraLimits.min, cameraLimits.max)
    };
    updateCamera(camera, viewRef.current);
  };

  if (graph.nodes.length === 0) {
    return (
      <section className="graph-canvas-empty">
        <strong>暂无图谱节点</strong>
      </section>
    );
  }

  return (
    <section className="graph-3d-panel" aria-label="3D 图谱画布">
      <div className="graph-canvas-topbar">
        <div className="graph-stats">
          <strong>{graph.nodes.length}</strong>
          <span>Nodes</span>
          <strong>{graph.edges.length}</strong>
          <span>Edges</span>
          <strong>{maxSceneWeight.toFixed(1)}</strong>
          <span>Weight</span>
        </div>
        <button className="graph-fit-button" type="button" onClick={fitView} title="重置 3D 视角">
          <Maximize2 size={15} />
          重置
        </button>
        <button className="graph-fit-button graph-icon-only" type="button" onClick={() => zoomCamera(-72)} title="放大">
          <ZoomIn size={15} />
        </button>
        <button className="graph-fit-button graph-icon-only" type="button" onClick={() => zoomCamera(92)} title="缩小">
          <ZoomOut size={15} />
        </button>
      </div>
      <div
        className="graph-3d-shell"
        ref={shellRef}
        title="滚轮缩放"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onWheel={handleWheel}
      />
      <div className="graph-legend" aria-label="3D 图谱图例">
        {[...new Set(graph.nodes.map((node) => node.entity_type))].map((type) => {
          const style = entityStyle[type] || fallbackStyle;
          return (
            <span key={type}>
              <i style={{ background: style.color }} />
              {entityLabel[type] || type}
            </span>
          );
        })}
      </div>
    </section>
  );
}
