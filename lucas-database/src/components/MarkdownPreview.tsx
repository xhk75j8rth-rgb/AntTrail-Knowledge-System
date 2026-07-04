import type { ReactNode } from 'react';
import { toAbsoluteApiUrl } from '../api/lucasDbClient';

type MarkdownBlock =
  | { type: 'heading'; level: number; text: string; key: string }
  | { type: 'image'; alt: string; src: string; key: string }
  | { type: 'paragraph'; text: string; key: string }
  | { type: 'quote'; lines: string[]; key: string }
  | { type: 'list'; items: string[]; key: string };

interface MarkdownPreviewProps {
  markdown: string;
}

const parseInline = (text: string): ReactNode[] => {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text))) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const value = match[0];
    if (value.startsWith('`')) {
      parts.push(<code key={`${match.index}-code`}>{value.slice(1, -1)}</code>);
    } else {
      parts.push(<strong key={`${match.index}-strong`}>{value.slice(2, -2)}</strong>);
    }
    lastIndex = match.index + value.length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
};

const parseImage = (text: string) => {
  const image = /^!\[([^\]]*)\]\((<)?([^>\)\n]+)(>)?\)$/.exec(text.trim());
  if (!image) {
    return null;
  }

  return {
    alt: image[1],
    src: image[3].trim()
  };
};

const isLocalFilePath = (src: string) =>
  /^[a-zA-Z]:[\\/]/.test(src) || /^file:\/\//i.test(src) || /^\\\\/.test(src);

const parseMarkdown = (markdown: string): MarkdownBlock[] => {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  const nextKey = (type: string) => `${type}-${blocks.length}`;

  while (index < lines.length) {
    const rawLine = lines[index];
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
    if (heading) {
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        text: heading[2].trim(),
        key: nextKey('heading')
      });
      index += 1;
      continue;
    }

    const image = parseImage(trimmed);
    if (image) {
      blocks.push({
        type: 'image',
        alt: image.alt,
        src: image.src,
        key: nextKey('image')
      });
      index += 1;
      continue;
    }

    if (trimmed.startsWith('>')) {
      const quoteLines: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ''));
        index += 1;
      }
      blocks.push({ type: 'quote', lines: quoteLines, key: nextKey('quote') });
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ''));
        index += 1;
      }
      blocks.push({ type: 'list', items, key: nextKey('list') });
      continue;
    }

    const paragraphLines: string[] = [trimmed];
    index += 1;
    while (index < lines.length) {
      const nextLine = lines[index].trim();
      if (
        !nextLine ||
        /^(#{1,6})\s+/.test(nextLine) ||
        parseImage(nextLine) ||
        nextLine.startsWith('>') ||
        /^[-*]\s+/.test(nextLine)
      ) {
        break;
      }
      paragraphLines.push(nextLine);
      index += 1;
    }
    blocks.push({
      type: 'paragraph',
      text: paragraphLines.join('\n'),
      key: nextKey('paragraph')
    });
  }

  return blocks;
};

export function MarkdownPreview({ markdown }: MarkdownPreviewProps) {
  return (
    <article className="markdown-preview">
      {parseMarkdown(markdown).map((block) => {
        if (block.type === 'heading') {
          const HeadingTag = `h${block.level}` as keyof JSX.IntrinsicElements;
          return <HeadingTag key={block.key}>{parseInline(block.text)}</HeadingTag>;
        }

        if (block.type === 'quote') {
          return (
            <blockquote key={block.key}>
              {block.lines.map((line, index) => (
                <p key={`${block.key}-${index}`}>{parseInline(line)}</p>
              ))}
            </blockquote>
          );
        }

        if (block.type === 'image') {
          if (isLocalFilePath(block.src)) {
            return <p key={block.key}>{parseInline(`![${block.alt}](${block.src})`)}</p>;
          }

          return (
            <figure className="markdown-image" key={block.key}>
              <img src={toAbsoluteApiUrl(block.src)} alt={block.alt} />
              {block.alt ? <figcaption>{block.alt}</figcaption> : null}
            </figure>
          );
        }

        if (block.type === 'list') {
          return (
            <ul key={block.key}>
              {block.items.map((item, index) => (
                <li key={`${block.key}-${index}`}>{parseInline(item)}</li>
              ))}
            </ul>
          );
        }

        return (
          <p key={block.key}>
            {block.text.split('\n').map((line, index) => (
              <span key={`${block.key}-${index}`}>
                {index > 0 ? <br /> : null}
                {parseInline(line)}
              </span>
            ))}
          </p>
        );
      })}
    </article>
  );
}
