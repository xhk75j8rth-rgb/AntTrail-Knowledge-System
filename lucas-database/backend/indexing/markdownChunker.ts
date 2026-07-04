import { makeChunk } from './chunker';
import type { Chunker, ChunkInput, ChunkOutput } from './chunkTypes';

interface MarkdownSection {
  heading: string | null;
  headingLevel: number | null;
  text: string;
  sectionIndex: number;
}

const headingPattern = /^(#{1,3})\s+(.+?)\s*#*\s*$/;
const maxChunkChars = 1100;
const overlapChars = 120;

const startsFence = (line: string) => {
  const trimmed = line.trim();
  return trimmed.startsWith('```') || trimmed.startsWith('~~~');
};

const splitIntoSections = (content: string): { hasHeading: boolean; sections: MarkdownSection[] } => {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const sections: MarkdownSection[] = [];
  let currentLines: string[] = [];
  let currentHeading: string | null = null;
  let currentLevel: number | null = null;
  let hasHeading = false;
  let inFence = false;

  const flush = () => {
    const text = currentLines.join('\n').trim();
    if (!text) {
      currentLines = [];
      return;
    }

    sections.push({
      heading: currentHeading,
      headingLevel: currentLevel,
      text,
      sectionIndex: sections.length
    });
    currentLines = [];
  };

  lines.forEach((line) => {
    const match = !inFence ? line.match(headingPattern) : null;
    if (match) {
      hasHeading = true;
      flush();
      currentHeading = match[2].trim();
      currentLevel = match[1].length;
      currentLines = [line];
      return;
    }

    currentLines.push(line);
    if (startsFence(line)) {
      inFence = !inFence;
    }
  });

  flush();
  return { hasHeading, sections };
};

const splitMarkdownBlocks = (text: string): Array<{ text: string; isCode: boolean }> => {
  const blocks: Array<{ text: string; isCode: boolean }> = [];
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  let current: string[] = [];
  let inFence = false;
  let currentIsCode = false;

  const flush = () => {
    const blockText = current.join('\n').trim();
    if (blockText) {
      blocks.push({ text: blockText, isCode: currentIsCode });
    }
    current = [];
    currentIsCode = false;
  };

  lines.forEach((line) => {
    if (startsFence(line)) {
      if (!inFence && current.length > 0) {
        flush();
      }
      inFence = !inFence;
      currentIsCode = true;
      current.push(line);
      if (!inFence) {
        flush();
      }
      return;
    }

    if (inFence) {
      current.push(line);
      return;
    }

    if (line.trim() === '') {
      current.push(line);
      flush();
      return;
    }

    current.push(line);
  });

  flush();
  return blocks;
};

const splitLongPlainText = (text: string) => {
  const parts: string[] = [];
  let start = 0;
  while (start < text.length) {
    const end = Math.min(text.length, start + maxChunkChars);
    parts.push(text.slice(start, end));
    if (end >= text.length) {
      break;
    }
    start = Math.max(end - overlapChars, start + 1);
  }
  return parts;
};

const overlapTail = (text: string) => {
  if (text.includes('```') || text.includes('~~~')) {
    return '';
  }
  return text.slice(Math.max(0, text.length - overlapChars)).trim();
};

const splitLongMarkdown = (text: string) => {
  if (text.length <= maxChunkChars) {
    return [text];
  }

  const chunks: string[] = [];
  let current = '';
  splitMarkdownBlocks(text).forEach((block) => {
    if (!block.isCode && block.text.length > maxChunkChars) {
      if (current.trim()) {
        chunks.push(current.trim());
        current = overlapTail(current);
      }
      splitLongPlainText(block.text).forEach((part) => chunks.push(part.trim()));
      current = '';
      return;
    }

    const next = current ? `${current}\n\n${block.text}` : block.text;
    if (current && next.length > maxChunkChars) {
      chunks.push(current.trim());
      const tail = overlapTail(current);
      current = tail ? `${tail}\n\n${block.text}` : block.text;
      return;
    }

    current = next;
  });

  if (current.trim()) {
    chunks.push(current.trim());
  }
  return chunks;
};

export class MarkdownChunker implements Chunker {
  chunk(input: ChunkInput): ChunkOutput[] {
    const content = input.content?.trim() || (input.title ? `# ${input.title}` : '');
    if (!content.trim()) {
      return [];
    }

    const { hasHeading, sections } = splitIntoSections(content);
    const sourceSections = hasHeading
      ? sections
      : [
          {
            heading: null,
            headingLevel: null,
            text: content,
            sectionIndex: 0
          }
        ];
    const chunks: ChunkOutput[] = [];

    sourceSections.forEach((section) => {
      splitLongMarkdown(section.text).forEach((chunkText, partIndex) => {
        const chunk = makeChunk(input, {
          sourceTable: 'nodes',
          chunkType: hasHeading ? 'markdown_section' : 'markdown_text',
          chunkText,
          chunkIndex: chunks.length,
          metadata: {
            heading: section.heading,
            heading_level: section.headingLevel,
            section_index: section.sectionIndex,
            section_part: partIndex
          }
        });
        if (chunk) {
          chunks.push(chunk);
        }
      });
    });

    return chunks;
  }
}
