import { Boxes } from 'lucide-react';
import type { NodeCardSummary } from '../api/lucasDbClient';

interface NodeCardPanelProps {
  selectedNodeId: string | null;
  cards: NodeCardSummary[];
}

export function NodeCardPanel({ selectedNodeId, cards }: NodeCardPanelProps) {
  if (!selectedNodeId) {
    return null;
  }

  return (
    <section className="node-card-panel" aria-label="结构化卡片">
      <div className="node-card-tabs" role="tablist">
        <button className="node-card-tab active" type="button" role="tab" aria-selected="true">
          <Boxes size={15} />
          卡片信息
          <span>{cards.length}</span>
        </button>
      </div>

      <div className="node-card-list">
        {cards.length === 0 ? (
          <div className="node-card-empty">暂无结构化卡片</div>
        ) : (
          cards.map((card) => (
            <article className="node-card-summary" key={card.id}>
              <div className="node-card-summary-title">
                <strong>{card.display_title}</strong>
                <span>
                  {card.schema_name} / {card.schema_version}
                </span>
              </div>
              {card.one_sentence_summary ? <p>{card.one_sentence_summary}</p> : null}
              {card.tags.length > 0 ? (
                <div className="node-card-tags">
                  {card.tags.map((tag) => (
                    <span key={`${card.id}-${tag}`}>{tag}</span>
                  ))}
                </div>
              ) : null}
            </article>
          ))
        )}
      </div>
    </section>
  );
}
