from app.search import MAX_LIMIT, get_search


def search_files(query: str, limit: int = 5) -> str:
    """Agent tool: BM25 full-text search over the data folder."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must be a non-empty string.")
    try:
        limit = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 5

    total, hits = get_search().search(query.strip(), limit=limit)
    if not hits:
        return f"No files match '{query.strip()}'."

    lines = [f"Found {total} matching file(s) for '{query.strip()}':"]
    for rank, hit in enumerate(hits, start=1):
        snippet = hit.snippet.replace("<mark>", "**").replace("</mark>", "**")
        lines.append(f"{rank}. {hit.path} (score {hit.score:.2f}): {snippet}")
    return "\n".join(lines)
