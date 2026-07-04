import { randomUUID } from 'node:crypto';
import { getDb } from './db';

export type NodeEventAction =
  | 'create'
  | 'update'
  | 'append'
  | 'delete'
  | 'move'
  | 'attach'
  | 'detach';

export interface NodeEventInput {
  nodeId: string | null;
  actor?: string;
  action: NodeEventAction;
  beforeSnapshot?: unknown;
  afterSnapshot?: unknown;
}

export const recordNodeEvent = (input: NodeEventInput) => {
  const db = getDb();
  const now = new Date().toISOString();

  db.prepare(
    `
      INSERT INTO node_events (
        id,
        node_id,
        actor,
        action,
        before_snapshot,
        after_snapshot,
        created_at
      )
      VALUES (
        @id,
        @node_id,
        @actor,
        @action,
        @before_snapshot,
        @after_snapshot,
        @created_at
      )
    `
  ).run({
    id: `event_${randomUUID()}`,
    node_id: input.nodeId,
    actor: input.actor || 'user',
    action: input.action,
    before_snapshot:
      input.beforeSnapshot === undefined ? null : JSON.stringify(input.beforeSnapshot),
    after_snapshot:
      input.afterSnapshot === undefined ? null : JSON.stringify(input.afterSnapshot),
    created_at: now
  });
};
