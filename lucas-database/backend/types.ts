export interface NodeRow {
  id: string;
  parent_id: string | null;
  title: string;
  slug: string | null;
  type: string;
  content: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface TreeNode {
  id: string;
  parent_id: string | null;
  title: string;
  type: string;
  children: TreeNode[];
}

export interface NodeChild {
  id: string;
  parent_id?: string | null;
  title: string;
  type: string;
}

export type NodeAttachmentKind = 'image' | 'video' | 'file';

export interface NodeAttachment {
  id: string;
  node_id: string;
  original_name: string;
  stored_name: string;
  relative_path: string;
  mime_type: string;
  size_bytes: number;
  kind: NodeAttachmentKind;
  created_at: string;
  deleted_at: string | null;
}

export interface NodeDetail extends NodeRow {
  children: NodeChild[];
  attachments: NodeAttachment[];
}

export interface SearchResult {
  id: string;
  title: string;
  path: string;
  snippet: string;
}
