import { makeChunk } from './chunker';
import type { Chunker, ChunkInput, ChunkOutput } from './chunkTypes';

type JsonRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is JsonRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const asString = (value: unknown): string | null => {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const pickString = (record: JsonRecord, keys: string[]) => {
  for (const key of keys) {
    const value = asString(record[key]);
    if (value) {
      return value;
    }
  }
  return null;
};

const valueToText = (value: unknown): string | null => {
  const direct = asString(value);
  if (direct) {
    return direct;
  }

  if (isRecord(value)) {
    const picked = pickString(value, [
      'content',
      'text',
      'title',
      'name',
      'value',
      'summary',
      'concept',
      'explanation',
      'evidence',
      'action',
      'risk',
      'quote',
      'method'
    ]);
    return picked || JSON.stringify(value);
  }

  if (value === undefined || value === null) {
    return null;
  }

  return String(value);
};

const getArrayForFields = (record: JsonRecord, fields: string[]) => {
  for (const field of fields) {
    const value = record[field];
    if (Array.isArray(value) && value.length > 0) {
      return { field, values: value };
    }
  }
  return { field: fields[0], values: [] as unknown[] };
};

const formatRecordFields = (
  record: JsonRecord,
  fields: Array<[string, string]>
): string | null => {
  const lines = fields
    .map(([field, label]) => {
      const text = valueToText(record[field]);
      return text ? `${label}: ${text}` : null;
    })
    .filter((line): line is string => Boolean(line));
  return lines.length > 0 ? lines.join('\n') : valueToText(record);
};

const formatListChunk = (values: unknown[]) => {
  const lines = values
    .map(valueToText)
    .filter((text): text is string => Boolean(text))
    .map((text) => `- ${text}`);
  return lines.length > 0 ? lines.join('\n') : null;
};

const formatCommentSignals = (value: unknown) => {
  if (!isRecord(value)) {
    return valueToText(value);
  }

  const lines = Object.entries(value)
    .map(([key, fieldValue]) => {
      const text = valueToText(fieldValue);
      return text ? `${key}: ${text}` : null;
    })
    .filter((line): line is string => Boolean(line));
  return lines.length > 0 ? lines.join('\n') : null;
};

export class CardAwareChunker implements Chunker {
  chunk(input: ChunkInput): ChunkOutput[] {
    if (!isRecord(input.rawJson)) {
      return [];
    }

    const card = input.rawJson;
    const schemaName = asString(card.schema_name) || 'ComposedCardV1';
    const schemaVersion = asString(card.schema_version) || '1';
    const chunks: ChunkOutput[] = [];
    const addChunk = (
      chunkType: string,
      chunkText: string | null,
      field: string,
      fieldIndex: number
    ) => {
      if (!chunkText) {
        return;
      }

      const chunk = makeChunk(input, {
        sourceTable: 'cards',
        chunkType,
        chunkText,
        chunkIndex: chunks.length,
        metadata: {
          schema_name: schemaName,
          schema_version: schemaVersion,
          field,
          field_index: fieldIndex
        }
      });
      if (chunk) {
        chunks.push(chunk);
      }
    };

    addChunk(
      'card_summary',
      [
        asString(card.display_title),
        asString(card.one_sentence_summary),
        asString(card.original_summary)
      ]
        .filter((text): text is string => Boolean(text))
        .join('\n'),
      'summary',
      0
    );

    this.addArrayAggregate(input, card, chunks, 'core_points', ['core_points'], schemaName, schemaVersion);
    this.addKnowledgeBlocks(input, card, chunks, schemaName, schemaVersion);
    this.addArrayAggregate(input, card, chunks, 'methodology', ['methodology'], schemaName, schemaVersion);
    this.addArrayAggregate(
      input,
      card,
      chunks,
      'application_suggestions',
      ['application_suggestions', 'application_suggestion'],
      schemaName,
      schemaVersion
    );
    this.addArrayAggregate(
      input,
      card,
      chunks,
      'follow_up_actions',
      ['follow_up_actions', 'follow_up_action'],
      schemaName,
      schemaVersion
    );
    this.addArrayAggregate(
      input,
      card,
      chunks,
      'reusable_value',
      ['reusable_value', 'reusable_values'],
      schemaName,
      schemaVersion
    );
    this.addArrayAggregate(input, card, chunks, 'risks', ['risks', 'risk'], schemaName, schemaVersion);
    this.addArrayAggregate(
      input,
      card,
      chunks,
      'evidence_quotes',
      ['evidence_quotes', 'evidence_quote'],
      schemaName,
      schemaVersion
    );

    const commentSignals = card.comment_signals || card.comment_signal;
    addChunk('comment_signals', formatCommentSignals(commentSignals), 'comment_signals', 0);

    return chunks;
  }

  private addArrayAggregate(
    input: ChunkInput,
    card: JsonRecord,
    chunks: ChunkOutput[],
    chunkType: string,
    fields: string[],
    schemaName: string,
    schemaVersion: string
  ) {
    const { field, values } = getArrayForFields(card, fields);
    const chunkText = formatListChunk(values);
    if (!chunkText) {
      return;
    }

    const chunk = makeChunk(input, {
      sourceTable: 'cards',
      chunkType,
      chunkText,
      chunkIndex: chunks.length,
      metadata: {
        schema_name: schemaName,
        schema_version: schemaVersion,
        field,
        field_index: 0
      }
    });
    if (chunk) {
      chunks.push(chunk);
    }
  }

  private addKnowledgeBlocks(
    input: ChunkInput,
    card: JsonRecord,
    chunks: ChunkOutput[],
    schemaName: string,
    schemaVersion: string
  ) {
    const { values } = getArrayForFields(card, ['knowledge_blocks']);
    values.forEach((value, index) => {
      const chunkText = isRecord(value)
        ? formatRecordFields(value, [
            ['concept', 'Concept'],
            ['explanation', 'Explanation'],
            ['evidence', 'Evidence'],
            ['reusable_value', 'Reusable value']
          ])
        : valueToText(value);
      if (!chunkText) {
        return;
      }

      const chunk = makeChunk(input, {
        sourceTable: 'cards',
        chunkType: 'knowledge_block',
        chunkText,
        chunkIndex: chunks.length,
        metadata: {
          schema_name: schemaName,
          schema_version: schemaVersion,
          field: 'knowledge_blocks',
          field_index: index
        }
      });
      if (chunk) {
        chunks.push(chunk);
      }
    });
  }
}
