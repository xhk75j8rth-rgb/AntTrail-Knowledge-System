import { Search, X } from 'lucide-react';
import type { SearchResult } from '../api/lucasDbClient';

interface SearchBoxProps {
  query: string;
  results: SearchResult[];
  loading: boolean;
  onQueryChange: (query: string) => void;
  onSelect: (nodeId: string) => void;
  onClear: () => void;
}

export function SearchBox({
  query,
  results,
  loading,
  onQueryChange,
  onSelect,
  onClear
}: SearchBoxProps) {
  return (
    <div className="search-box">
      <div className="search-input-wrap">
        <Search size={16} />
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索标题和内容"
        />
        {query ? (
          <button className="icon-button small" type="button" title="清空搜索" onClick={onClear}>
            <X size={14} />
          </button>
        ) : null}
      </div>
      {query ? (
        <div className="search-results">
          {loading ? <div className="search-state">搜索中...</div> : null}
          {!loading && results.length === 0 ? <div className="search-state">没有结果</div> : null}
          {!loading
            ? results.map((result) => (
                <button
                  key={result.id}
                  className="search-result"
                  type="button"
                  onClick={() => onSelect(result.id)}
                >
                  <strong>{result.title}</strong>
                  <span>{result.path}</span>
                  {result.snippet ? <small>{result.snippet}</small> : null}
                </button>
              ))
            : null}
        </div>
      ) : null}
    </div>
  );
}
